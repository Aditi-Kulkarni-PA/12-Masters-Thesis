"""Canonical project paths — import these, never recompute them.

Why this file exists
--------------------
Eight modules were each deriving the pipeline directory by counting `.parent` hops from
their own location. Two of them counted wrong, and both failures were silent:

  tools/email_customers.py   `_APP_DIR.parent.parent` resolved OUTSIDE the project. The
                             tool fell back to a stale CSV sitting at the wrong path and
                             generated customer emails from 143 rows instead of 1,346 —
                             for several runs, with no error raised.
  helpers/post_processing.py same shape, so the pipeline CSV never matched and the email
                             export quietly used the app-output copy instead.

A wrong `.parent` count does not raise; it produces a path that does not exist, and code
that treats "does not exist" as "fall back to the other one" turns a bug into wrong data.
Renaming the repo folder is what finally exposed it.

Rule: every path under the repo comes from here. `Path(__file__).parent.parent...` for
anything project-level is a defect.
"""

from pathlib import Path

# core/ -> supply_chain_topology_app/
APP_DIR: Path = Path(__file__).resolve().parent.parent

# supply_chain_topology_app/ -> 0_supply_chain_thesis/
REPO_ROOT: Path = APP_DIR.parent

# The ML pipeline: models, databases, raw inputs, and everything predict writes.
PIPELINE_DIR: Path = REPO_ROOT / "prediction_pipeline"
PIPELINE_PROCESSED: Path = PIPELINE_DIR / "data" / "processed"
PIPELINE_RAW: Path = PIPELINE_DIR / "data" / "raw"
PIPELINE_DB: Path = PIPELINE_DIR / "db" / "delivery_predictions.db"

# The canonical CSV predict always writes, and every downstream capability reads.
PREDICT_CSV: Path = PIPELINE_PROCESSED / "daily_delivery_delay_prediction.csv"

# The email capability's OWN artifact. It must never write back over PREDICT_CSV:
# predict's output is delayed-only with no `predict_delay` column, so the email tool's
# "delayed-only" branch wrote its capped subset straight over the canonical file —
# 1,346 rows became 143. That destroyed the input every other capability reads, and it
# only became visible once the path bug was fixed and the write landed on the real file.
EMAIL_ALERTS_CSV: Path = PIPELINE_PROCESSED / "email_alerts.csv"

# App-local output copy (run isolation clears this; it is not the canonical source).
APP_OUTPUT: Path = APP_DIR / "output"
APP_OUTPUT_CSV: Path = APP_OUTPUT / "daily_delivery_delay_prediction.csv"

APP_LOG_DIR: Path = APP_DIR / "log"


def assert_layout() -> None:
    """Fail loudly if the repo does not look the way these constants assume.

    Cheap, and it converts a future folder rename from "silently reads the wrong file"
    into "refuses to start".
    """
    problems = []
    if not PIPELINE_DIR.is_dir():
        problems.append(f"PIPELINE_DIR does not exist: {PIPELINE_DIR}")
    if not (REPO_ROOT / "pyproject.toml").is_file():
        problems.append(f"REPO_ROOT has no pyproject.toml: {REPO_ROOT}")
    if not (APP_DIR / "config" / "prompts").is_dir():
        problems.append(f"APP_DIR has no config/prompts: {APP_DIR}")
    if problems:
        raise RuntimeError(
            "Project layout is not what core/paths.py assumes:\n  "
            + "\n  ".join(problems)
            + "\nIf a folder was renamed, fix core/paths.py rather than adding a "
              "fallback — a fallback is how the email tool ended up reading stale data.")


assert_layout()
