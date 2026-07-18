"""First end-to-end MAF run — headless, with live progress logging."""
import asyncio, json, sys, time
from datetime import datetime
from pathlib import Path
import logging
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from agent_framework import FunctionInvocationContext, function_middleware
from delivery_agents import supply_chain_delivery_master_agent as master, pipeline_mcp
from helpers.app_utils import build_freshness_system_msg

logging.getLogger("mcp").setLevel(logging.WARNING)

captured: dict[str, str] = {}
timings: dict[str, float] = {}

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def _banner(text: str) -> None:
    print(f"\n{'='*60}\n[{_ts()}] {text}\n{'='*60}", flush=True)

@function_middleware
async def capture(context: FunctionInvocationContext, call_next):
    name = context.function.name
    print(f"\n>>> [{_ts()}] TOOL START : {name}", flush=True)
    t0 = time.perf_counter()
    await call_next()
    dt = round(time.perf_counter() - t0, 2)
    timings[name] = dt

    result_value = context.result
    if isinstance(result_value, str):
        payload = result_value
    elif isinstance(result_value, (list, tuple)):
        payload = "".join(getattr(c, "text", "") for c in result_value)
    else:
        payload = repr(result_value)

    captured[name] = payload
    print(f"<<< [{_ts()}] TOOL DONE  : {name}  ({dt}s, {len(payload):,} chars, "
          f"valid_json={_is_json(payload)})", flush=True)

def _is_json(s: str) -> bool:
    try:
        json.loads(s); return True
    except Exception:
        return False

def _show(r) -> None:
    if r.value is not None and hasattr(r.value, "model_dump_json"):
        print(r.value.model_dump_json(indent=2))
    else:
        print(r.text)

_ORDERS = str(_APP_DIR.parent / "prediction_pipeline" / "data" / "raw" / "daily_delivery_logistics_1.csv")

async def main():
    query = "Predict today's delivery delays and diagnose the main delay patterns."
    query += f"\n\nThe input orders data is in the file at path: {_ORDERS}"
    query += build_freshness_system_msg()

    session = master.create_session()

    async with pipeline_mcp:
        _banner("\nTURN 1 — sending query (expecting plan)")
        r1 = await master.run(query, session=session, middleware=[capture])
        _banner("\nTURN 1 — master response")
        _show(r1)

        _banner("\nTURN 2 — confirming ('Yes, proceed.') — tools will run now")
        r2 = await master.run("Yes, proceed.", session=session, middleware=[capture])
        _banner("TURN 2 — master response")
        _show(r2)

    _banner("\nSUMMARY — tool payloads")
    for name in captured:
        print(f"  {name:35s} {timings[name]:7.2f}s  {len(captured[name]):>8,} chars  "
              f"valid_json={_is_json(captured[name])}")

if __name__ == "__main__":
    asyncio.run(main())