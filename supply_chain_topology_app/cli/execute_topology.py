"""First end-to-end MAF run — headless, with live progress logging."""
import os

# Silence HuggingFace / tqdm progress bars BEFORE any import pulls in
# sentence_transformers — those libraries read these at import time, so setting
# them inside the loader function (as an earlier attempt did) is too late. The
# cross-encoder loads mid-run inside a tool call, and its bar interleaves with the
# run's own tool_start/tool_end lines.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TQDM_DISABLE", "1")

import asyncio, json, sys, time
from datetime import datetime
from pathlib import Path
import logging
import pandas as pd

# Ensure local package imports resolve when executing this file directly.
_APP_DIR = Path(__file__).resolve().parent.parent   # cli/ -> supply_chain_topology_app/
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

#from agent_framework import FunctionInvocationContext, function_middleware
from topologies.registry import resolve as _resolve_topology, VALID_TOPOLOGIES
from core.mcp_tools import pipeline_mcp
from measurement.instrumentation import RunRecorder, extract_usage, sum_usage
from helpers.app_utils import build_freshness_system_msg
from core.clients import MODEL
from measurement.pricing import estimate_cost, total_sub_agent_cost   
from measurement.run_store_writer import write_run
from measurement.prompt_versions import get_prompt_versions
from measurement.run_isolation import clear_run_artifacts
from helpers.logging_utils import setup_run_logger
from core.paths import PIPELINE_RAW
from score_topology_run import score_run

# Keep MCP logs quiet so run output stays focused on workflow progress.
logging.getLogger("mcp").setLevel(logging.WARNING)

# One log file per run, and its path goes onto the run row. Without that mapping the
# log folder cannot be kept aligned with the runs that survive: a log nobody claims
# cannot be tied to a topology, model or cost, yet still reads like a record of one.
_RUN_LOGGER, _LOG_PATH = setup_run_logger(_APP_DIR)

# --- Topology selection -----------------------------------------------------
# One source of truth for this run: the same spec supplies the agent that executes,
# the coordinator prompt that is hashed, and the string written to run.topology —
# so the run store cannot record a topology different from the one that ran.
# Override with SC_TOPOLOGY=<name>; see topologies/registry.py for valid names.
_TOPOLOGY = os.getenv("SC_TOPOLOGY", "planner_executor").strip() or "planner_executor"
_SPEC = _resolve_topology(_TOPOLOGY)
master = _SPEC.builder()

# Captures per-tool raw outputs and wall-clock timings for end-of-run reporting.

# function_middleware. function_middleware is MAF's hook that wraps every tool call: 
# when the master agent decides to call predict_delivery_delays_tool, 
# MAF runs your middleware function around the actual call, passing it a context object and 
# a call_next function you call to let the real tool execution happen. 

# RunRecorder.middleware is just that same pattern as function_middleware. function_middleware, 
# now packaged as a class so every consumer (run_once, the chat UI, the future harness) uses i
# dentical logic instead of three copies drifting apart. It logs the tool name, times call_next(),
# and after it returns, reads context.result (the tool's output) to record its length and 
# whether it's valid JSON.

# Need for contextvars.ContextVar : _wrap_as_tool's inner function (_run_sub_agent) is called by MAF internals with no way to 
# hand it "the current RunRecorder"
# A global variable would work for one run at a time but breaks the moment two things run 
# concurrently (which matters later for the concurrent topology). The fix is contextvars.ContextVar — 
# think of it as a variable that's global-looking, but each async task sees its own private 
# value rather than one shared value everyone stomps on.

# recorder = RunRecorder() runs the dataclass default, so recorder.sub_agent_usage is a 
# fresh empty list — call it list object L. It lives at some memory address; 
# recorder.sub_agent_usage is just a name pointing at L.
recorder = RunRecorder()

def _ts() -> str:
    """Return a compact clock string used in console progress lines."""
    return datetime.now().strftime("%H:%M:%S")

def _banner(text: str) -> None:
    """Print a visual section separator for easier terminal scanning."""
    print(f"\n{'='*60}\n[{_ts()}] {text}\n{'='*60}", flush=True)

_SHOW_STR_LIMIT = 200   # chars; longer narrative strings get truncated for console/log
_SHOW_STR_PREVIEW = 150


def _show(r) -> None:
    """Render structured model output when available, else fallback text.

    List-type fields are shown as counts, not dumped in full. MonolithOutput carries
    delayed_orders/simulations/recommended_actions/content -- up to ~50 rows each --
    and printing every row to the console and the run's log file made both unreadable
    for no benefit: the full data is already persisted (run_id), readable in full via
    report_topology_run.py or the run store directly.

    String fields are also truncated past _SHOW_STR_LIMIT chars (23-Aug-26, user) --
    predict_summary/diagnosis_summary can run several thousand characters, which was
    just as unreadable in the console/log as the row dumps this was originally built
    to fix. Shown as "<preview>... [N characters]" -- full text is unaffected, still
    persisted in full and readable via report_topology_run.py or the run store.
    """
    if r.value is not None and hasattr(r.value, "model_dump_json"):
        condensed = {}
        for k, v in r.value.model_dump().items():
            if isinstance(v, list):
                condensed[k] = f"<{len(v)} item(s)>"
            elif isinstance(v, str) and len(v) > _SHOW_STR_LIMIT:
                condensed[k] = f"{v[:_SHOW_STR_PREVIEW]}... [{len(v)} characters]"
            else:
                condensed[k] = v
        print(json.dumps(condensed, indent=2, default=str))
    else:
        print(r.text)

async def _reasoning_watcher(recorder: RunRecorder, stop_event: asyncio.Event,
                              idle_threshold: float = 4.0, heartbeat_every: float = 20.0) -> None:
    """Console status printer for the gap between the last tool call ending and the
    master's structured-output generation finishing (23-Aug-26: user flagged this gap
    looks 'stuck' -- confirmed it's the model itself writing predict/diagnose/simulate/
    recommendation/email content, not idle time; see render_timeline()'s "model
    composing answer" tail for the persisted/reported version of the same finding).

    There is no callback for this phase -- it is entirely inside the underlying LLM
    completion that master.run() awaits, invisible to recorder.middleware -- so this
    polls recorder.tool_calls for idle time instead, running concurrently with
    master.run() rather than inside it. Purely cosmetic: touches only console output,
    never recorder state, so it cannot affect timing measurements or persisted data.
    """
    last_msg_at: float | None = None
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.0)
            break
        except asyncio.TimeoutError:
            pass
        if not recorder.tool_calls:
            continue
        last = recorder.tool_calls[-1]
        if last.duration_s <= 0:
            continue  # a tool call is still running -- not idle, say nothing
        idle = time.perf_counter() - (last.started_at + last.duration_s)
        if idle < idle_threshold:
            last_msg_at = None  # a new wave may have started; re-arm for the next gap
            continue
        if last_msg_at is None:
            print("  >> master reasoning (writing the full structured response -- "
                  "usually the slowest part of the turn)...", flush=True)
            last_msg_at = time.perf_counter()
        elif time.perf_counter() - last_msg_at >= heartbeat_every:
            print(f"  .. still writing ({idle:.0f}s since the last tool call)", flush=True)
            last_msg_at = time.perf_counter()


# Canonical input file consumed by this one-shot orchestrator run.
_ORDERS = str(PIPELINE_RAW / "daily_delivery_logistics_1.csv")

def _final_answer_text(r) -> str:
    """Extract the model's final answer, matching _show()'s convention:
    structured output (.value) takes priority over raw .text, since MAF
    leaves .text blank when a response_format schema is used."""
    if r.value is not None and hasattr(r.value, "model_dump_json"):
        return r.value.model_dump_json()
    return r.text or ""

def _resolve_query(query_id: str) -> tuple[str, str, bool, str | None]:
    """Return (query_id, query_text, implies_capability) for this run.

    The frozen eval set (query_metadata, built by T16) is the source of query text, so
    the run row can be joined back to its implied-tools list — without that,
    tool_call_count_expected and missing_expected_tools_json are NULL and those validity
    checks do nothing. SC_QUERY overrides the text for an ad-hoc run, which cannot be a
    frozen query any more, so it is labelled as such rather than borrowing an ID whose
    implied tools no longer describe it.

    implies_capability is False only when implied_tools_json is an empty array, i.e. the
    query is an out-of-scope/refusal probe (currently Q1). It drives whether the
    confirmation turn is sent at all — see the `turns` construction in main(). An ad-hoc
    SC_QUERY run has no metadata to read, so it is treated as implying capability and
    keeps the full two-turn flow.
    """
    import sqlite3
    from measurement.run_store_schema import DB_PATH

    override = os.getenv("SC_QUERY", "").strip()
    if override:
        return "Q_ADHOC", override, True, None
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT query_text, implied_tools_json, clarification_response "
            "FROM query_metadata WHERE query_id = ?",
            (query_id,),
        ).fetchone()
        conn.close()
    except Exception as exc:
        raise SystemExit(f"ABORT: could not read query_metadata ({exc}).")
    if row is None:
        raise SystemExit(
            f"ABORT: query_id {query_id!r} is not in query_metadata. Run "
            f"measurement/build_query_metadata.py, or set SC_QUERY for an ad-hoc run."
        )
    # A NULL implied_tools_json means query_metadata was never populated for this query,
    # which is not the same as "implies nothing" -- fall back to the two-turn flow rather
    # than silently skipping a turn on incomplete metadata.
    implies_capability = True
    if row[1] is not None:
        implies_capability = bool(json.loads(row[1]))
    return query_id, row[0], implies_capability, row[2]


def _turn1_plan_text(responses: list) -> str | None:
    """Turn-1 chat_response verbatim, or None (T94).

    Single source for both plan_presented and turn1_plan_text so the boolean can
    never disagree with the text it is supposed to summarise.
    """
    if not responses:
        return None
    first = responses[0]
    value = getattr(first, "value", None)
    text = getattr(value, "chat_response", None) if value is not None else None
    text = (text or "").strip()
    return text or None


# Encodings warmed before the run clock starts. cl100k_base is the one this stack was
# observed fetching (5-Sep-26); o200k_base is warmed alongside it because newer OpenAI
# models tokenize with it and a second cold fetch mid-run would cost the same stall.
# ~3.5 MB total, downloaded once per machine and then reused from disk.
_TIKTOKEN_WARM_ENCODINGS = ("cl100k_base", "o200k_base")

# Above this, the encoders were almost certainly fetched over the network rather than
# read from disk. A warm load is ~0.1s, so 5s is far outside normal variation.
_TIKTOKEN_SLOW_S = 5.0


def _preflight_tiktoken() -> None:
    """Resolve tiktoken's BPE encoders before the run clock starts.

    tiktoken does not bundle its encoder files. On a cache miss it downloads them from
    openaipublic.blob.core.windows.net at first tokenizer use, which happens inside turn 1
    and therefore inside the measured wall time -- observed 29.2s cold versus 0.08s warm
    (5-Sep-26). Because nothing is printed between the query echo and the first API call,
    that download presents as an unexplained hang with no indication of cause.

    Running it here moves the cost outside every timed turn and converts a silent stall
    into a named, actionable message. Never fatal: if the encoders cannot be fetched the
    run proceeds and fails at its own API call, which reports a real error rather than
    this one's guess at the cause.
    """
    try:
        import tiktoken
    except ImportError:
        return  # not in the dependency tree on this machine -- nothing to warm

    import tempfile

    # tiktoken reads TIKTOKEN_CACHE_DIR, falling back to a directory under the OS temp
    # path. On macOS that resolves under /var/folders/.../T/, which the OS purges
    # periodically -- so an unset variable means the cache can silently vanish between
    # runs and reintroduce the stall mid-experiment.
    cache_dir = os.getenv("TIKTOKEN_CACHE_DIR", "").strip()
    if not cache_dir:
        default_dir = os.path.join(tempfile.gettempdir(), "data-gym-cache")
        print(f"  tiktoken cache     : WARNING — TIKTOKEN_CACHE_DIR is unset, so the cache "
              f"lives at {default_dir}")
        print(f"                       The OS purges that path periodically. When it is "
              f"purged, the next run re-downloads the")
        print(f"                       encoders inside turn 1 and stalls ~30s with no "
              f"output. Set TIKTOKEN_CACHE_DIR in .env")
        print(f"                       to a persistent path to prevent this.")

    t0 = time.perf_counter()
    try:
        for name in _TIKTOKEN_WARM_ENCODINGS:
            tiktoken.get_encoding(name)
    except Exception as exc:
        # Almost always a network failure reaching the encoder host. Say so plainly and
        # continue -- the run may not need a tokenizer at all, and if it does, its own
        # error will be more accurate than anything guessed here.
        elapsed = round(time.perf_counter() - t0, 1)
        print(f"  tiktoken cache     : COULD NOT WARM after {elapsed}s — {type(exc).__name__}: {exc}")
        print(f"                       tiktoken fetches its encoders from "
              f"openaipublic.blob.core.windows.net, which is a")
        print(f"                       different host from api.openai.com and can be "
              f"blocked independently of it. Verify with:")
        print(f"                         curl -sSf --max-time 10 -o /dev/null "
              f"https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken")
        print(f"                       Continuing — if a tokenizer is needed, the run will "
              f"fail with its own error.")
        return

    elapsed = round(time.perf_counter() - t0, 1)
    if elapsed >= _TIKTOKEN_SLOW_S:
        print(f"  tiktoken cache     : COLD — encoders downloaded, took {elapsed}s "
              f"(warm is ~0.1s)")
        print(f"                       This cost is now outside the measured turns, so this "
              f"run's timings are unaffected.")
        print(f"                       Cache dir: {cache_dir or 'OS temp (unset)'}")
    else:
        print(f"  tiktoken cache     : warm ({elapsed}s)")


async def main():
    """Execute a two-turn master-agent run and print tool execution summary."""
    # Full-workflow query: implies all five domain capabilities without naming any
    # tool or prescribing an order. Phrased as the delivery manager would ask it, so
    # every topology has to work out for itself what is needed and in what sequence —
    # which is the behaviour under measurement. A narrower query (e.g. predict +
    # diagnose only) exercises two capabilities and leaves the three MasterOutput
    # narrative fields legitimately empty, which is fine for a smoke test but useless
    # as a comparison workload.
    #
    # The text itself comes from query_metadata (T16's frozen set), keyed by
    # SC_QUERY_ID -- default Q11, the full-workflow query (renumbered 5-Sep-26; this
    # was Q10 before the query set was renumbered into ascending complexity_score
    # order -- see query_set_v1.xlsx's Q1 note for the full old-ID -> new-ID mapping).
    # SC_QUERY still overrides the text for an ad-hoc run; see _resolve_query for what
    # that costs.
    query_id, query, _implies_capability, _clarification = _resolve_query(
        os.getenv("SC_QUERY_ID", "").strip() or "Q11")
    query += f"\n\nThe input orders data is in the file at path: {_ORDERS}"

    # no-cache when 0, will do a freshness check and add a system message to the query 
    # and avoid re-run the prediction pipeline if it is fresh.
    # no-cache when 1, will skip the freshness check and re-run the prediction pipeline 
    # regardless of freshness.
    # freshness saves compute time inside the tool, not LLM tokens.
    _no_cache = os.getenv("SC_NO_CACHE", "").strip().lower() in ("1", "true", "yes")

    # Precedence check. execute_topology.sh exports SC_NO_CACHE, but the value also exists
    # in .env, which uv loads via --env-file. Verified 22-Aug-26 that the shell value
    # wins (uv fills gaps rather than overriding, and load_dotenv uses override=False)
    # — but this decides whether the run is measurement-valid, so assert it rather than
    # trust it. SC_EXPECT_NO_CACHE is set only by the script and never appears in .env,
    # so a mismatch means something silently overrode the caller's intent.
    _expected = os.getenv("SC_EXPECT_NO_CACHE", "").strip()
    if _expected and _expected != ("1" if _no_cache else "0"):
        raise SystemExit(
            f"ABORT: execute_topology.sh asked for SC_NO_CACHE={_expected} but the process sees "
            f"SC_NO_CACHE={os.getenv('SC_NO_CACHE')!r}. Something (likely .env via "
            f"--env-file) overrode it. Remove SC_NO_CACHE from .env — the script owns it. "
            f"Continuing would produce a run whose cache state does not match its label."
        )
    print(f"  effective config   : SC_NO_CACHE={int(_no_cache)}  "
          f"SC_DEV_PATH_FALLBACK={os.getenv('SC_DEV_PATH_FALLBACK', '<unset>')}  "
          f"model={MODEL}  query_id={query_id}")

    # Before anything is timed -- see the function's docstring for why this is not
    # left to happen on its own inside turn 1.
    _preflight_tiktoken()

    if not _no_cache:
        query += build_freshness_system_msg()
    else:
        # Measurement mode: remove every artifact a previous run derived, so the
        # tool-level upstream_missing guards actually reflect THIS run's state.
        # Without this the guards are satisfied by stale files and downstream tools
        # silently compute on the previous run's data -- see core/run_isolation.py.
        clear_run_artifacts()


    # Reuse one session across turns so planner and executor share context.
    session = master.create_session()

    # Turn 2 exists to approve a plan the coordinator offered on turn 1. A query that
    # implies zero capabilities (Q1, the out-of-scope probe) has no plan to approve --
    # the correct turn-1 outcome is a refusal, and sending "Yes, proceed." anyway
    # overrides that refusal and drives tool execution the query never warranted. The
    # measured behaviour for such a query is entirely turn 1's restraint, so the
    # confirmation turn is not sent at all.
    #
    # Gated on implied_tools_json being empty rather than on query_id == "Q1", so any
    # future out-of-scope probe is covered without touching this code, and on metadata
    # rather than on parsing the coordinator's text, so nothing here depends on matching
    # a refusal string across nine differently-worded conditions.
    # Turn 2 is the query's own clarification response where one is defined, and the
    # generic confirmation otherwise. Frozen in query_metadata rather than chosen here,
    # and sent to every topology executing this query whether or not it asked for
    # anything, so the reply is identical across conditions and is not itself a variable.
    #
    # Only a deliberately ambiguous query needs its own text. Sending it unconditionally
    # avoids having to detect a clarification request mid-run, which would put a
    # classification step on the critical path of a measured run.
    turns = [("TURN 1 — sending query (expecting plan)", query)]
    if _implies_capability:
        turn2 = _clarification or "Yes, proceed."
        label = ("TURN 2 — clarification response — tools will run now" if _clarification
                 else "TURN 2 — confirming ('Yes, proceed.') — tools will run now")
        turns.append((label, turn2))
    else:
        print(f"  turn-2 gate        : SKIPPED — {query_id} implies no capability "
              f"(out-of-scope probe); turn-1 restraint is the measured outcome")

    responses = []
    turn_times = []
    run_t0 = None

    # Keep MCP connection open for the full interaction window.
    async with pipeline_mcp:
        # before calling master.run(...), call the recorder.track_sub_agents(), 
        # which does _sub_agent_bucket.set(self.sub_agent_usage) — this says 
        # "for the rest of this task, anyone who asks _sub_agent_bucket.get() gets my list." 

        # with recorder.track_sub_agents(): calls _sub_agent_bucket.set(self.sub_agent_usage). 
        # This does not copy the list — it stores the same reference to L inside the contextvar. 
        # Now there are two names pointing at the identical list object: recorder.sub_agent_usage 
        # and whatever _sub_agent_bucket.get() returns inside this task.

        for label, msg in turns:
            _banner(f"\n{label}")
            # Echo the message actually sent. The query is overridable via SC_QUERY and
            # has the orders path appended, so what reaches the model is not necessarily
            # what is written in this file — printing it removes any doubt about which
            # workload produced the numbers below.
            for line in msg.splitlines():
                print(f"  > {line}" if line.strip() else "  >")
            print(flush=True)
            t0 = time.perf_counter()
            if run_t0 is None:
                run_t0 = t0          # anchor: start of turn 1
            stop_watcher = asyncio.Event()
            watcher_task = asyncio.create_task(_reasoning_watcher(recorder, stop_watcher))
            try:
                with recorder.track_sub_agents():
                    r = await master.run(msg, session=session, middleware=[recorder.middleware])
            finally:
                stop_watcher.set()
                await watcher_task
            turn_times.append(round(time.perf_counter() - t0, 2))
            responses.append(r)
            _banner(f"{label.split(' — ')[0]} — master response")
            _show(r)


    # ---- SUMMARY (single merged table: payloads + token usage + cost) ----
    # All agents (master + every domain sub-agent) share the same chat_client,
    # so a single MODEL from .env prices every row here.

    # Pair each tool with ITS OWN usage by name, not by list position — recorder.summary()
    # is in start order while sub_agent_usage is in completion order, and those diverge
    # the moment tools run concurrently. Same fix as core/run_store_writer.py; the writer
    # was corrected first and this display path was missed, so the persisted numbers were
    # right while the printed table attributed each tool another tool's tokens.
    _usage_q: dict[str, list[dict]] = {}
    for _u in recorder.sub_agent_usage:
        _usage_q.setdefault(_u.get("tool_name"), []).append(_u)
    _ZERO = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    rows = []
    for tc in recorder.summary():
        _q = _usage_q.get(tc["tool_name"])
        u = _q.pop(0) if _q else _ZERO
        rows.append({
            "tool":           tc["tool_name"],
            "duration_s":     tc["duration_s"],
            "input_chars":    tc["input_chars"],
            "output_chars":   tc["payload_chars"],
            "valid_json":     tc["valid_json"],
            "prompt_tok":     u["prompt_tokens"],
            "completion_tok": u["completion_tokens"],
            "total_tok":      u["total_tokens"],
            "cost_usd":       estimate_cost(MODEL, u),
        })

    # Specialist vs orchestration: an entry in sub_agent_usage matching no tool call is
    # a coordinator turn the topology drove itself, not a specialist. Reporting them in
    # one bucket hid ~190k prompt tokens inside "Sub-agents TOTAL" on the first
    # sequential run -- 2.6x the specialists' own cost, attributed to them.
    spec_totals, orch_totals = recorder.split_usage()
    sub_totals = recorder.total_sub_agent_usage()
    master_totals = sum_usage(*(extract_usage(r) for r in responses))
    grand_total = sum_usage(sub_totals, master_totals)

    tool_time = recorder.total_tool_time()
    run_time = round(sum(turn_times), 2)
    # Tools may run concurrently, so sum(durations) can exceed the run's wall time.
    # Subtracting it produced a negative master time (-90.29s observed). Use the real
    # span from first tool start to last tool end instead, which is correct for both
    # serial and parallel execution.
    tool_span = recorder.tool_wall_span_s()
    master_time = round(max(0.0, run_time - tool_span), 2)

    sub_cost = total_sub_agent_cost(MODEL, recorder.sub_agent_usage)
    master_cost = estimate_cost(MODEL, master_totals)
    grand_cost = round(sub_cost + (master_cost or 0), 6)

    rows.append({"tool": "— Sub-agents TOTAL", "duration_s": tool_time,
                 "input_chars": None, "output_chars": None, "valid_json": None,
                 "prompt_tok": spec_totals["prompt_tokens"],
                 "completion_tok": spec_totals["completion_tokens"],
                 "total_tok": spec_totals["total_tokens"],
                 "cost_usd": estimate_cost(MODEL, spec_totals)})
    if orch_totals["total_tokens"]:
        rows.append({"tool": "— Orchestration turns", "duration_s": None,
                     "input_chars": None, "output_chars": None, "valid_json": None,
                     "prompt_tok": orch_totals["prompt_tokens"],
                     "completion_tok": orch_totals["completion_tokens"],
                     "total_tok": orch_totals["total_tokens"],
                     "cost_usd": estimate_cost(MODEL, orch_totals)})
    _turn_word = "turn" if len(responses) == 1 else "turns"
    rows.append({"tool": f"— Master ({len(responses)} {_turn_word})", "duration_s": master_time,
                 "input_chars": None, "output_chars": None, "valid_json": None,
                 "prompt_tok": master_totals["prompt_tokens"],
                 "completion_tok": master_totals["completion_tokens"],
                 "total_tok": master_totals["total_tokens"], "cost_usd": master_cost})
    rows.append({"tool": "— GRAND TOTAL", "duration_s": run_time,
                 "input_chars": None, "output_chars": None, "valid_json": None,
                 "prompt_tok": grand_total["prompt_tokens"],
                 "completion_tok": grand_total["completion_tokens"],
                 "total_tok": grand_total["total_tokens"], "cost_usd": grand_cost})

    df = pd.DataFrame(rows)
    _banner(f"SUMMARY — tools, tokens & cost  [model={MODEL}, tool calls={len(recorder.tool_calls)}]")
    print(df.to_string(
        index=False,
        na_rep="",
        formatters={
            "duration_s":   lambda v: f"{v:.2f}s",
            "input_chars":  lambda v: "" if pd.isna(v) else f"{int(v):,}",
            "output_chars": lambda v: "" if pd.isna(v) else f"{int(v):,}",
            "valid_json":   lambda v: "" if pd.isna(v) else str(v),
            "prompt_tok":   lambda v: f"{int(v):,}",
            "completion_tok": lambda v: f"{int(v):,}",
            "total_tok":    lambda v: f"{int(v):,}",
            "cost_usd":     lambda v: "" if pd.isna(v) else f"${v:.4f}",
        },
    ))

    prompt_versions=get_prompt_versions(
        [
            "predict_delivery_delays", "diagnose_delay_patterns",
            "delay_simulation", "recommendation", "email_alert",
        ],
        topology=_SPEC.name,
        coordinator_key=_SPEC.coordinator_key,
    )

    # SC_RUN_N forces a specific repetition slot (e.g. the harness backfilling one
    # missing run_n without recomputing the rest). Left unset (the normal single-run
    # case), run_n stays None below and write_run() auto-computes MAX(run_n)+1 for this
    # (topology, query_id) pair (T113, 30-Aug-26) -- previously hardcoded to 1 on every
    # call, so every repeat run of the same combination overwrote the same slot
    # indistinguishably. Fixed 5-Sep-26: execute_topology.sh has set this env var since
    # T113 landed, but this file never read it, so the shell script's second positional
    # argument (run_n) was silently ignored every time.
    _run_n_raw = os.getenv("SC_RUN_N", "").strip()
    _run_n = int(_run_n_raw) if _run_n_raw else None

    run_id = write_run(
        recorder=recorder,
        responses=responses,
        prompt_versions=prompt_versions,
        log_path=str(_LOG_PATH),
        run_t0=run_t0,
        query_id=query_id,
        topology=_SPEC.name,
        run_n=_run_n,
        model=MODEL,
        run_phase=os.getenv("SC_RUN_PHASE"),
        # Set only by run_experiment.py; both None for a manual execute_topology.sh call.
        batch_id=os.getenv("SC_BATCH_ID") or None,
        execution_order=(int(os.getenv("SC_EXECUTION_ORDER"))
                         if os.getenv("SC_EXECUTION_ORDER", "").strip() else None),
        wall_time_s=run_time,
        final_answer=_final_answer_text(responses[-1]),
        plan_presented=bool(_turn1_plan_text(responses)),
        turn1_plan_text=_turn1_plan_text(responses),
        # Reproducibility: everything needed to rebuild this run's report from the
        # store alone. query_text_actual captures any SC_QUERY override plus the
        # appended orders path; env_config records the measurement toggles AS THEY
        # WERE, since path_fallback_used only says whether the fallback fired, not
        # whether it was enabled -- a cached run and a measurement run were otherwise
        # indistinguishable after the fact, which makes R16's "verify per run"
        # impossible to do retrospectively.
        query_text_actual=query,
        env_config={
            "SC_NO_CACHE": os.getenv("SC_NO_CACHE"),
            "SC_DEV_PATH_FALLBACK": os.getenv("SC_DEV_PATH_FALLBACK"),
            "SC_TOPOLOGY": os.getenv("SC_TOPOLOGY"),
            "SC_QUERY_overridden": bool(os.getenv("SC_QUERY", "").strip()),
            "OPENAI_MODEL": os.getenv("OPENAI_MODEL"),
            "OPENAI_MODEL_MINI": os.getenv("OPENAI_MODEL_MINI"),
            "SC_MCP_ENRICH_ROWS": os.getenv("SC_MCP_ENRICH_ROWS"),
            "SC_RUN_PHASE": os.getenv("SC_RUN_PHASE"),
        },
        turn_times=turn_times,
    )
    print(f"\nPersisted run_id: {run_id}")

    # Quality scoring (T42) — reads back what write_run() just persisted, so it runs
    # strictly AFTER wall_time_s/turn_times/tool offsets are already computed and
    # written. It judges output_text already sitting in tool_call rows; it does not
    # call the topology again and touches no timing field, so it cannot muddle the
    # run's own end-time numbers. Never let a judge failure invalidate an otherwise
    # good run — same posture as _print_run_validity below.
    if os.getenv("SC_SKIP_SCORE", "").strip().lower() not in ("1", "true", "yes"):
        _banner("QUALITY SCORING (T42) — judging persisted capability outputs")
        try:
            score_run(run_id)
        except Exception as exc:
            print(f"  (scoring failed, run is still valid: {exc})")

    # Echo the run-validity fields back from the DB rather than from local variables.
    # They were being written correctly all along but never surfaced, so a run with
    # plan_presented=0 or path_fallback_used=1 looked identical to a clean one at the
    # terminal. path_fallback_used=1 invalidates a measurement run outright (R10).
    _print_run_validity(run_id)


def _print_run_validity(run_id: str) -> None:
    """Read back and display the fields that decide whether a run is usable."""
    import sqlite3
    from measurement.run_store_schema import DB_PATH

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT topology, query_id, plan_presented, turn1_plan_text, path_fallback_used,
                      tool_call_count_expected, tool_call_count_actual,
                      missing_expected_tools_json, prompt_versions_json,
                      run_status, dependency_violations_json,
                      tool_base_offset_s, turn_times_json
               FROM run WHERE run_id = ?""",
            (run_id,),
        ).fetchone()
        timing_rows = conn.execute(
            "SELECT tool_name, started_offset_s, ended_offset_s FROM tool_call "
            "WHERE run_id = ? ORDER BY call_order", (run_id,),
        ).fetchall()
        empty_rows = conn.execute(
            "SELECT tool_name FROM tool_call WHERE run_id = ? AND empty_payload = 1 ORDER BY call_order",
            (run_id,),
        ).fetchall()
        # A tool the query never implied returning nothing is expected behaviour on a
        # topology that runs every capability regardless of query (Static-Graph DAG) --
        # not evidence of a defect. Only a tool the query DID imply, still empty, is the
        # original concerning signal (measurement/run_store_writer.py mirrors this same
        # distinction for run.run_status; 29-Aug-26, R41).
        from measurement.dependencies import canonical
        from measurement.run_store_writer import _get_implied_tools
        implied = _get_implied_tools(conn, row["query_id"]) if row else None
        implied_canonical = {canonical(t) for t in implied} if implied is not None else None
        conn.close()
    except Exception as exc:                      # never let reporting break a run
        print(f"  (could not read back run validity: {exc})")
        return

    if row is None:
        print("  (run row not found for read-back)")
        return

    plan = row["plan_presented"]
    # A query implying zero capabilities (Q1, the out-of-scope probe) inverts two checks:
    # there is no plan to present, and any tool call is a restraint failure rather than
    # expected work. implied is an empty set for such a query and None only when
    # query_metadata was never populated, so the two cases stay distinguishable.
    zero_capability = implied is not None and not implied
    if zero_capability:
        # plan_presented is derived from turn-1 chat_response being non-empty, which a
        # refusal satisfies. Reporting that as "yes" would read as a plan having been
        # offered, so it is labelled for what it actually is on this query.
        plan_txt = "n/a — out-of-scope probe; turn-1 text is a decline, not a plan"
    else:
        plan_txt = {1: "yes", 0: "NO", None: "n/a (structural)"}.get(plan, str(plan))
    fallback = row["path_fallback_used"]
    missing = row["missing_expected_tools_json"] or "[]"

    coordinator = "?"
    try:
        pv = json.loads(row["prompt_versions_json"] or "{}")
        coordinator = next(
            (f"{k.split(':', 1)[1]} = {v}" for k, v in pv.items() if k.startswith("coordinator:")),
            "MISSING",
        )
    except Exception:
        pass

    _banner("RUN VALIDITY")
    print(f"  topology           : {row['topology']}")
    print(f"  coordinator prompt : {coordinator}")
    print(f"  plan presented     : {plan_txt}")
    print(f"  input path source  : {'DERIVED from query (good)' if not fallback else 'DEV FALLBACK USED'}")
    empty_tools = [r["tool_name"] for r in empty_rows]

    print(f"  run status         : {row['run_status']}")
    print(f"  tools expected/ran : {row['tool_call_count_expected']} / {row['tool_call_count_actual']}")
    print(f"  missing tools      : {missing}")
    print(f"  empty results      : {empty_tools if empty_tools else 'none'}")

    violations = json.loads(row["dependency_violations_json"] or "[]")
    # check_dependencies() returns an empty list both when every dependency was genuinely
    # respected AND when zero capabilities ran at all (nothing to check is vacuously
    # "no violations"). Found 6-Sep-26 on dynamic_graph/Q8: tools expected/ran 3/0 still
    # printed "respected", reading as an orderly run when the real story is that nothing
    # executed. Distinguish the two by whether any tool call actually happened.
    if violations:
        print(f"  dependency order   : {len(violations)} VIOLATION(S)")
        for v in violations:
            print(f"    ! {v['detail']}")
    elif not row["tool_call_count_actual"]:
        print("  dependency order   : N/A — no tool calls were made (see tools expected/ran above)")
    else:
        print("  dependency order   : respected — every capability waited for its inputs")

    from measurement.dependencies import concurrency_report, missed_concurrency, render_timeline
    _timing = [dict(r) for r in timing_rows]
    # Anchors so the timeline starts at turn-1 start, not at the first tool.
    import json as _j
    _base_off = row["tool_base_offset_s"] if "tool_base_offset_s" in row.keys() else None
    _turns = _j.loads(row["turn_times_json"] or "[]") if "turn_times_json" in row.keys() else []
    conc = concurrency_report(_timing)
    missed = missed_concurrency(_timing)
    if conc and conc.get("exploited") is not None:
        busy = conc["actual_span_s"] - conc.get("coordinator_idle_s", 0.0)
        sched = (conc["critical_path_s"] / busy) if busy else None
        # A ratio above 1.0 is not a better-than-perfect schedule. critical_path_s is
        # the floor the dependency graph implies, so busy time coming in under it is
        # only possible by running a dependent pair at the same time. Printing the raw
        # percentage made those runs read as the best-scheduled ones: the pilot batch
        # shows Mesh values from 97% to 146%, with the 146% run also carrying a
        # dependency violation. report_topology_run.py already guarded this; this
        # printer did not, and it is the one every batch run uses (Risk Log R65).
        # Tolerance matches backfill_scheduling._INFEASIBLE_TOLERANCE: a ratio
        # marginally above 1.0 is timing noise from two-decimal offsets on short calls,
        # not a breach. Pilot values separate cleanly, with everything from 1.046 upward
        # carrying a recorded violation and the values below that carrying none.
        if sched is None:
            pass
        elif sched > 1.02:
            print(f"  scheduling         : n/a -- busy time ({busy:.2f}s) came in UNDER the "
                  f"dependency-respecting floor ({conc['critical_path_s']:.2f}s),")
            print(f"                       which is only possible by running a dependent pair "
                  f"concurrently. See the violation(s) above.")
        else:
            print(f"  scheduling         : {sched:.0%} of achievable overlap"
                  f"  ({len(missed)} pair(s) left unoverlapped)")
        print(f"  coordinator idle   : {conc.get('coordinator_idle_s', 0.0)}s spent deciding "
              f"between waves (not a scheduling fault)")
        print(f"  span               : {conc['actual_span_s']}s vs critical path "
              f"{conc['critical_path_s']}s")
        print("\n  execution timeline:")
        for line in render_timeline(_timing, indent="    ",
                                    base_offset_s=_base_off,
                                    turn_times=_turns):
            print(line)
        if missed:
            print()
            for m in missed:
                print(f"    ~ {m['detail']}")

    plan_text = row["turn1_plan_text"]
    if plan_text:
        print("\n  turn-1 plan text (stored verbatim, T94):")
        for line in plan_text.splitlines():
            print(f"    | {line}")
    else:
        print("  turn-1 plan text   : (none stored — no plan was produced)")

    problems = []
    if fallback:
        problems.append("path_fallback_used=1 — the agent did not receive a real input path; "
                        "a default was substituted. This run is NOT valid for measurement (R10).")
    if missing not in ("[]", "", None):
        problems.append(f"expected tools never ran: {missing}")
    if zero_capability:
        # Restraint is the measured outcome here (design spec F14). Running any tool for a
        # query that implies none is the failure mode, and it would otherwise pass silently
        # because missing_expected_tools_json is empty when nothing was expected.
        ran = row["tool_call_count_actual"] or 0
        if ran:
            problems.append(
                f"{ran} tool call(s) ran for {row['query_id']}, which implies no capability — "
                "the out-of-scope request was acted on rather than declined. This is the "
                "restraint failure F14 measures, not a valid capability run.")
    elif plan == 0:
        problems.append("no plan was presented on turn 1 — the 2-turn plan/confirm flow did not "
                        "engage; plan_presented is a measured comparison field.")
    concerning_empty = ([t for t in empty_tools if implied_canonical is None or canonical(t) in implied_canonical])
    if concerning_empty:
        problems.append(f"produced no data: {', '.join(concerning_empty)} — returned valid JSON with an "
                        "empty result list. Check whether a dependency was missing at call time.")
    unimplied_empty = [t for t in empty_tools if t not in concerning_empty]
    if unimplied_empty:
        print(f"  (not a problem: {', '.join(unimplied_empty)} returned empty, but the query never "
              f"implied it — expected on a topology that runs every capability regardless of query)")
    # Unexploited concurrency is not a correctness failure — the run can be entirely
    # valid and still slower than the dependency graph allows. Reported as a distinct
    # observation so it is neither mistaken for a defect nor lost in a "checks clean".
    if missed:
        problems.append(
            f"{len(missed)} pair(s) were free to overlap but ran sequentially. Not an error "
            "— the dependency order was respected — but it is what separates a "
            "code-scheduled topology from one that has to reason the independence out.")
    if violations:
        problems.append(
            f"dependency order violated ({len(violations)}): a capability acted before the "
            "work it consumes had finished. Recoverable via upstream_missing, but it is the "
            "orchestration quality being measured — see run.dependency_violations_json.")
    # The FABRICATED NARRATIVE line was removed 23-Aug-26 (user). It fired whenever a
    # narrative field was non-empty while its tool returned nothing -- which is also what
    # CORRECT behaviour looks like, since exception_handling.md requires the coordinator to
    # say so when a capability produced nothing. Observed on run f6a7bdd8 flagging
    # "Recommendations could not be completed because the recommendation tool returned no
    # recommended actions", i.e. exactly the required response. The "produced no data:"
    # line above already surfaces the underlying condition, without asserting intent.
    # Detection still runs and is still persisted to run.fabricated_narrative_json for
    # later analysis; only the console verdict is gone.

    if problems:
        print("\n  ATTENTION:")
        for p in problems:
            print(f"    - {p}")
    else:
        print("\n  All run-validity checks clean.")


if __name__ == "__main__":
    asyncio.run(main())