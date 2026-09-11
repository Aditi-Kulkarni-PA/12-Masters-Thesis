"""
Run-record writer (T25) — persists a completed RunRecorder into run_store.db.
One call = one `run` row + N `tool_call` rows.

Usage (from execute_topology.py or any topology's run script):
    from measurement.run_store_writer import write_run

    run_id = write_run(
        recorder=recorder, responses=responses, query_id="Q1",
        topology="planner_executor", model=MODEL,          # run_n auto-computed
        wall_time_s=run_time, final_answer=responses[-1].text,
        plan_presented=True,
    )
"""
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, timezone, timedelta

from measurement.instrumentation import extract_usage, sum_usage, CAPTURE_VERSION
from measurement.pricing import estimate_cost, total_sub_agent_cost
from measurement.run_store_schema import DB_PATH, migrate_schema
from measurement.dependencies import check_dependencies, canonical, scheduling_values
from core.paths import PIPELINE_DIR

_FALLBACK_MARKER = PIPELINE_DIR / "data" / ".dev_path_fallback_fired.json"

def _turn_answer_text(r) -> str:
    """One turn's answer, matching execute_topology.py's _final_answer_text convention:
    structured output (.value) takes priority over raw .text, since MAF leaves .text
    blank when a response_format schema is used. Duplicated rather than imported --
    write_run() takes raw `responses` objects from any topology's entry point, so it
    should not depend on one entry point's private helper."""
    if r.value is not None and hasattr(r.value, "model_dump_json"):
        return r.value.model_dump_json()
    return r.text or ""


def _config_hash(model: str, prompt_versions: dict) -> str:
    """Stable hash over the config that could affect results, for grouping runs."""
    payload = json.dumps({"model": model, "prompts": prompt_versions}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]

def _get_implied_tools(conn: sqlite3.Connection, query_id: str):
    row = conn.execute(
        "SELECT implied_tools_json FROM query_metadata WHERE query_id = ?", (query_id,)
    ).fetchone()
    if row is None or row[0] is None:
        return None  # query_metadata not populated yet (T16 not done) -- unprompted stays unknown
    return set(json.loads(row[0]))

# Each tool's primary payload key(s), and the MasterOutput narrative field (if any)
# whose content is supposed to be grounded in that payload.
#
# predict and diagnose have no entry in the narrative column because they write their
# own summary INSIDE their payload (predict_summary / diagnosis_summary); the other
# three have no free-text field in their schema, so the coordinator writes it.
_PAYLOAD_KEYS: dict[str, tuple[tuple[str, ...], str | None]] = {
    "predict_delivery_delays_tool": (("delayed_orders",), None),
    "diagnose_delay_patterns_tool": (("high_risk_patterns", "comparison"), None),
    "delay_simulations_tool":       (("simulations",), "simulate_summary"),
    "recommendation_tool":          (("recommended_actions",), "recommendation_summary"),
    "email_alert_tool":             (("content",), "email_alert_summary"),
}


def _is_empty_payload(tool_name: str, payload: str) -> bool:
    """True if the tool returned valid JSON whose primary list(s) were all empty.

    An empty list is the documented response when a tool errors or finds nothing
    (see each agent prompt's Error handling section), so it is valid JSON and raises
    no exception -- which meant a tool that produced no data at all was recorded with
    error=None and run_status=success. Observed 22-Aug-26: recommendation_tool hit a
    missing daily CSV, returned {"recommended_actions": []} (26 chars), and the run
    was scored a clean success while the coordinator narrated recommendations that had
    no data behind them.
    """
    spec = _PAYLOAD_KEYS.get(tool_name)
    if not spec or not payload:
        return False
    keys, _ = spec
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    present = [k for k in keys if k in data]
    if not present:
        return False
    return all(not data.get(k) for k in present)


# Phrases that mark a narrative as REPORTING a capability's failure rather than
# inventing results for it. exception_handling.md REQUIRES the coordinator to say so
# when a capability returned nothing, so the correct behaviour necessarily produces a
# non-empty narrative field -- which the bare "field non-empty while tool returned
# nothing" test scores as fabrication. Observed 23-Aug-26 run f6a7bdd8: the coordinator
# wrote "Recommendations could not be completed because the recommendation tool returned
# no recommended actions", was flagged FABRICATED NARRATIVE, and had in fact done exactly
# what it was told to do. Heuristic by nature, but it only ever SUPPRESSES a flag, so a
# miss costs a false alarm rather than a missed fabrication.
_FAILURE_REPORT_PHRASES = (
    "could not", "cannot be", "can not be", "unable to", "was not able", "did not return",
    "returned no", "no recommend", "no actions", "no results", "no data", "no output",
    "not available", "unavailable", "not provided", "failed", "empty", "none were",
)


def _reports_failure(text: str) -> bool:
    """True if this narrative appears to REPORT that its capability produced nothing."""
    lowered = text.lower()
    return any(p in lowered for p in _FAILURE_REPORT_PHRASES)


def _detect_fabricated_narrative(empty_tools: set[str], final_answer: str) -> list[str]:
    """MasterOutput narrative fields written despite their backing tool returning nothing.

    This is the measurable form of "did the coordinator invent content it never
    received" -- directly relevant to faithfulness scoring, since a fabricated summary
    reads as legitimate output to RAGAS.

    Still computed and persisted to run.fabricated_narrative_json for later analysis, but
    NO LONGER DISPLAYED in either run-validity report (removed 23-Aug-26, user) -- the
    console verdict could not distinguish invention from the honest "this capability
    produced nothing" report that exception_handling.md requires, and the "empty results"
    line already surfaces the underlying condition without asserting intent.

    A narrative that openly states the capability produced nothing is excluded here too,
    so the stored column means "invented content" rather than merely "field was non-empty".
    See _FAILURE_REPORT_PHRASES.
    """
    if not empty_tools or not final_answer:
        return []
    try:
        out = json.loads(final_answer)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(out, dict):
        return []
    fabricated = []
    for tool in empty_tools:
        field_name = _PAYLOAD_KEYS.get(tool, ((), None))[1]
        if not field_name:
            continue
        text = str(out.get(field_name) or "").strip()
        if text and not _reports_failure(text):
            fabricated.append(field_name)
    return sorted(fabricated)


def _consume_path_fallback_marker(run_started_at: datetime) -> bool:
    """True if the dev-path-fallback fired during this run's window. Always
    clears the marker on the way out -- a stale marker must never leak into
    a later run's result, the same class of bug as the stale-sidecar issue
    we fixed earlier this session."""
    if not _FALLBACK_MARKER.is_file():
        return False
    try:
        data = json.loads(_FALLBACK_MARKER.read_text())
        fired_at = datetime.fromisoformat(data["fired_at"])
    except Exception:
        fired_at = None
    fired_during_this_run = fired_at is not None and fired_at >= run_started_at
    _FALLBACK_MARKER.unlink(missing_ok=True)
    return fired_during_this_run

def write_failed_run(
    *,
    query_id: str,
    topology: str,
    run_n: int,
    model: str,
    failure_category: str,
    log_path: str | None = None,
    run_phase: str | None = None,
    experiment_no: int | None = None,
    batch_id: str | None = None,
    execution_order: int | None = None,
    db_path: str = DB_PATH,
) -> str:
    """Record a run that never completed. Returns the new run_id.

    A run killed by an error or a timeout never reaches write_run(), so before this
    existed it left no trace in the store at all. Every rate computed from the store
    then used a denominator that silently excluded it, and a topology that failed
    outright scored better than one that completed poorly. Dynamic Graph is the case
    that exposed this: it produced no result for the highest-complexity queries, and
    its completion rate still read as 1.00 because the missing runs were invisible.

    The row carries only what the harness knows: which combination was attempted, that
    it did not complete, and where its log is. No cost, token or quality figures are
    written, because none were produced. They stay NULL rather than 0, so a failed run
    never contributes a false zero to a cost or quality average.

    config_hash holds a sentinel rather than a real hash. The run never established a
    configuration, and a sentinel that cannot be mistaken for a hash is more honest
    than a fabricated one.

    Re-running the same combination replaces the failed row rather than adding a second
    one, so a batch retried after a fix does not accumulate duplicates.
    """
    run_id = str(uuid.uuid4())
    migrate_schema(db_path)

    conn = sqlite3.connect(db_path)
    try:
        # Clear any earlier failed attempt at the same slot. Completed runs are left
        # untouched: only a row that itself records a non-completion is replaced.
        #
        # run_phase is part of the slot alongside model. Without it a main-experiment
        # retry would match, and delete, a pilot row for the same topology, query, run_n
        # and tier -- the pilot is N=1 at every tier, so main N=1 collides with it exactly.
        conn.execute(
            "DELETE FROM run WHERE topology = ? AND query_id = ? AND run_n = ? "
            "AND model = ? AND run_phase IS ? AND run_status = 'failed'",
            (topology, query_id, run_n, model, run_phase),
        )
        # started_at is left NULL, not stamped with the current time. This row records a
        # run that did not complete, and for one that never reached the model there is no
        # start to record. Writing the write time into started_at made eight such rows
        # share a timestamp to the millisecond across two models and two batches, which
        # read as a single harness abort and caused a genuine finding to be misdiagnosed
        # and excluded (Risk Log R71, R72). The write time goes to recorded_at instead, so
        # the audit trail is kept without inventing a start time.
        #
        # experiment_no is written here as well as on the completed path. A failed run
        # that carried no experiment would drop out of its campaign's denominator, which
        # is the exact failure this function was added to prevent.
        conn.execute(
            "INSERT INTO run (run_id, query_id, topology, run_n, model, config_hash, "
            "started_at, recorded_at, run_status, failure_category, log_path, run_phase, "
            "experiment_no, batch_id, execution_order, capture_version, path_fallback_used) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 'failed', ?, ?, ?, ?, ?, ?, ?, 0)",
            (run_id, query_id, topology, run_n, model,
             "unavailable:run_did_not_complete",
             datetime.now(timezone.utc).isoformat(),
             failure_category, log_path, run_phase, experiment_no, batch_id, execution_order,
             CAPTURE_VERSION),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def write_run(
    *,
    recorder,
    responses: list,
    query_id: str,
    topology: str,
    run_n: int | None = None,
    model: str,
    wall_time_s: float,
    final_answer: str,
    plan_presented: bool | None = None,
    turn1_plan_text: str | None = None,
    query_text_actual: str | None = None,
    env_config: dict | None = None,
    turn_times: list | None = None,
    payload_policy: str | None = None,
    prompt_versions: dict | None = None,
    log_path: str | None = None,
    run_t0: float | None = None,
    run_phase: str | None = None,
    experiment_no: int | None = None,
    batch_id: str | None = None,
    execution_order: int | None = None,
    db_path: str = DB_PATH,
) -> str:
    """Persist one completed run + its tool calls. Returns the new run_id.

    *turn1_plan_text* (T94) stores the turn-1 chat_response verbatim. The boolean
    plan_presented only records THAT a plan appeared; comparing planning quality
    across topologies needs the text itself, and it cannot be reconstructed later.

    *run_n* (T113, 30-Aug-26): repetition index for this (topology, query_id) pair.
    Leave None (the normal case) and this function computes it as
    MAX(run_n)+1 over existing rows for the same (topology, query_id) -- every call
    site previously hardcoded run_n=1 regardless of how many times that combination
    had already run, which made rows indistinguishable for the N>=3/N>=5 repetition
    figures the design spec calls for. Pass an explicit int only for a deliberate
    backfill/repair script that must target a specific slot.

    *run_phase* (T113, 30-Aug-26): "dev" / "pilot" by convention, not enforced here.
    None means unknown, not "assume dev" -- see run_store_schema.py's migration
    comment for why that matters.

    *batch_id* / *execution_order* (5-Sep-26): identify which harness invocation
    produced this run, and its position within that invocation's (usually randomized)
    run order. Both None for a run not launched through execute_experiment.py.
    """
    run_id = str(uuid.uuid4())
    # Bring an older database up to date before inserting; no-op once applied.
    migrate_schema(db_path)
    prompt_versions = prompt_versions or {}
    config_hash = _config_hash(model, prompt_versions)

    conn = sqlite3.connect(db_path)
    try:
        if run_n is None:
            # Scoped to this experiment's (run_phase, model), not to the pair alone.
            # Counting every row for a topology and query would carry the pilot's
            # repetitions into the main experiment and start it at run_n = 2.
            row = conn.execute(
                "SELECT COALESCE(MAX(run_n), 0) FROM run WHERE topology = ? AND query_id = ? "
                "AND model = ? AND run_phase IS ?",
                (topology, query_id, model, run_phase),
            ).fetchone()
            run_n = row[0] + 1

        # Clear an earlier non-completion at this same slot, matching what
        # write_failed_run() already does when a failed run is re-recorded. Without this
        # a retry after a timeout left both rows in the store: the original 'failed' row
        # and the new completed one, so the slot counted twice and the tier's run total
        # exceeded the grid. Only rows that record a non-completion are removed; a
        # completed run is never replaced by another completed run, so a genuine
        # repetition at the same run_n still has to be deliberate. run_phase is part of
        # the slot for the same reason as in write_failed_run(): main N=1 and pilot N=1
        # are identical on every other column.
        conn.execute(
            "DELETE FROM run WHERE topology = ? AND query_id = ? AND run_n = ? "
            "AND model = ? AND run_phase IS ? AND run_status = 'failed'",
            (topology, query_id, run_n, model, run_phase),
        )

        run_finished_at = datetime.now(timezone.utc)
        run_started_at = run_finished_at - timedelta(seconds=wall_time_s)  # approximation, fine for this check
        path_fallback_used = _consume_path_fallback_marker(run_started_at)        
        implied_tools = _get_implied_tools(conn, query_id)

        master_usage = sum_usage(*(extract_usage(r) for r in responses))
        sub_totals = recorder.total_sub_agent_usage()
        grand_totals = sum_usage(master_usage, sub_totals)
        master_cost = estimate_cost(model, master_usage)
        sub_cost = total_sub_agent_cost(model, recorder.sub_agent_usage)
        grand_cost = round((master_cost or 0) + sub_cost, 6)

        # Coordinator turns a topology drove itself: recorded in sub_agent_usage but
        # matching no tool call, so they would otherwise be indistinguishable from
        # specialist cost. Persisted separately -- grand totals above are unaffected.
        _, orch_usage = recorder.split_usage()
        orch_cost = estimate_cost(model, orch_usage) if orch_usage["total_tokens"] else None

        tool_calls = recorder.summary()
        any_error = any(tc["error"] for tc in tool_calls)

        # A run that produced output but executed none of the capabilities the query
        # required is not a success, and was recorded as one until 7-Sep-26 because the
        # expression below only tested for tool errors: with no tool calls there are no
        # errors, so it fell through to "success". Twelve pilot runs were counted as
        # completed on that path, inflating completion rate by up to 6.7 points.
        #
        # Judged on capability calls rather than on the raw tool_call count: a narrative
        # continuation is not execution of a required capability. A query that requires
        # nothing (the out-of-scope probe) cannot reach this state, so a correct decline
        # is untouched.
        _required = _get_implied_tools(conn, query_id)
        _ran_capability = bool({canonical(t["tool_name"]) for t in tool_calls}
                               & {canonical(t) for t in _required})
        no_execution = bool(_required) and not _ran_capability

        if no_execution:
            run_status = "no_execution"
        elif any_error and len(tool_calls) <= 1:
            run_status = "failed"
        elif any_error:
            run_status = "partial"
        else:
            run_status = "success"

        # behaviour_class is deliberately not set here. Classifying what a run did
        # instead of executing is an LLM judgement, made in the scoring pipeline where
        # the quality rubric already runs, so the write path stays free of a model call
        # and a run cannot fail on a classifier error. The column stays NULL until
        # scoring fills it.
        failure_category = next((tc["error"] for tc in tool_calls if tc["error"]), None)
        plan_presented_flag = None if plan_presented is None else int(plan_presented)
        path_fallback_used_flag = int(path_fallback_used)
        # implied_tools holds canonical (wrapped) names. A condition that attaches raw
        # tools emits raw ones, so a literal comparison reports every tool as missing
        # even when all five ran -- observed on monolith run 671ea1c0: 5 ran, 5 "missing".
        # canonical() maps both spellings onto one identity.
        called_canonical = {canonical(t["tool_name"]) for t in tool_calls}
        implied_canonical = ({canonical(t) for t in implied_tools}
                             if implied_tools is not None else None)
        tool_call_count_expected = len(implied_tools) if implied_tools is not None else None
        tool_call_count_actual = len(tool_calls)
        called_tools = {tc["tool_name"] for tc in tool_calls}
        missing_expected = ((implied_canonical - called_canonical)
                            if implied_canonical is not None else None)

        # Tools that returned valid JSON but no data, and any narrative written over them.
        empty_by_index = [_is_empty_payload(tc["tool_name"], raw.payload)
                          for raw, tc in zip(recorder.tool_calls, tool_calls)]
        empty_tools = {tc["tool_name"] for tc, e in zip(tool_calls, empty_by_index) if e}
        fabricated = _detect_fabricated_narrative(empty_tools, final_answer)
        # Turn-1-relative anchor, computed here (not just below at its storage site) so
        # check_dependencies() can shift its violation "detail" text into the same frame
        # the execution timeline displays in -- see dependencies.py's docstring (6-Sep-26
        # fix). None when no tool ran at all; check_dependencies() defaults to 0.0 then.
        _tool_base_offset_s = (round(recorder._first_start() - run_t0, 3)
                               if run_t0 is not None and recorder.tool_calls else None)
        # T97: did each capability wait for what it consumes? Compares a dependent's
        # START against its prerequisite's END, so concurrent dispatch is judged
        # correctly rather than passing on start-order alone.
        dep_violations = check_dependencies(tool_calls, base_offset_s=_tool_base_offset_s or 0.0)
        # Scheduling and concurrency, computed here rather than left to a later backfill.
        # tool_calls already carries started_offset_s and ended_offset_s, and
        # scheduling_values() is the same function backfill_scheduling.py calls, so a run
        # written today and a run repaired later cannot produce different numbers.
        # Wrapped: these are derived measures, and failing to compute them must never cost
        # a run its record. On any error the columns stay NULL and the backfill repairs
        # them, which is exactly the state every run was in before this was wired up.
        try:
            _sched = scheduling_values(tool_calls) or {}
        except Exception as e:                      # noqa: BLE001 - derived measure only
            print(f"[run_store] scheduling measures not computed for this run ({e}); "
                  f"run backfill_scheduling.py to fill them in")
            _sched = {}
        # A tool the query never implied returning nothing is not evidence of a defect --
        # it is the expected, correct behaviour on a topology that runs every capability
        # regardless of query (Static-Graph DAG's documented design: e.g. a "predict,
        # diagnose, email" query gives simulate no scenario to work from, and reporting an
        # empty result rather than fabricating one is exactly what exception_handling.md
        # requires). Only a tool the query DID imply, still coming back empty, is the
        # original signal this check was built for -- 22-Aug-26, recommendation_tool
        # hitting a missing daily CSV and returning {"recommended_actions": []} while the
        # run scored a clean success. implied_tools is None for an ad-hoc/dev query with
        # no query_metadata row -- keep the old, conservative behaviour there rather than
        # guess. Confirmed 29-Aug-26 on Q6/static_graph_dag (run 940dd117): simulate was
        # not in Q6's implied_tools, ran anyway per design, and was flagged run_status=
        # partial for behaving correctly.
        # empty_tools may hold raw or wrapped names depending on the topology (same
        # reason called_canonical exists above) -- canonicalize before comparing against
        # implied_canonical, which is already canonical.
        concerning_empty = (empty_tools if implied_canonical is None
                            else {t for t in empty_tools if canonical(t) in implied_canonical})
        if concerning_empty and run_status == "success":
            run_status = "partial"
            failure_category = failure_category or f"empty_payload:{','.join(sorted(concerning_empty))}"

        conn.execute(
            """
            INSERT INTO run (
                run_id, query_id, topology, selected_topology, run_n, model, config_hash,
                prompt_versions_json, payload_policy, started_at, wall_time_s,
                master_prompt_tokens, master_completion_tokens, master_total_tokens, master_cost_usd,
                grand_total_tokens, grand_total_cost_usd, final_answer, run_status, failure_category,
                plan_presented, turn1_plan_text, path_fallback_used, tool_call_count_expected, tool_call_count_actual, missing_expected_tools_json,
                fabricated_narrative_json, dependency_violations_json,
                query_text_actual, env_config_json, turn_times_json, log_path,
                tool_base_offset_s, all_turns_json,
                orchestration_prompt_tokens, orchestration_completion_tokens,
                orchestration_total_tokens, orchestration_cost_usd,
                capture_version, run_phase, experiment_no, batch_id, execution_order, behaviour_class,
                scheduling_ratio, scheduling_deviation, infeasible_overlap,
                critical_path_s, actual_span_s, fully_serial_s, coordinator_idle_s
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, query_id, topology, None, run_n, model, config_hash,
                json.dumps(prompt_versions), payload_policy,
                datetime.now(timezone.utc).isoformat(), wall_time_s,
                master_usage["prompt_tokens"], master_usage["completion_tokens"], master_usage["total_tokens"],
                master_cost, grand_totals["total_tokens"], grand_cost, final_answer, run_status, failure_category,
                plan_presented_flag,
                (turn1_plan_text or None),
                path_fallback_used_flag,
                tool_call_count_expected,
                tool_call_count_actual,
                json.dumps(sorted(missing_expected)) if missing_expected is not None else None,
                json.dumps(fabricated) if fabricated else None,
                json.dumps(dep_violations) if dep_violations else None,
                query_text_actual,
                json.dumps(env_config) if env_config else None,
                json.dumps(turn_times) if turn_times else None,
                str(log_path) if log_path else None,
                _tool_base_offset_s,
                json.dumps([_turn_answer_text(r) for r in responses]),
                orch_usage["prompt_tokens"] or None,
                orch_usage["completion_tokens"] or None,
                orch_usage["total_tokens"] or None,
                orch_cost,
                CAPTURE_VERSION,
                run_phase,
                experiment_no,
                batch_id,
                execution_order,
                None,   # behaviour_class -- set by the scoring pipeline
                _sched.get("scheduling_ratio"),
                _sched.get("scheduling_deviation"),
                _sched.get("infeasible_overlap"),
                _sched.get("critical_path_s"),
                _sched.get("actual_span_s"),
                _sched.get("fully_serial_s"),
                _sched.get("coordinator_idle_s"),
            ),
        )

        # Pair each tool call with ITS OWN usage by tool_name, not by list position.
        #
        # recorder.tool_calls is in START order (middleware fires on entry);
        # recorder.sub_agent_usage is in COMPLETION order (record_sub_agent_usage fires
        # after agent.run() returns). Those coincide only while tools run serially.
        # Once the master issues several tool calls in one turn, they diverge and the
        # old positional zip credited each tool with another tool's tokens — observed
        # 22-Aug-26: recommendation_tool returned 26 chars of empty result yet was
        # recorded with 67,099 prompt tokens belonging to the simulation agent.
        # Run totals stayed correct (a sum is order-independent), so the corruption was
        # invisible in the summary line while every per-tool cost figure was wrong.
        #
        # A queue per tool name preserves order across repeat calls of the same tool
        # (a reflect-retry calls one tool twice), which a plain dict lookup would lose.
        # PREFERRED: correlate by call_id, set by the middleware and carried through
        # record_sub_agent_usage (T114 / Risk Log R50, 30-Aug-26). The name queue below
        # is correct only while, for any single tool name, starts and completions happen
        # in the same order -- which two CONCURRENT calls of the SAME tool break, each
        # then being credited with the other's tokens. Run totals stay right (a sum is
        # order-independent), so the error is invisible in every summary figure while
        # every per-call cost is wrong. No condition before Mesh B can invoke one
        # specialist twice concurrently; Mesh B can, and duplicate peer invocation is an
        # expected mesh cost it has to measure accurately.
        _usage_by_id: dict[int, dict] = {}
        _usage_by_name: dict[str, list[dict]] = {}
        for u in recorder.sub_agent_usage:
            cid = u.get("call_id")
            if cid is not None:
                _usage_by_id[cid] = u
            else:
                # No enclosing tool_call (e.g. Sequential's planning call, or usage
                # recorded by an older code path) -- keep the original behaviour.
                _usage_by_name.setdefault(u.get("tool_name"), []).append(u)

        _ZERO_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        for i, (tc_raw, tc) in enumerate(zip(recorder.tool_calls, tool_calls)):
            u = _usage_by_id.get(tc.get("call_id"))
            if u is None:
                queue = _usage_by_name.get(tc["tool_name"])
                u = queue.pop(0) if queue else _ZERO_USAGE
            cost = estimate_cost(model, u)
            unprompted = None
            if implied_tools is not None:
                unprompted = int(canonical(tc["tool_name"]) not in implied_canonical)
                
            conn.execute(
                """
                INSERT INTO tool_call (
                    tool_call_id, run_id, tool_name, call_order, duration_s, input_chars,
                    output_chars, output_text, valid_json, error, prompt_tokens, completion_tokens,
                    total_tokens, cost_usd, unprompted, empty_payload,
                    started_offset_s, ended_offset_s, retrieved_contexts_json, agent_messages
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(uuid.uuid4()), run_id, tc["tool_name"], i, tc["duration_s"],
                    tc["input_chars"], tc["payload_chars"], tc_raw.payload, int(tc["valid_json"]), tc["error"],
                    u["prompt_tokens"], u["completion_tokens"], u["total_tokens"], cost,
                    unprompted, int(empty_by_index[i]),
                    tc.get("started_offset_s"), tc.get("ended_offset_s"), None,
                    u.get("messages"),
                ),
            )

        conn.commit()
    finally:
        conn.close()

    return run_id

