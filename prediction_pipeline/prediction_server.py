"""
MCP Server — thin wrapper over the prediction pipeline.

Exposes three tools via stdio transport:
  - predict_delivery_delays: runs the two-stage ML pipeline on a CSV
  - get_delay_diagnosis: reads all summary tables and returns comparison data
  - simulate_order_delays: what-if simulation on predicted delayed orders

Start:
    python prediction_server.py
"""

import json
import os
import sys
import sqlite3
import anyio.to_thread
from pathlib import Path
from datetime import datetime, timezone
import logging
logging.getLogger("mcp").setLevel(logging.WARNING)

from dotenv import load_dotenv, find_dotenv

load_dotenv(dotenv_path=find_dotenv(), override=False)

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Resolve project paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_DB_PATH = os.getenv(
    "SC_PREDICTION_DB_PATH",
    str(_PROJECT_ROOT / "db" / "delivery_predictions.db"),
)

# Resolve both paths against the workspace root when they are relative
# (env vars in .env use relative paths like "0_supply_chain_thesis/...")
_WORKSPACE_ROOT = _PROJECT_ROOT.parent

if not Path(_DB_PATH).is_absolute():
    _DB_PATH = str((_WORKSPACE_ROOT / _DB_PATH).resolve())

_csv_dir_raw = os.getenv(
    "SC_DELIVERY_OUTPUT_DIR",
    str(_PROJECT_ROOT.parent / "supply_chain_topology_app" / "output"),
)
if not Path(_csv_dir_raw).is_absolute():
    _csv_dir_raw = str((_WORKSPACE_ROOT / _csv_dir_raw).resolve())
_CSV_PATH = Path(_csv_dir_raw) / "daily_delivery_delay_prediction.csv"

# Canonical location where predict_delivery_delays always writes the CSV
_PIPELINE_CSV = _PROJECT_ROOT / "data" / "processed" / "daily_delivery_delay_prediction.csv"

from src.daily_predict import DailyPredictionPipeline
from src.database_operations_10 import DatabaseOperations
from src.simulate_delays import run_simulation

# ---------------------------------------------------------------------------
# Dev-mode path recovery (OFF by default)
# ---------------------------------------------------------------------------
# Cheaper-tier models occasionally hallucinate file_path instead of copying it
# verbatim from the prompt (e.g. a placeholder like "/mnt/data/input_orders.csv",
# or a truncated relative path like "./daily_delivery_logistics_1.csv"). This is
# a real model-reliability finding, not something to silently paper over during
# measurement runs. But while iterating with cheap models during development,
# it's just friction. When SC_DEV_PATH_FALLBACK=1, a file_path that doesn't
# exist is resolved by basename against the canonical raw-data directory before
# giving up. Leave this unset (default) for any run whose results you intend to
# use as a thesis measurement — it must see the model's real path-handling
# behavior, hallucinations included.
_RAW_DATA_DIR = _PROJECT_ROOT / "data" / "raw"

# Fallback marker file for dev-mode path recovery (see _resolve_file_path)
_FALLBACK_MARKER = _PROJECT_ROOT / "data" / ".dev_path_fallback_fired.json"

def _resolve_file_path(file_path: str) -> str:
    if Path(file_path).is_file():
        return file_path
    if os.getenv("SC_DEV_PATH_FALLBACK", "").strip().lower() not in ("1", "true", "yes"):
        return file_path  # unchanged -- let the real FileNotFoundError surface
    candidate = _RAW_DATA_DIR / Path(file_path).name
    if candidate.is_file():
        print(
            f"  !! DEV_PATH_FALLBACK !! model passed file_path='{file_path}' (not found) "
            f"-- substituting known file '{candidate}' so the run can proceed. "
            "SC_DEV_PATH_FALLBACK=1 is set -- unset it for thesis measurement runs.",
            file=sys.stderr,
            flush=True,
        )
        _FALLBACK_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _FALLBACK_MARKER.write_text(json.dumps({
            "fired_at": datetime.now(timezone.utc).isoformat(),
            "bad_path": file_path,
            "substituted": str(candidate),
        }))
        return str(candidate)
    return file_path  # no match either -- let the real error surface


# ---------------------------------------------------------------------------
# Check if prediction artifacts exist before starting the server, to catch issues early.
# ---------------------------------------------------------------------------

_stdout_guard_installed = False


def _install_stdout_guard() -> None:
    """Point sys.stdout at stderr permanently, so a stray print() anywhere in the
    pipeline libraries can never corrupt this stdio-transport server's protocol stream.

    Replaces the per-call `contextlib.redirect_stdout(sys.stderr)` this module used
    before tool bodies were moved onto worker threads (23-Aug-26). redirect_stdout
    mutates a PROCESS-GLOBAL and restores it on exit; that was safe only while handlers
    were serialised by their own blocking bodies. Once two handlers can genuinely run at
    once, two overlapping enter/exit pairs can restore sys.stdout out of order and leave
    it pointing at the wrong stream -- the exact corruption the original guard existed to
    prevent.

    Safe to reassign sys.stdout because mcp/server/stdio.py:49 captures
    `sys.stdout.buffer` ONCE when the transport starts and writes protocol frames through
    that captured handle, not through `sys.stdout`. Called from tool bodies rather than at
    import time so that capture is guaranteed to have already happened. Set-once and
    idempotent, so concurrent callers cannot interleave destructively.

    Prints are DISCARDED, not forwarded to stderr. The pipeline is chatty at progress
    level ("Skipping shape display", "Summary table 3/12", "Model loaded from: ..."), and
    stderr from this subprocess is inherited by the parent, so forwarding dumped ~25 lines
    into the middle of every run's console output and log. Nothing diagnostic is lost:
    real failures raise, and the exception propagates back through MCP as a tool error.
    Set SC_PIPELINE_VERBOSE=1 to send them to stderr instead when debugging the pipeline.

    NOTE this is strictly better than the pre-23-Aug-26 behaviour, not merely different:
    predict_delivery_delays never had a redirect at all, so its prints went to the real
    stdout -- i.e. straight INTO the JSON-RPC protocol stream, where the client dropped
    them as unparseable. They were always being emitted; they were just polluting the
    protocol channel instead of a console.
    """
    global _stdout_guard_installed
    if _stdout_guard_installed:
        return
    if os.getenv("SC_PIPELINE_VERBOSE", "").strip().lower() in ("1", "true", "yes"):
        sys.stdout = sys.stderr
    else:
        sys.stdout = open(os.devnull, "w")
    _stdout_guard_installed = True


def _check_predict_ran() -> str | None:
    """Returns an error string if prediction has not completed, else None.

    Checks the completion marker FIRST. Prediction writes its artifacts in sequence —
    DB tables, then the delayed CSV, then the sidecar — and the marker is written last,
    once all of them are in place. Testing individual artifacts instead meant a
    capability running concurrently with prediction could find one artifact present and
    another still missing, pass this check, and compute on a half-updated system with no
    error raised anywhere. Observed 22-Aug-26: with all five capabilities dispatched at
    once, diagnose and recommend correctly failed at ~2s while email happened to check
    at ~6.7s, just after the CSV appeared, and silently produced output from state that
    prediction had not finished writing.

    The individual-artifact checks are kept below the marker as a fallback, so a run
    started before this marker existed still behaves as it used to.
    """
    marker = _PIPELINE_CSV.parent / "daily_predict_complete.json"
    if marker.is_file():
        return None
    if not (_PIPELINE_CSV.exists() or _CSV_PATH.exists()):
        return "Prediction has not completed yet. Run predict_delivery_delays first."
    try:
        conn = sqlite3.connect(_DB_PATH)
        count = conn.execute("SELECT COUNT(*) FROM daily_summary_overall").fetchone()[0]
        conn.close()
        if count == 0:
            return "Prediction DB tables are empty. Run predict_delivery_delays first."
    except Exception as e:
        return f"DB health check failed: {e}"
    return None

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp = FastMCP("prediction_pipeline")


@mcp.tool()
async def predict_delivery_delays(file_path: str, csv_dir: str = "") -> str:
    """Run the two-stage ML pipeline over the raw input orders file supplied in the
    request, to predict which of today's orders will be delayed and classify
    each one's severity. Produces today's predicted delayed orders. Needs
    nothing but the input orders file.

    Stage 1 — classifies delayed vs on-time (Random Forest).
    Stage 2 — assigns severity: Short (1-2h), Medium (3-5h), Long (6+h).

    Persists results to CSV and refreshes the SQLite summary tables.

    Returns a JSON string with two keys:
      - "summary": aggregate stats (total_orders, total_delayed, pct_delayed,
                   severity_short/medium/long, csv_path, delayed_csv_path,
                   showing_top_n, top_regions, top_weather, top_partners)
      - "delayed_orders": all delayed rows with rule-based delay_reason (agent enriches all of them)

    Args:
        file_path: Absolute path to the input CSV with delivery orders.
        csv_dir:   Directory for the output prediction CSV.
                   Defaults to prediction_pipeline/data/processed.
    """
    # Offloaded to a worker thread so this handler does not hold the event loop for its
    # whole duration. See _install_stdout_guard() and the module note on concurrency.
    _install_stdout_guard()
    file_path = _resolve_file_path(file_path)
    return await anyio.to_thread.run_sync(
        DailyPredictionPipeline.get_prediction, file_path, csv_dir
    )


@mcp.tool()
async def get_delay_diagnosis() -> str:
    """Analyse today's predicted delays against historical baselines across every
    dimension, and identify high-risk pattern combinations. Works from
    today's predicted delay output; the historical baseline is already
    available and needs no work to produce. Produces today's diagnosis
    results.

    Returns overall KPIs, dimension-by-dimension comparison (daily vs hist),
    and high-risk pattern combinations — everything needed for root-cause
    diagnosis of delivery delays.
    """
    _install_stdout_guard()
    err = await anyio.to_thread.run_sync(_check_predict_ran)
    if err:
        return json.dumps({"Error": "upstream_missing", "message": err})

    data = await anyio.to_thread.run_sync(
        DatabaseOperations.get_diagnosis_data, _DB_PATH
    )
    return json.dumps(data)


@mcp.tool()
async def simulate_order_delays(scenario: str, filters: str, changes: str) -> str:
    """Simulate what-if changes to weather, vehicle type, region or delivery mode
    and report how delay severity shifts. Works from today's predicted
    delayed orders, re-scoring their severity against historical patterns
    that are already available.
    predicted delayed orders and looking up historical severity patterns.

    Applies column changes to filtered rows, looks up the historical severity
    distribution for the new conditions, and reassigns severity labels
    proportionally.  Results are saved to a simulation CSV.

    Args:
        scenario: Natural-language description of the what-if scenario.
        filters:  JSON selecting rows to modify.
                  Keys: region, delivery_mode, vehicle_type, weather_condition,
                        delivery_partner, package_type, min_distance_km (float).
                  Example: '{"region": "east"}'
        changes:  JSON with new column values to apply.
                  Keys: weather_condition, vehicle_type, delivery_mode.
                  Example: '{"weather_condition": "stormy"}'
    """
    _install_stdout_guard()
    err = await anyio.to_thread.run_sync(_check_predict_ran)
    if err:
        return json.dumps({"Error": "upstream_missing", "message": err})

    return await anyio.to_thread.run_sync(
        run_simulation, scenario, filters, changes
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
