"""
MCP Server — thin wrapper over the prediction pipeline.

Exposes three tools via stdio transport:
  - predict_delivery_delays: runs the two-stage ML pipeline on a CSV
  - get_delay_diagnosis: reads all summary tables and returns comparison data
  - simulate_order_delays: what-if simulation on predicted delayed orders

Start:
    python prediction_server.py
"""

import contextlib
import json
import os
import sys
import sqlite3
from pathlib import Path
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
# (env vars in .env use relative paths like "0_supply_chain_capstone/...")
_WORKSPACE_ROOT = _PROJECT_ROOT.parent

if not Path(_DB_PATH).is_absolute():
    _DB_PATH = str((_WORKSPACE_ROOT / _DB_PATH).resolve())

_csv_dir_raw = os.getenv(
    "SC_DELIVERY_OUTPUT_DIR",
    str(_PROJECT_ROOT.parent / "supply_chain_delivery_app" / "output"),
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
# Check if prediction artifacts exist before starting the server, to catch issues early.
# ---------------------------------------------------------------------------

def _check_predict_ran() -> str | None:
    """Returns an error string if prediction artifacts are missing, else None.

    Checks the pipeline's own processed CSV first (written by predict_delivery_delays),
    then falls back to the app output CSV (written by post_processing after a full run).
    """
    if not (_PIPELINE_CSV.exists() or _CSV_PATH.exists()):
        return "Prediction CSV not found. Run predict_delivery_delays first."
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
    """Run the two-stage delay prediction pipeline on a CSV of orders.

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
    return DailyPredictionPipeline.get_prediction(file_path, csv_dir)


@mcp.tool()
async def get_delay_diagnosis() -> str:
    """Read daily and historical summary tables and return comparison data.

    Returns overall KPIs, dimension-by-dimension comparison (daily vs hist),
    and high-risk pattern combinations — everything needed for root-cause
    diagnosis of delivery delays.
    """
    err = _check_predict_ran()
    if err:
        return json.dumps({"Error": "upstream_missing", "message": err})
    
    with contextlib.redirect_stdout(sys.stderr):
        print(f"[MCP DEBUG] get_delay_diagnosis called, db_path={_DB_PATH}", file=sys.stderr)
        data = DatabaseOperations.get_diagnosis_data(_DB_PATH)
        print(f"[MCP DEBUG] get_delay_diagnosis result keys: {list(data.keys())}", file=sys.stderr)
    return json.dumps(data)


@mcp.tool()
async def simulate_order_delays(scenario: str, filters: str, changes: str) -> str:
    """Simulate delivery delays by modifying conditions for a subset of
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
    err = _check_predict_ran()
    if err:
        return json.dumps({"Error": "upstream_missing", "message": err})
    
    return run_simulation(scenario, filters, changes)


if __name__ == "__main__":
    mcp.run(transport="stdio")
