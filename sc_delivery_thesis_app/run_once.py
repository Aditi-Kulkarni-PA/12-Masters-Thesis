"""First end-to-end MAF run — headless, with live progress logging."""
import asyncio, json, os, sys, time
from datetime import datetime
from pathlib import Path
import logging

# Ensure local package imports resolve when executing this file directly.
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

#from agent_framework import FunctionInvocationContext, function_middleware
from topologies.planner_executor import supply_chain_delivery_master_agent as master
from core.mcp_tools import pipeline_mcp
from core.instrumentation import RunRecorder, extract_usage, sum_usage
from helpers.app_utils import build_freshness_system_msg

# Keep MCP logs quiet so run output stays focused on workflow progress.
logging.getLogger("mcp").setLevel(logging.WARNING)

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

def _show(r) -> None:
    """Render structured model output when available, else fallback text."""
    if r.value is not None and hasattr(r.value, "model_dump_json"):
        print(r.value.model_dump_json(indent=2))
    else:
        print(r.text)

# Canonical input file consumed by this one-shot orchestrator run.
_ORDERS = str(_APP_DIR.parent / "prediction_pipeline" / "data" / "raw" / "daily_delivery_logistics_1.csv")

async def main():
    """Execute a two-turn master-agent run and print tool execution summary."""
    query = "Predict today's delivery delays and diagnose the main delay patterns."
    query += f"\n\nThe input orders data is in the file at path: {_ORDERS}"

    # no-cache when 0, will do a freshness check and add a system message to the query 
    # and avoid re-run the prediction pipeline if it is fresh.
    # no-cache when 1, will skip the freshness check and re-run the prediction pipeline 
    # regardless of freshness.
    # freshness saves compute time inside the tool, not LLM tokens.
    if os.getenv("SC_NO_CACHE", "").strip().lower() not in ("1", "true", "yes"):
        query += build_freshness_system_msg()


    # Reuse one session across turns so planner and executor share context.
    session = master.create_session()

    turns = [
        ("TURN 1 — sending query (expecting plan)", query),
        ("TURN 2 — confirming ('Yes, proceed.') — tools will run now", "Yes, proceed."),
    ]

    responses = []
    turn_times = []

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
            t0 = time.perf_counter()
            with recorder.track_sub_agents():
                r = await master.run(msg, session=session, middleware=[recorder.middleware])
            turn_times.append(round(time.perf_counter() - t0, 2))
            responses.append(r)
            _banner(f"{label.split(' — ')[0]} — master response")
            _show(r)


    # ---- SUMMARY ----
    _banner("\nSUMMARY — tool payloads")
    for tc in recorder.summary():
        print(f"  {tc['tool_name']:35s} {tc['duration_s']:7.2f}s  {tc['payload_chars']:>8,} chars  "
              f"valid_json={tc['valid_json']}")

    _banner("SUMMARY — sub-agent token usage")
    for u in recorder.sub_agent_usage:
        print(f"  {u['tool_name']:35s} prompt={u['prompt_tokens']:>6} "
              f"completion={u['completion_tokens']:>6} total={u['total_tokens']:>6}")

    sub_totals = recorder.total_sub_agent_usage()
    master_totals = sum_usage(*(extract_usage(r) for r in responses))
    grand_total = sum_usage(sub_totals, master_totals)

    tool_time = recorder.total_tool_time()
    run_time = round(sum(turn_times), 2)
    master_time = round(run_time - tool_time, 2)

    print(f"\n  Sub-agents total : prompt={sub_totals['prompt_tokens']:>6} "
          f"completion={sub_totals['completion_tokens']:>6} total={sub_totals['total_tokens']:>6}  "
          f"time={tool_time:7.2f}s")
    print(f"  Master ({len(responses)} turns): prompt={master_totals['prompt_tokens']:>6} "
          f"completion={master_totals['completion_tokens']:>6} total={master_totals['total_tokens']:>6}  "
          f"time~={master_time:7.2f}s (approx, wall time minus tool time)")
    print(f"  GRAND TOTAL      : prompt={grand_total['prompt_tokens']:>6} "
          f"completion={grand_total['completion_tokens']:>6} total={grand_total['total_tokens']:>6}  "
          f"time={run_time:7.2f}s (wall clock, all turns)")


if __name__ == "__main__":
    asyncio.run(main())