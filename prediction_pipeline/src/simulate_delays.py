"""
Simulate delivery delays by modifying conditions for predicted delayed orders
and looking up historical severity patterns from the prediction database.

Pure computation — no agent/LLM dependency.  Called by the MCP server
(prediction_server.py) which exposes it as ``simulate_order_delays``.
"""

import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths — resolved relative to this file (src/ → prediction_pipeline/)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = os.getenv(
    "SC_PREDICTION_DB_PATH",
    str(_PROJECT_ROOT / "db" / "delivery_predictions.db"),
)
if not Path(_DB_PATH).is_absolute():
    _WORKSPACE_ROOT = _PROJECT_ROOT.parent
    _DB_PATH = str((_WORKSPACE_ROOT / _DB_PATH).resolve())

_CSV_DIR = _PROJECT_ROOT / "data" / "processed"
_CSV_PATH = _CSV_DIR / "daily_delivery_delay_prediction.csv"
_SIM_CSV = _CSV_DIR / "simulation_delivery_delays.csv"

_SEVERITY_LABELS = ["Short (1-2h)", "Medium (3-5h)", "Long (6+h)"]

# Cap on rows included in the text report returned to the LLM agent.
# The FULL result set is always saved to the simulation CSV; the app reads
# that CSV directly, so the agent only needs a sample to reason about.
_MAX_REPORT_ROWS = int(os.getenv("SC_SIM_REPORT_ROWS", "40"))
_FILTERABLE_COLS = {
    "region", "delivery_mode", "vehicle_type",
    "weather_condition", "delivery_partner", "package_type",
}
_CHANGEABLE_COLS = {"weather_condition", "vehicle_type", "delivery_mode"}

_SEL = (
    "delay_rate, severity_short_count, severity_medium_count, "
    "severity_long_count, delayed_count"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _severity_dist(row) -> dict:
    """Parse a summary-table row into {delay_rate, fracs}."""
    delay_rate, s_short, s_med, s_long, _delayed = row
    total = s_short + s_med + s_long
    if total == 0:
        return {"delay_rate": delay_rate, "fracs": [1 / 3, 1 / 3, 1 / 3]}
    return {
        "delay_rate": delay_rate,
        "fracs": [s_short / total, s_med / total, s_long / total],
    }


def _lookup_severity(
    conn: sqlite3.Connection, new_vals: dict, filter_ctx: dict | None = None,
) -> dict | None:
    """Look up severity distribution from the most specific matching table.

    *new_vals* are columns being changed.  *filter_ctx* contains column
    values from the filter that are NOT being changed — used to attempt a
    more specific combined-table lookup (e.g. filtering by delivery_mode
    while changing weather_condition allows a mode+weather lookup).
    """
    ctx = {**(filter_ctx or {}), **new_vals}  # merged, changes take priority
    w = ctx.get("weather_condition")
    v = ctx.get("vehicle_type")
    m = ctx.get("delivery_mode")

    # Combined: mode + weather
    if m and w:
        row = conn.execute(
            f"SELECT {_SEL} FROM hist_summary_by_mode_weather "
            "WHERE delivery_mode = ? AND weather_condition = ?", (m, w),
        ).fetchone()
        if row:
            return _severity_dist(row)

    # Combined: weather + vehicle
    if w and v:
        row = conn.execute(
            f"SELECT {_SEL} FROM hist_summary_by_weather_vehicle "
            "WHERE weather_condition = ? AND vehicle_type = ?", (w, v),
        ).fetchone()
        if row:
            return _severity_dist(row)

    # Combined: mode + vehicle (no dedicated table — average the two single-dim)
    if m and v and not w:
        row_m = conn.execute(
            f"SELECT {_SEL} FROM hist_summary_by_delivery_mode "
            "WHERE delivery_mode = ?", (m,),
        ).fetchone()
        row_v = conn.execute(
            f"SELECT {_SEL} FROM hist_summary_by_vehicle_type "
            "WHERE vehicle_type = ?", (v,),
        ).fetchone()
        if row_m and row_v:
            d_m, d_v = _severity_dist(row_m), _severity_dist(row_v)
            return {
                "delay_rate": (d_m["delay_rate"] + d_v["delay_rate"]) / 2,
                "fracs": [(a + b) / 2 for a, b in zip(d_m["fracs"], d_v["fracs"])],
            }

    # Single-dimension fallback — pick the one with the highest delay_rate
    best = None
    for col, val in new_vals.items():
        if col not in _CHANGEABLE_COLS:
            continue
        table = f"hist_summary_by_{col}"
        try:
            row = conn.execute(
                f"SELECT {_SEL} FROM {table} WHERE {col} = ?", (val,),
            ).fetchone()
        except sqlite3.OperationalError:
            continue
        if row:
            d = _severity_dist(row)
            if best is None or d["delay_rate"] > best["delay_rate"]:
                best = d
    return best


def _assign_severity_labels(dist: dict, n: int) -> list[str]:
    """Distribute *n* rows across severity buckets proportionally."""
    fracs = dist["fracs"]
    counts = [round(f * n) for f in fracs]
    # Fix rounding so counts sum to n
    diff = n - sum(counts)
    if diff > 0:
        counts[0] += diff
    elif diff < 0:
        for i in range(2, -1, -1):
            take = min(counts[i], -diff)
            counts[i] -= take
            diff += take
            if diff == 0:
                break
    labels: list[str] = []
    for label, c in zip(_SEVERITY_LABELS, counts):
        labels.extend([label] * c)
    rng = np.random.default_rng(42)
    rng.shuffle(labels)
    return list(labels)


# ---------------------------------------------------------------------------
# Public API — called by the MCP server
# ---------------------------------------------------------------------------

def run_simulation(scenario: str, filters: str, changes: str) -> str:
    """Simulate delivery delays for a what-if scenario.

    Args:
        scenario: Natural-language description of the what-if scenario.
        filters:  JSON selecting rows to modify.
                  Keys: region, delivery_mode, vehicle_type, weather_condition,
                        delivery_partner, package_type, min_distance_km (float).
        changes:  JSON with new column values to apply.
                  Keys: weather_condition, vehicle_type, delivery_mode.

    Returns:
        A text report with simulation results and a table of affected rows.
    """
    # ---- 1. Parse & validate inputs ----
    try:
        filt: dict = json.loads(filters)
    except (json.JSONDecodeError, TypeError):
        return "ERROR: 'filters' must be valid JSON."
    try:
        chg: dict = json.loads(changes)
    except (json.JSONDecodeError, TypeError):
        return "ERROR: 'changes' must be valid JSON."
    if not chg:
        return "ERROR: 'changes' must specify at least one column to modify."

    # Normalise to lowercase
    filt = {
        k: (v if isinstance(v, (int, float)) else str(v).strip().lower())
        for k, v in filt.items()
    }
    chg = {k: str(v).strip().lower() for k, v in chg.items()}

    # ---- 2. Read delayed-orders CSV ----
    csv_path = _CSV_PATH
    if not csv_path.exists():
        return (
            "No prediction data found — please run the predict tool first "
            "so there is data to simulate against."
        )
    df = pd.read_csv(csv_path)
    if df.empty:
        return "Prediction CSV is empty — nothing to simulate."

    # Lowercase string columns used for filtering (preserve severity labels)
    _lower_cols = _FILTERABLE_COLS & set(df.columns)
    for c in _lower_cols:
        if df[c].dtype == "object":
            df[c] = df[c].str.strip().str.lower()

    # ---- 3. Build filter mask ----
    mask = pd.Series(True, index=df.index)
    min_dist = filt.pop("min_distance_km", None)
    for col, val in filt.items():
        if col in _FILTERABLE_COLS and col in df.columns:
            mask &= df[col] == val
    if min_dist is not None:
        mask &= df["distance_km"] >= float(min_dist)

    affected = df.loc[mask].copy()
    if affected.empty:
        return (
            f"No rows matched the filters {json.dumps(filt)}. "
            "Nothing to simulate."
        )

    n_affected = len(affected)
    orig_sev = affected["predict_severity_label"].value_counts().to_dict()

    # ---- 4. Apply column changes ----
    for col, val in chg.items():
        if col in _CHANGEABLE_COLS and col in affected.columns:
            affected[col] = val

    # ---- 5. Look up severity from DB ----
    db_path = _DB_PATH
    if not Path(db_path).exists():
        return "Prediction database not found — cannot look up severity patterns."

    conn = sqlite3.connect(str(db_path))
    try:
        # Pass filter cols (minus distance) as context for combined-table lookups
        filter_ctx = {k: v for k, v in filt.items() if k in _CHANGEABLE_COLS}
        dist = _lookup_severity(conn, chg, filter_ctx)
    finally:
        conn.close()

    if dist is None:
        return (
            f"No historical severity data for conditions {json.dumps(chg)}. "
            "Cannot estimate impact."
        )

    # ---- 6. Assign new severity labels ----
    affected["original_severity"] = df.loc[mask, "predict_severity_label"].values
    new_labels = _assign_severity_labels(dist, n_affected)
    affected["simulated_severity"] = new_labels

    # ---- 7. Save simulation CSV ----
    affected.to_csv(str(_SIM_CSV), index=False)

    # ---- 8. Build return string ----
    new_sev = (
        pd.Series(new_labels).value_counts().reindex(_SEVERITY_LABELS, fill_value=0).to_dict()
    )

    lines = [
        f"## Simulation: {scenario}",
        f"- **Rows affected**: {n_affected} of {len(df)} delayed orders",
        f"- **Filters applied**: {json.dumps(filt) if filt else 'none'}"
        + (f", min_distance_km >= {min_dist}" if min_dist else ""),
        f"- **Changes applied**: {json.dumps(chg)}",
        f"- **Hist. delay rate for new conditions**: {dist['delay_rate']:.1%}",
        f"- **Original severity dist**: {orig_sev}",
        f"- **Simulated severity dist**: {new_sev}",
        "",
    ]

    display_cols = [
        "delivery_id", "delivery_partner", "delivery_mode", "region",
        "weather_condition", "vehicle_type", "distance_km",
        "original_severity", "simulated_severity",
    ]
    display_cols = [c for c in display_cols if c in affected.columns]
    shown = affected[display_cols].head(_MAX_REPORT_ROWS)
    lines.append(shown.to_string(index=False))
    if n_affected > _MAX_REPORT_ROWS:
        lines.append(
            f"\n(Showing first {_MAX_REPORT_ROWS} of {n_affected} affected rows. "
            f"The FULL simulation result was saved to {_SIM_CSV} — the app reads it "
            "from there, so do NOT try to reproduce all rows.)"
        )

    return "\n".join(lines)
