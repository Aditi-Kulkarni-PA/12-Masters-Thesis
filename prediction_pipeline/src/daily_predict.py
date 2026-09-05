"""
Daily Delivery Delay Prediction Pipeline

Replicates the notebook's data extraction → processing → feature engineering →
two-stage prediction pipeline for new (daily) data, and persists results to
CSV and SQLite.

Usage (standalone)::

    python -m src.daily_predict
    python -m src.daily_predict --file ./data/raw/new_orders.csv
    python -m src.daily_predict --file ./data/raw/new_orders.csv --if-exists append

Usage (from agent / other code)::

    from src.daily_predict import DailyPredictionPipeline
    result = DailyPredictionPipeline.run()
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, validate_call

# ---------------------------------------------------------------------------
# Resolve project root so imports from ``src`` and ``model`` work regardless
# of how the script is invoked.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.data_extract_1 import DataExtract  # noqa: E402  (import block continues below)
from src.data_processing_3 import DataProcessing
from src.feature_engineering_4 import FeatureEngineering
from src.model_persistence_9 import ModelPersistence
from src.database_operations_10 import DatabaseOperations, FULL_SEVERITY_LABELS

# ---------------------------------------------------------------------------
# Suppress noisy runtime warnings that are irrelevant during prediction
# ---------------------------------------------------------------------------
warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════════════════════
# Constants — must match the notebook training configuration
# ═══════════════════════════════════════════════════════════════════════════

# Feature column list used during training (pre-encoding)
X_COLS: List[str] = [
    "delivery_partner", "package_type", "vehicle_type", "delivery_mode",
    "region", "weather_condition", "distance_km", "package_weight_kg",
    "weight_x_distance", "km_per_expected_hr", "cost_per_kg",
    "weather_severity", "mode_urgency", "schedule_risk",
    "vehicle_capacity", "vehicle_load_strain",
    "carrier_avg_schedule", "carrier_avg_weight", "region_avg_distance",
]

# Categorical columns (subset of X_COLS) that get one-hot encoded
CAT_COLS: List[str] = [
    "delivery_partner", "package_type", "vehicle_type",
    "delivery_mode", "region", "weather_condition",
]

# Column lists passed to the cleaning pipeline
_CAT_FEATURES: List[str] = [
    "delivery_partner", "package_type", "vehicle_type",
    "delivery_mode", "region", "weather_condition",
    "delivery_status",
]
_NUM_QUANT_FEATURES: List[str] = [
    "distance_km", "package_weight_kg", "delivery_cost", "delivery_rating",
]
_NUM_QUANT_MEDIAN: List[str] = ["distance_km", "package_weight_kg", "delivery_cost"]
_NUM_QUANT_MODE: List[str] = ["delivery_rating"]
_NUM_QUANT_MEAN: List[str] = []
_DATE_FEATURES: List[str] = ["delivery_time_hours", "expected_time_hours"]
_TARGET: List[str] = ["delayed"]

# Post-delivery columns excluded from features (but kept for target derivation)
_POST_DELIVERY_EXCLUDE: List[str] = [
    "delivery_rating", "delivery_time_hrs_num", "delivery_status",
]

# Saved model names
_CLASSIFICATION_MODEL_NAME: str = "best_classification_random_forest"
_SEVERITY_MODEL_NAME: str = "best_severity_random_forest"

# MCP response helpers — columns and constants shared with prediction_server
_MCP_DISPLAY_COLS: List[str] = [
    "delivery_id", "delivery_partner", "package_type",
    "delivery_mode", "region", "weather_condition",
    "distance_km", "package_weight_kg", "predict_severity_label",
    # Derived features — give the agent cross-functional reasoning material.
    # Selected by Random Forest feature importance so llm_insights can
    # reference what actually drove the prediction:
    "vehicle_type",          # Bike / EV / Van / Truck
    "km_per_expected_hr",    # distance / expected_time (schedule tightness) — 27.1% importance
    "mode_urgency",          # Standard=1 … Same Day=4 — 21.5% importance
    "schedule_risk",         # weather_severity × mode_urgency (0–16) — 14.9% importance
    "vehicle_load_strain",   # (weight × distance) / vehicle_capacity — ~10% importance
    "carrier_avg_schedule",  # partner's mean schedule tightness — ~8% importance
    "weather_severity",      # Clear=0 … Stormy=4 — ~7% importance
    "weight_x_distance",     # load-distance burden — ~5% importance
    "cost_per_kg",           # weight-adjusted pricing — ~3% importance
]
_MCP_ADVERSE_WEATHER = {"stormy", "rainy", "foggy"}
_MCP_ENRICH_ROWS: int = int(os.getenv("SC_MCP_ENRICH_ROWS", 50))   # rows sent to agent; set SC_MCP_ENRICH_ROWS in .env


def _mcp_delay_reason(row) -> str:
    """Build a human-readable delay reason from feature values."""
    factors = []
    w = str(row.get("weather_condition", "")).lower()
    if w in _MCP_ADVERSE_WEATHER:
        factors.append(f"{w} weather")
    m = str(row.get("delivery_mode", "")).lower()
    if m in ("express", "same day"):
        factors.append(f"{m} delivery")
    d = row.get("distance_km", 0)
    if d > 100:
        factors.append(f"long distance ({d:.0f} km)")
    elif d > 50:
        factors.append(f"medium distance ({d:.0f} km)")
    wt = row.get("package_weight_kg", 0)
    if wt > 15:
        factors.append(f"heavy package ({wt:.1f} kg)")
    return ", ".join(factors) if factors else "combination of moderate risk factors"


def _mcp_top_n(series, total_delayed: int, n: int = 5) -> list:
    """Return top-N category counts as a list of dicts."""
    if total_delayed == 0:
        return []
    counts = series.value_counts().head(n)
    return [
        {"name": str(name), "count": int(cnt),
         "pct": round(cnt / total_delayed * 100, 1)}
        for name, cnt in counts.items()
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Pydantic configuration model
# ═══════════════════════════════════════════════════════════════════════════


class DailyPredictConfig(BaseModel):
    """Validated configuration for a daily prediction run."""

    file_dir: str = Field(
        default="./data/raw",
        description="Directory containing the input CSV file.",
    )
    file_name: str = Field(
        default="Delivery_Logistics.csv",
        description="Name of the CSV file with new delivery records.",
    )
    model_dir: str = Field(
        default="models",
        description="Directory containing saved model .pkl files.",
    )
    db_path: str = Field(
        default="db/delivery_predictions.db",
        description="Path to the SQLite database.",
    )
    csv_dir: str = Field(
        default="./data/processed",
        description="Directory to write the output CSV.",
    )
    if_exists: Literal["replace", "append"] = Field(
        default="replace",
        description="How to handle existing daily table: 'replace' or 'append'.",
    )
    display: bool = Field(
        default=True,
        description="Whether to print progress messages.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Pipeline class
# ═══════════════════════════════════════════════════════════════════════════



# ---------------------------------------------------------------------------
# Atomic artifact writing + completion signalling
# ---------------------------------------------------------------------------
# Downstream capabilities (diagnose / simulate / recommend / email) may run
# concurrently with prediction. Two failure modes follow from that, and both were
# observed on 22-Aug-26:
#
#   1. Partial read. A plain to_csv leaves the destination readable while it is still
#      being written, so a concurrent reader can consume a half-written file.
#   2. Staggered visibility. Prediction writes the DB tables, then the delayed CSV,
#      then the sidecar. A reader checking "does the CSV exist?" between those steps
#      sees a system that is partly updated, and computes on it without error.
#
# _atomic_write fixes (1) by writing to a temp file in the same directory and calling
# os.replace, which is atomic on POSIX. PREDICT_COMPLETE_MARKER fixes (2) by giving
# downstream a single flag that appears only once every artifact is in place.

PREDICT_COMPLETE_MARKER = "daily_predict_complete.json"


def _atomic_write(dest_path: str, write_fn) -> None:
    """Write via *write_fn(tmp_path)* then atomically move into place.

    The temp file is created in the destination's own directory so os.replace stays
    within one filesystem, which is what makes it atomic.
    """
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
    try:
        write_fn(str(tmp))
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _write_completion_marker(out_dir, **fields) -> str:
    """Record that prediction finished and all its artifacts are readable."""
    from datetime import datetime, timezone
    marker = Path(out_dir) / PREDICT_COMPLETE_MARKER
    payload = {"completed_at": datetime.now(timezone.utc).isoformat(), **fields}
    _atomic_write(str(marker), lambda p: Path(p).write_text(json.dumps(payload)))
    return str(marker)


class DailyPredictionPipeline:
    """End-to-end daily prediction: extract → clean → engineer → predict → persist."""

    # ------------------------------------------------------------------ #
    #  1. DATA EXTRACTION                                                 #
    # ------------------------------------------------------------------ #

    @staticmethod
    @validate_call
    def extract(file_path: str, display: bool = True) -> pd.DataFrame:
        """Read raw CSV and return a DataFrame."""
        df, _ = DataExtract.data_extract_pipeline(
            file_path,
            show_shape=display,
            show_head=display,
            show_info=display,
            show_null_info=display,
            head_n=3,
        )
        return df

    # ------------------------------------------------------------------ #
    #  2. DATA CLEANING                                                   #
    # ------------------------------------------------------------------ #

    @staticmethod
    @validate_call(config=dict(arbitrary_types_allowed=True))
    def clean(df: pd.DataFrame, display: bool = True) -> pd.DataFrame:
        """Run the full cleaning pipeline (standardise, missing-value handling, date fields)."""
        # Work on copies of the mutable lists so we don't persist changes
        cat_features = list(_CAT_FEATURES)
        num_quant_features = list(_NUM_QUANT_FEATURES)

        df_cleaned, _summary, _new_cols = DataProcessing.clean_data_pipeline(
            df,
            standardize_names=True,
            handle_missing=True,
            columns_to_drop=[],
            cat_features=cat_features,
            num_quant_median=list(_NUM_QUANT_MEDIAN),
            num_quant_mean=list(_NUM_QUANT_MEAN),
            num_quant_mode=list(_NUM_QUANT_MODE),
            target_variable=list(_TARGET),
            date_features=list(_DATE_FEATURES),
            num_quant_features=num_quant_features,
            display=display,
            allow_row_drop=False,  # Daily predictions: impute instead of dropping rows
        )

        # Strip post-delivery columns from feature lists (same as notebook)
        num_quant_features[:] = [
            c for c in num_quant_features if c not in _POST_DELIVERY_EXCLUDE
        ]
        cat_features[:] = [c for c in cat_features if c not in _POST_DELIVERY_EXCLUDE]

        if display:
            print(
                f"\n✓ Cleaned: {len(df_cleaned)} rows, "
                f"{df_cleaned['delivery_id'].nunique()} unique delivery IDs"
            )
        return df_cleaned

    # ------------------------------------------------------------------ #
    #  3. FEATURE ENGINEERING (pre-encoding)                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    @validate_call(config=dict(arbitrary_types_allowed=True))
    def engineer_features(df_cleaned: pd.DataFrame, display: bool = True) -> pd.DataFrame:
        """Create derived features (interaction, ordinal, group-aggregates)."""
        df_eng = FeatureEngineering.feature_eng_pre_split_pipeline(
            df_cleaned,
            slack_time_column="slack_time",
            delay_hours_column="delay_hours",
            delayed_column="delayed",
            display=display,
        )
        return df_eng

    # ------------------------------------------------------------------ #
    #  4. ENCODE (one-hot, no scaling — matches tree-based model input)   #
    # ------------------------------------------------------------------ #

    @staticmethod
    @validate_call(config=dict(arbitrary_types_allowed=True))
    def encode(
        X_daily: pd.DataFrame,
        expected_columns: List[str],
        display: bool = True,
    ) -> pd.DataFrame:
        """One-hot encode categorical columns and align to the training column order.

        Parameters
        ----------
        X_daily : Feature DataFrame with X_COLS columns (including categoricals).
        expected_columns : The exact column list the model was trained on.
        """
        X_encoded = pd.get_dummies(
            X_daily,
            columns=CAT_COLS,
            drop_first=True,
            prefix_sep="_",
            dtype=int,
        )

        # Standardise column names (lowercase + underscores) to match training
        X_encoded.columns = (
            X_encoded.columns
            .str.strip()
            .str.lower()
            .str.replace(r"[^a-z0-9_]", "_", regex=True)
            .str.replace(r"_+", "_", regex=True)
            .str.strip("_")
        )

        # Align to the model's expected column order (add missing, drop extra)
        X_aligned = X_encoded.reindex(columns=expected_columns, fill_value=0)

        if display:
            missing = set(expected_columns) - set(X_encoded.columns)
            extra = set(X_encoded.columns) - set(expected_columns)
            if missing:
                print(f"  ⚠ Columns missing in daily data (filled with 0): {sorted(missing)}")
            if extra:
                print(f"  ⚠ Extra columns in daily data (dropped): {sorted(extra)}")
            print(f"✓ Encoded: {X_aligned.shape[1]} columns aligned to model training schema")

        return X_aligned

    # ------------------------------------------------------------------ #
    #  5. TWO-STAGE PREDICTION                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    @validate_call(config=dict(arbitrary_types_allowed=True))
    def predict(
        X_encoded: pd.DataFrame,
        classification_model: object,
        severity_model: object,
        display: bool = True,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Run the two-stage pipeline: delay classification → severity.

        Returns
        -------
        pred_delay : 1-D int array (0 or 1)
        pred_severity : 1-D int array (0=No Delay, 1=Short, 2=Medium, 3=Long)
        """
        # Stage 1: binary delay prediction
        pred_delay = classification_model.predict(X_encoded)
        delayed_mask = pred_delay == 1

        # Stage 2: severity (only for predicted-delayed rows, +1 shift)
        pred_severity = np.zeros(len(X_encoded), dtype=int)
        if delayed_mask.any():
            raw_severity = severity_model.predict(X_encoded[delayed_mask])
            pred_severity[delayed_mask] = raw_severity + 1  # 0→1, 1→2, 2→3

        if display:
            print(
                f"✓ Stage 1 — {int(pred_delay.sum())}/{len(pred_delay)} "
                f"predicted delayed ({pred_delay.mean()*100:.1f}%)"
            )
            sev_dist = dict(zip(*np.unique(pred_severity, return_counts=True)))
            print(f"✓ Stage 2 — severity distribution: {sev_dist}")

        return np.asarray(pred_delay), pred_severity

    # ------------------------------------------------------------------ #
    #  6. MERGE + PERSIST                                                 #
    # ------------------------------------------------------------------ #

    @staticmethod
    @validate_call(config=dict(arbitrary_types_allowed=True))
    def build_output(
        X_original: pd.DataFrame,
        delivery_ids: pd.Series,
        pred_delay: np.ndarray,
        pred_severity: np.ndarray,
        severity_labels: Optional[Dict[int, str]] = None,
    ) -> pd.DataFrame:
        """Merge predictions with the original (pre-encoding) feature set.

        Returns a DataFrame named ``daily_delivery_delay_prediction``.
        """
        if severity_labels is None:
            severity_labels = FULL_SEVERITY_LABELS

        out = X_original.copy()
        out.insert(0, "delivery_id", delivery_ids.values)
        out["predict_delay"] = np.asarray(pred_delay).ravel()
        out["predict_severity"] = np.asarray(pred_severity).ravel()
        out["predict_severity_label"] = out["predict_severity"].map(severity_labels)
        return out

    # ------------------------------------------------------------------ #
    #  7. MCP RESPONSE BUILDER                                           #
    # ------------------------------------------------------------------ #

    @classmethod
    def get_prediction(cls, file_path: str, csv_dir: str = "") -> str:
        """Run the pipeline and return a JSON string ready for the MCP tool.

        Contains all post-pipeline logic (severity counts, delay_reason,
        top-N groupings, delayed-only CSV) so the MCP server stays a thin
        wrapper.

        Returns a JSON string with two keys:
          - "summary": aggregate stats
          - "delayed_orders": ALL delayed rows (no cap) so the agent can
                              enrich delay_reason for every record in the CSV
        """
        effective_csv_dir = csv_dir or str(_PROJECT_ROOT / "data" / "processed")

        result = cls.run_from_file(file_path, csv_dir=effective_csv_dir, display=False)

        df = result["df_predictions"]
        delayed = df[df["predict_delay"] == 1].copy()
        total_orders = len(df)
        total_delayed = len(delayed)

        severity_short  = int((delayed["predict_severity"] == 1).sum()) if total_delayed else 0
        severity_medium = int((delayed["predict_severity"] == 2).sum()) if total_delayed else 0
        severity_long   = int((delayed["predict_severity"] == 3).sum()) if total_delayed else 0

        delayed["delay_reason"] = delayed.apply(_mcp_delay_reason, axis=1)
        delayed["llm_insights"] = ""  # filled by the predict agent
        # Round high-precision derived features to 2 decimal places for display/agent
        for _col in ("vehicle_load_strain", "km_per_expected_hr"):
            if _col in delayed.columns:
                delayed[_col] = delayed[_col].round(2)
        display_cols = [c for c in _MCP_DISPLAY_COLS + ["delay_reason", "llm_insights"] if c in delayed.columns]

        # Save delayed-only CSV so the UI can read it directly.
        #
        # Written atomically (temp file in the same directory, then os.replace) because
        # downstream capabilities may run concurrently with this one. A plain to_csv
        # leaves the path readable while it is still being written, so a concurrent
        # reader can pick up a partial file and produce plausible-but-wrong output with
        # no error anywhere. os.replace is atomic on POSIX, so a reader sees either the
        # previous file or the complete new one, never a half-written one.
        import pathlib as _pl
        out_dir = _pl.Path(effective_csv_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        delayed_csv_path = str(out_dir / "daily_delivery_delay_prediction.csv")
        _atomic_write(delayed_csv_path,
                      lambda p: delayed[display_cols].to_csv(p, index=False))

        top_regions  = _mcp_top_n(delayed["region"], total_delayed)             if "region"            in delayed.columns else []
        top_weather  = _mcp_top_n(delayed["weather_condition"], total_delayed)  if "weather_condition" in delayed.columns else []
        top_partners = _mcp_top_n(delayed["delivery_partner"], total_delayed)   if "delivery_partner"  in delayed.columns else []

        summary = {
            "total_orders":    total_orders,
            "total_delayed":   total_delayed,
            "pct_delayed":     round(total_delayed / total_orders * 100, 1) if total_orders else 0.0,
            "severity_short":  severity_short,
            "severity_medium": severity_medium,
            "severity_long":   severity_long,
            "delayed_csv_path": delayed_csv_path,
            "showing_top_n":   total_delayed,
            "top_regions":     top_regions,
            "top_weather":     top_weather,
            "top_partners":    top_partners,
            "enrich_rows_cap": _MCP_ENRICH_ROWS,
        }

        # Send top _MCP_ENRICH_ROWS rows to the agent for llm_insights enrichment.
        # Rows beyond this cap keep the rule-based reason already saved in the CSV.
        # The Gradio app controls how many rows to display (SC_MCP_DISPLAY_ROWS).
        all_records = (
            delayed[display_cols].head(_MCP_ENRICH_ROWS).to_dict(orient="records")
            if total_delayed else []
        )

        # ---- build formatted stats and save to file (app reads directly, bypassing LLM) ----
        def _fmt_top(entries: list) -> str:
            return "\n".join(
                f"- **{e['name']}**: **{e['count']}** orders (**{e['pct']}%** of delayed)"
                for e in entries
            )

        formatted_stats = (
            f"### Delivery Delay Prediction Results\n\n"
            f"**Overview**: Analyzed **{total_orders}** orders -- **{total_delayed}** predicted delayed (**{summary['pct_delayed']}%**)\n\n"
            f"**Severity Breakdown** (delayed orders only):\n"
            f"- **Short (1-2h)**: **{severity_short}** orders (**{round(severity_short / total_orders * 100, 1) if total_orders else 0}%**)\n"
            f"- **Medium (3-5h)**: **{severity_medium}** orders (**{round(severity_medium / total_orders * 100, 1) if total_orders else 0}%**)\n"
            f"- **Long (6+h)**: **{severity_long}** orders (**{round(severity_long / total_orders * 100, 1) if total_orders else 0}%**)\n\n"
            f"**Top Affected Regions** (by delayed orders):\n{_fmt_top(top_regions)}\n\n"
            f"**Top Affected Weather Conditions**:\n{_fmt_top(top_weather)}\n\n"
            f"**Top Affected Delivery Partners**:\n{_fmt_top(top_partners)}\n\n"
            f"**Note**: Delayed-only results saved to: {delayed_csv_path}. "
            f"Top {summary['showing_top_n']} delayed rows shown in the table below."
        )
        # ---- save sidecar JSON so delivery_app can read summary/stats directly ----
        sidecar_path = str(out_dir / "daily_delivery_delay_prediction_meta.json")
        _atomic_write(
            sidecar_path,
            lambda p: _pl.Path(p).write_text(
                json.dumps({"summary": summary, "formatted_stats": formatted_stats})
            ),
        )

        # ---- completion marker: written LAST, after every artifact is in place ----
        #
        # Downstream capabilities previously decided "has prediction run?" by testing
        # whether individual artifacts existed. Those artifacts appear at different
        # points during this method, so a concurrent reader could find one present and
        # another still missing, and would then compute on incomplete state. This marker
        # is the single, atomic "prediction is complete" signal: it exists only once the
        # DB tables, the delayed CSV and the sidecar have all been written.
        _write_completion_marker(out_dir, total_orders=total_orders,
                                 total_delayed=total_delayed,
                                 delayed_csv_path=delayed_csv_path)

        return json.dumps({"summary": summary, "formatted_stats": formatted_stats, "delayed_orders": all_records})

    # ------------------------------------------------------------------ #
    #  8. ORCHESTRATOR — run the full pipeline                            #
    # ------------------------------------------------------------------ #

    @classmethod
    def _resolve_env(cls, env_var: str, fallback: str) -> str:
        """Return env-var value, resolving relative paths against workspace root.

        .env paths are relative to the project root (0_supply_chain_thesis/), which is
        _PROJECT_ROOT.parent (src/ → prediction_pipeline/ → 0_supply_chain_thesis/).
        """
        val = os.getenv(env_var, "")
        if val:
            p = Path(val)
            return str(p if p.is_absolute() else (_PROJECT_ROOT.parent / p).resolve())
        return fallback

    @classmethod
    def run_from_file(
        cls,
        file_path: str,
        csv_dir: str = "",
        if_exists: Literal["replace", "append"] = "replace",
        display: bool = True,
    ) -> Dict[str, object]:
        """Convenience entry point: run the pipeline for a single CSV file.

        Resolves model_dir, db_path from environment variables
        (SC_PREDICTION_MODEL_DIR, SC_PREDICTION_DB_PATH) with sensible
        defaults relative to the prediction_pipeline project root.

        Parameters
        ----------
        file_path : Absolute or relative path to the input CSV.
        csv_dir   : Output directory for prediction CSV. If empty, uses
                     the default from DailyPredictConfig.
        if_exists : 'replace' or 'append' for the daily DB table.
        display   : Whether to print progress messages. Set False when
                    called from MCP server to avoid stderr pipe deadlock.
        """
        fp = Path(file_path)
        overrides: dict = {
            "file_dir": str(fp.parent),
            "file_name": fp.name,
            "model_dir": cls._resolve_env(
                "SC_PREDICTION_MODEL_DIR", str(_PROJECT_ROOT / "models")
            ),
            "db_path": cls._resolve_env(
                "SC_PREDICTION_DB_PATH",
                str(_PROJECT_ROOT / "db" / "delivery_predictions.db"),
            ),
            "if_exists": if_exists,
            "display": display,
        }
        if csv_dir:
            overrides["csv_dir"] = csv_dir
        return cls.run(**overrides)

    @classmethod
    def run(
        cls,
        config: Optional[DailyPredictConfig] = None,
        **overrides,
    ) -> Dict[str, object]:
        """Execute the complete daily prediction pipeline.

        Parameters
        ----------
        config : Validated configuration. If *None*, a default is created.
        **overrides : Any ``DailyPredictConfig`` field can be passed as a
            keyword argument to override the default (e.g. ``file_name="x.csv"``).

        Returns
        -------
        dict with keys:
            df_predictions  — the output DataFrame
            csv_path        — path to the saved CSV
            db_result       — dict returned by DatabaseOperations.refresh_daily
        """
        if config is None:
            config = DailyPredictConfig(**overrides)
        display = config.display

        file_path = os.path.join(config.file_dir, config.file_name)
        if display:
            print("=" * 80)
            print("DAILY DELIVERY DELAY PREDICTION PIPELINE")
            print("=" * 80)
            print(f"  Input file : {file_path}")
            print(f"  Model dir  : {config.model_dir}")
            print(f"  DB path    : {config.db_path}")
            print(f"  if_exists  : {config.if_exists}")
            print("=" * 80)

        # ── 1. Extract ─────────────────────────────────────────────────
        if display:
            print("\n▸ Step 1: Data Extraction")
        df_raw = cls.extract(file_path, display=display)

        # ── 2. Clean ──────────────────────────────────────────────────
        if display:
            print("\n▸ Step 2: Data Cleaning")
        df_cleaned = cls.clean(df_raw, display=display)

        # ── 3. Feature Engineering ────────────────────────────────────
        if display:
            print("\n▸ Step 3: Feature Engineering (pre-encoding)")
        df_eng = cls.engineer_features(df_cleaned, display=display)

        # ── 4. Select features & retain delivery_id ───────────────────
        delivery_ids = df_eng["delivery_id"].reset_index(drop=True)
        X_daily = df_eng[X_COLS].reset_index(drop=True)
        if display:
            print(f"\n✓ Selected {len(X_COLS)} feature columns, "
                  f"{len(delivery_ids)} records")

        # ── 5. Load models ────────────────────────────────────────────
        if display:
            print("\n▸ Step 4: Loading saved models")

        classification_model = ModelPersistence.load_model(
            _CLASSIFICATION_MODEL_NAME, model_dir=config.model_dir,
        )
        severity_model = ModelPersistence.load_model(
            _SEVERITY_MODEL_NAME, model_dir=config.model_dir,
        )

        # Determine expected columns from the trained model
        expected_columns: List[str] = list(classification_model.feature_names_in_)

        # ── 6. Encode ────────────────────────────────────────────────
        if display:
            print("\n▸ Step 5: One-hot Encoding (no scaling)")
        X_encoded = cls.encode(X_daily, expected_columns=expected_columns, display=display)

        # ── 7. Predict ───────────────────────────────────────────────
        if display:
            print("\n▸ Step 6: Two-stage Prediction")
        pred_delay, pred_severity = cls.predict(
            X_encoded, classification_model, severity_model, display=display,
        )

        # ── 8. Build output DataFrame ────────────────────────────────
        if display:
            print("\n▸ Step 7: Building output DataFrame")
        df_predictions = cls.build_output(
            X_original=X_daily,
            delivery_ids=delivery_ids,
            pred_delay=pred_delay,
            pred_severity=pred_severity,
        )
        # ── 9. Save CSV ─────────────────────────────────────────────
        if display:
            print("\n▸ Step 8: Saving CSV")
        os.makedirs(config.csv_dir, exist_ok=True)
        csv_path = os.path.join(config.csv_dir, "daily_delivery_delay_prediction.csv")
        if config.if_exists == "append" and os.path.exists(csv_path):
            df_predictions.to_csv(csv_path, mode="a", header=False, index=False)
        else:
            df_predictions.to_csv(csv_path, index=False)
        if display:
            print(f"✓ CSV saved → {csv_path} ({len(df_predictions)} rows)")

        # ── 10. Save to DB + rebuild summaries + metadata ────────────
        if display:
            print("\n▸ Step 9: Saving to database & rebuilding summaries")
        db_result = DatabaseOperations.refresh_daily(
            X_daily=X_daily,
            daily_ids=delivery_ids,
            pred_delay=pred_delay,
            pred_severity=pred_severity,
            db_path=config.db_path,
            if_exists=config.if_exists,
        )

        if display:
            print("\n" + "=" * 80)
            print("✓ DAILY PREDICTION PIPELINE COMPLETE")
            print("=" * 80)
            print(f"  Records processed : {len(df_predictions)}")
            print(f"  Predicted delayed : {int(pred_delay.sum())} "
                  f"({pred_delay.mean()*100:.1f}%)")
            print(f"  CSV               : {csv_path}")
            print(f"  Database          : {config.db_path}")
            print(f"  Summary tables    : {len(db_result.get('summary_tables', []))}")
            print("=" * 80)

        return {
            "df_predictions": df_predictions,
            "df_engineered": df_eng,
            "csv_path": csv_path,
            "db_result": db_result,
        }


# ═══════════════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the daily delivery delay prediction pipeline.",
    )
    parser.add_argument(
        "--file-dir", default="./data/raw",
        help="Directory containing the input CSV (default: ./data/raw)",
    )
    parser.add_argument(
        "--file-name", default="Delivery_Logistics.csv",
        help="CSV file name (default: Delivery_Logistics.csv)",
    )
    parser.add_argument(
        "--model-dir", default="models",
        help="Model directory (default: models)",
    )
    parser.add_argument(
        "--db-path", default="db/delivery_predictions.db",
        help="SQLite database path (default: db/delivery_predictions.db)",
    )
    parser.add_argument(
        "--csv-dir", default="./data/processed",
        help="Output CSV directory (default: ./data/processed)",
    )
    parser.add_argument(
        "--if-exists", choices=["replace", "append"], default="replace",
        help="How to handle existing daily table (default: replace)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress progress output",
    )
    args = parser.parse_args()

    cfg = DailyPredictConfig(
        file_dir=args.file_dir,
        file_name=args.file_name,
        model_dir=args.model_dir,
        db_path=args.db_path,
        csv_dir=args.csv_dir,
        if_exists=args.if_exists,
        display=not args.quiet,
    )

    DailyPredictionPipeline.run(config=cfg)
