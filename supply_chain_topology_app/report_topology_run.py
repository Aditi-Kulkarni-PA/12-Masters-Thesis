"""Rebuild a run's full report from the run store alone — no log files, no console scrollback.

    uv run python supply_chain_topology_app/report_topology_run.py <run_id|latest>
    uv run python supply_chain_topology_app/report_topology_run.py --list

If a figure cannot be reproduced from the database, that is a gap in the schema, not a
reason to consult a log: every number the thesis reports has to survive in the store,
because logs get rotated, overwritten, and lost long before the write-up happens.
"""
import json
import sqlite3
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from measurement.run_store_schema import DB_PATH
from measurement.dependencies import (concurrency_report, missed_concurrency,
                               render_timeline, check_dependencies)


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def list_runs(limit=20):
    with _conn() as c:
        rows = c.execute(
            """SELECT r.run_id, r.topology, r.model, r.run_n, r.started_at, r.run_status,
                      r.grand_total_cost_usd, r.wall_time_s, r.lock_rows, q.judge_mean
               FROM run r LEFT JOIN quality_scores q ON q.run_id = r.run_id
               ORDER BY r.started_at DESC LIMIT ?""", (limit,)).fetchall()
    # Cost, latency AND quality on one line: two of the three invite the wrong
    # conclusion on their own. A lock marker so protected runs are obvious.
    print(f"{'run_id':10} {'topology':18} {'model':16} {'n':>2} {'status':9} "
          f"{'cost':>8} {'wall':>7} {'qual':>6}  started")
    for r in rows:
        q = f"{r['judge_mean']:.2f}" if r['judge_mean'] is not None else "  -"
        lock = "*" if r['lock_rows'] else " "
        print(f"{r['run_id'][:8]:9}{lock} {r['topology']:18} {r['model']:16} {r['run_n']:>2} "
              f"{str(r['run_status']):9} ${r['grand_total_cost_usd'] or 0:>7.4f} "
              f"{r['wall_time_s'] or 0:>6.1f}s {q:>6}  {r['started_at'][:19]}")


def report(run_id):
    with _conn() as c:
        if run_id == "latest":
            row = c.execute("SELECT * FROM run ORDER BY started_at DESC LIMIT 1").fetchone()
        else:
            row = c.execute("SELECT * FROM run WHERE run_id LIKE ?", (run_id + "%",)).fetchone()
        if row is None:
            print(f"No run matching {run_id!r}. Use --list.")
            return 1
        rid = row["run_id"]
        calls = [dict(r) for r in c.execute(
            "SELECT * FROM tool_call WHERE run_id=? ORDER BY call_order", (rid,))]
        qmeta = c.execute("SELECT * FROM query_metadata WHERE query_id=?",
                          (row["query_id"],)).fetchone()
        qual = c.execute("SELECT * FROM quality_scores WHERE run_id=?", (rid,)).fetchone()

    bar = "=" * 62
    print(f"\n{bar}\nRUN REPORT  {rid}\n{bar}")
    print(f"  topology        : {row['topology']}")
    print(f"  model           : {row['model']}")
    print(f"  run_n           : {row['run_n']}")
    print(f"  started         : {row['started_at']}")
    print(f"  wall time       : {row['wall_time_s']}s")
    print(f"  config hash     : {row['config_hash']}")

    env = json.loads(row["env_config_json"] or "{}")
    if env:
        nc = env.get("SC_NO_CACHE")
        valid = str(nc).strip().lower() in ("1", "true", "yes")
        print(f"  measurement mode: SC_NO_CACHE={nc} "
              f"{'(valid)' if valid else '(NOT a measurement run — caching was ON)'}")
        print(f"  env config      : " + "  ".join(f"{k}={v}" for k, v in env.items()
                                                  if k != "SC_NO_CACHE"))
    else:
        print("  measurement mode: (not recorded — run predates env_config_json)")

    tt = json.loads(row["turn_times_json"] or "[]")
    if tt:
        print(f"  turn times      : " + "  ".join(f"turn{i+1}={t}s" for i, t in enumerate(tt)))

    print(f"\n  query_id        : {row['query_id']}")
    if qmeta:
        print(f"  query text      : {qmeta['query_text']}")
        print(f"  complexity      : {qmeta['complexity_tier']}   "
              f"set: {qmeta['query_set_version']}   held_out: {qmeta['held_out']}")
        implied = json.loads(qmeta["implied_tools_json"] or "[]")
        print(f"  implied tools   : {len(implied)} -> {', '.join(implied)}")
    else:
        print("  query text      : (no query_metadata row)")
    if row["query_text_actual"]:
        print("  query AS SENT   :")
        for line in row["query_text_actual"].splitlines():
            print(f"      {line}" if line.strip() else "")

    pv = json.loads(row["prompt_versions_json"] or "{}")
    print("\n  prompt versions :")
    for k, v in pv.items():
        print(f"      {k:44} {v}")

    # ---- tools ----------------------------------------------------------
    print(f"\n{bar}\nTOOL CALLS\n{bar}")
    hdr = (f"  {'tool':30} {'dur':>7} {'in':>6} {'out':>7} {'ptok':>7} {'ctok':>6} "
           f"{'cost':>8} {'json':>5} {'empty':>6}")
    print(hdr)
    for t in calls:
        print(f"  {t['tool_name']:30} {t['duration_s'] or 0:>6.2f}s {t['input_chars'] or 0:>6} "
              f"{t['output_chars'] or 0:>7} {t['prompt_tokens'] or 0:>7} "
              f"{t['completion_tokens'] or 0:>6} ${t['cost_usd'] or 0:>7.4f} "
              f"{'yes' if t['valid_json'] else 'NO':>5} {'YES' if t['empty_payload'] else '-':>6}")
    sub_p = sum(t['prompt_tokens'] or 0 for t in calls)
    sub_c = sum(t['completion_tokens'] or 0 for t in calls)
    sub_cost = sum(t['cost_usd'] or 0 for t in calls)
    print(f"  {'— sub-agents TOTAL':30} {'':>7} {'':>6} {'':>7} {sub_p:>7} {sub_c:>6} ${sub_cost:>7.4f}")
    print(f"  {'— master':30} {'':>7} {'':>6} {'':>7} {row['master_prompt_tokens'] or 0:>7} "
          f"{row['master_completion_tokens'] or 0:>6} ${row['master_cost_usd'] or 0:>7.4f}")
    print(f"  {'— GRAND TOTAL':30} {'':>7} {'':>6} {'':>7} "
          f"{'':>7} {row['grand_total_tokens'] or 0:>6} ${row['grand_total_cost_usd'] or 0:>7.4f}")

    # ---- validity -------------------------------------------------------
    print(f"\n{bar}\nRUN VALIDITY\n{bar}")
    plan = row["plan_presented"]
    print(f"  run status         : {row['run_status']}"
          + (f"   ({row['failure_category']})" if row["failure_category"] else ""))
    print(f"  plan presented     : {{1:'yes',0:'NO'}}.get(plan,'n/a')".format() if False else
          f"  plan presented     : {'yes' if plan == 1 else 'NO' if plan == 0 else 'n/a (structural)'}")
    print(f"  input path source  : "
          f"{'DERIVED from query (good)' if not row['path_fallback_used'] else 'DEV FALLBACK USED'}")
    print(f"  tools expected/ran : {row['tool_call_count_expected']} / {row['tool_call_count_actual']}")
    print(f"  missing tools      : {row['missing_expected_tools_json'] or '[]'}")
    empties = [t['tool_name'] for t in calls if t['empty_payload']]
    print(f"  empty results      : {empties or 'none'}")
    # FABRICATED line removed 23-Aug-26 (user) -- see the note in execute_topology.py's
    # run-validity block. It could not distinguish an invented narrative from the honest
    # "this capability produced nothing" report that exception_handling.md requires, and
    # the "empty results" line above already shows the underlying condition. Still
    # detected and persisted to run.fabricated_narrative_json for later analysis.

    viol = json.loads(row["dependency_violations_json"] or "[]")
    # Recompute from stored offsets as a cross-check that the stored verdict still holds.
    # Anchored the same way the timeline below is (tool_base_offset_s) so a recomputed
    # detail string reads in the same frame as everything else in this report -- see
    # dependencies.py's check_dependencies() docstring (6-Sep-26 fix).
    recomputed = check_dependencies(calls, base_offset_s=row["tool_base_offset_s"] or 0.0)
    # See execute_topology.py's identical check (6-Sep-26 fix): an empty violations list
    # is ambiguous between "genuinely respected" and "nothing ran to violate anything".
    if viol or recomputed:
        print(f"  dependency order   : {len(viol)} VIOLATION(S)")
        for v in (viol or recomputed):
            print(f"    ! {v['detail']}")
    elif not row["tool_call_count_actual"]:
        print("  dependency order   : N/A — no tool calls were made (see tools expected/ran above)")
    else:
        print("  dependency order   : respected — every capability waited for its inputs")
    if len(viol) != len(recomputed):
        print(f"    (note: stored {len(viol)}, recomputed {len(recomputed)} — schema drift?)")

    conc = concurrency_report(calls)
    missed = missed_concurrency(calls)
    if conc and conc.get("exploited") is not None:
        busy = conc["actual_span_s"] - conc.get("coordinator_idle_s", 0.0)
        sched = (conc["critical_path_s"] / busy) if busy else None
        if sched is not None:
            # >100% is not a better-than-perfect schedule -- it is arithmetically
            # unreachable while respecting the dependency graph, so it is a SYMPTOM of
            # having violated it. critical_path_s sums dependent durations end-to-end
            # (predict THEN diagnose), while busy measures merged wall-clock intervals;
            # running a dependent pair concurrently collapses two durations into one
            # interval, pushing busy below the floor the graph implies. Observed
            # 23-Aug-26 run 9b1f4e59: "101% of achievable overlap" printed alongside a
            # predict/diagnose violation, reading as a perfect score.
            if sched > 1.0:
                print(f"  scheduling         : n/a -- busy time ({busy:.2f}s) came in UNDER the "
                      f"dependency-respecting floor ({conc['critical_path_s']:.2f}s),")
                print(f"                       which is only possible by running a dependent pair "
                      f"concurrently. See the violation(s) above.")
            else:
                print(f"  scheduling         : {sched:.0%} of achievable overlap  "
                      f"({len(missed)} pair(s) left unoverlapped)")
        print(f"  coordinator idle   : {conc.get('coordinator_idle_s', 0.0)}s between waves")
        print(f"  span               : {conc['actual_span_s']}s vs critical path "
              f"{conc['critical_path_s']}s")
        # Quality belongs next to cost and latency: reported alone, "cheaper and faster"
        # cannot be distinguished from "did less work".
        print()
        if qual is None:
            print("  quality           : NOT SCORED — run "
                  "`score_topology_run.py " + rid[:8] + "`")
        else:
            print(f"  quality (judge)   : weighted mean {qual['judge_mean']:.2f}/5   "
                  f"relevance {qual['judge_relevance']:.2f} x.45  "
                  f"faithfulness {qual['judge_faithfulness']:.2f} x.45  "
                  f"safety {qual['judge_safety']:.2f} x.10   [{qual['judge_model']}]")
        print("\n  execution timeline:")
        for line in render_timeline(calls, indent="    ",
                                    base_offset_s=row["tool_base_offset_s"],
                                    turn_times=json.loads(row["turn_times_json"] or "[]")):
            print(line)
        for m in missed:
            print(f"    ~ {m['detail']}")

    if row["turn1_plan_text"]:
        print("\n  turn-1 plan text:")
        for line in row["turn1_plan_text"].splitlines():
            print(f"    | {line}")
    else:
        print("\n  turn-1 plan text   : (none — no plan produced)")

    if row["final_answer"]:
        print("\n  final answer (MasterOutput):")
        try:
            for k, v in json.loads(row["final_answer"]).items():
                if str(v).strip():
                    print(f"    {k}: {str(v)[:160]}{'...' if len(str(v)) > 160 else ''}")
        except Exception:
            print(f"    {row['final_answer'][:300]}")
    print()
    return 0


if __name__ == "__main__":
    if "--list" in sys.argv:
        list_runs()
    else:
        sys.exit(report(sys.argv[1] if len(sys.argv) > 1 else "latest"))
