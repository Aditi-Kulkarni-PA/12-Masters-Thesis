"""Run isolation — clear this-run-derived artifacts before a measurement run.

Why this exists
---------------
`_check_predict_ran()` (prediction_pipeline/prediction_server.py) decides whether to
return `{"Error": "upstream_missing"}` by asking two existence questions: does the
prediction CSV exist, and does `daily_summary_overall` have any rows. Both are
satisfied by artifacts left behind by a PREVIOUS run.

So from the second run onward the guard cannot fire. Downstream tools
(diagnose / simulate / recommend / email) pass their dependency check and compute
answers from stale data even when predict has not run in this session. Two
consequences, both serious:

  1. Results are wrong-but-plausible: diagnose diagnoses the previous run's
     predictions. Nothing in the output says so.
  2. The dependency-discovery mechanism never engages. `upstream_missing` is the
     entire signal a Group B topology has for learning that predict must precede
     diagnose. If it never fires, no topology ever discovers anything -- they all
     just read stale files and look successful. That silently voids the control the
     topology comparison rests on.

`SC_NO_CACHE=1` did not cover this: it suppresses the freshness *marker* in the
prompt, but never removed the artifacts themselves.

What is cleared, and what is NOT
--------------------------------
Cleared: everything a run derives -- the daily prediction CSV and its sidecar, the
simulation CSV, the app's output/ copies, the diagnosis sidecar, and the `daily_*`
summary tables.

NEVER cleared: `hist_*` tables and `hist_delivery_delay_prediction.csv`. Those are
the historical/training-split baseline that diagnose, simulate and recommend compare
*against*. They are input data, not run output; deleting them would break every tool
rather than resetting it. Raw input orders under data/raw/ are likewise untouched.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from core.paths import PIPELINE_PROCESSED, PIPELINE_DB

_APP_DIR = Path(__file__).resolve().parent.parent          # measurement/ -> supply_chain_topology_app/
_REPO_DIR = _APP_DIR.parent                                 # -> 0_supply_chain_thesis/
_PIPELINE_PROCESSED = PIPELINE_PROCESSED
_DB_PATH = PIPELINE_DB
_APP_OUTPUT = _APP_DIR / "output"

# Files a run derives. Anything not listed here survives.
_DERIVED_FILES: tuple[Path, ...] = (
    # Completion marker first: prediction writes it last, so clearing it first means an
    # interrupted purge can never leave a marker claiming completeness over artifacts
    # that have already been deleted.
    _PIPELINE_PROCESSED / "daily_predict_complete.json",
    _PIPELINE_PROCESSED / "daily_delivery_delay_prediction.csv",
    _PIPELINE_PROCESSED / "daily_delivery_delay_prediction_meta.json",
    _PIPELINE_PROCESSED / "simulation_delivery_delays.csv",
    _PIPELINE_PROCESSED / "email_alerts.csv",
    _APP_OUTPUT / "daily_delivery_delay_prediction.csv",
    _APP_OUTPUT / "daily_delivery_delay_prediction_meta.json",
    _APP_OUTPUT / "simulate_delays_latest.csv",
    _APP_OUTPUT / "email_alerts.csv",
    _APP_OUTPUT / "diagnosis_meta.json",
)

# Guard against ever matching a hist_* table.
_DAILY_TABLE_PREFIX = "daily_"


def clear_run_artifacts(verbose: bool = True) -> dict[str, list[str]]:
    """Delete run-derived files and empty the daily_* tables. Returns what changed."""
    removed_files: list[str] = []
    cleared_tables: list[str] = []

    for path in _DERIVED_FILES:
        if path.is_file():
            path.unlink()
            removed_files.append(str(path.relative_to(_REPO_DIR)))

    if _DB_PATH.is_file():
        conn = sqlite3.connect(_DB_PATH)
        try:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ?",
                    (f"{_DAILY_TABLE_PREFIX}%",),
                )
            ]
            for table in tables:
                assert not table.startswith("hist_"), f"refusing to clear baseline table {table}"
                if conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                    conn.execute(f"DELETE FROM {table}")
                    cleared_tables.append(table)
            conn.commit()
        finally:
            conn.close()

    if verbose:
        print(f"  run isolation: cleared {len(removed_files)} derived file(s), "
              f"{len(cleared_tables)} daily_* table(s); hist_* baseline untouched")

    return {"files": removed_files, "tables": cleared_tables}
