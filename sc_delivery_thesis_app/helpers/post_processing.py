"""
Deterministic post-processing helpers for MasterOutput → UI display.

Each function takes structured agent output (Pydantic rows / summary text)
and returns display-ready objects (DataFrames, markdown strings, file paths).
Keeping these out of the Gradio handler keeps the handler readable and
ensures counts, formatting, and data transformations are never LLM-dependent.

Formatting contract (where display formatting happens):
  - predict / simulate / recommendation / email tabs: final markdown is BUILT
    HERE from structured rows; the agent's narrative text is appended after
    any duplicate heading/counts are stripped.
  - diagnosis tab: the agent's diagnosis_summary markdown is shown as-is
    (only the two tables are built here).
"""

import json as _json
import re
from pathlib import Path
from typing import Optional

import pandas as pd

# Pipeline's processed directory (source of truth for predict outputs)
_PIPELINE_PROCESSED = (Path(__file__).resolve().parent.parent.parent
                       / "prediction_pipeline" / "data" / "processed")


def _ensure_output_dir(app_dir: Path) -> Path:
    """Return app_dir/output, creating it if needed."""
    output_dir = app_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _strip_leading_heading(text: str) -> str:
    """Remove a leading '#'/'##'/'###' heading line from agent narrative text.

    Each processor supplies its own canonical heading, so a duplicate heading
    written by the agent is dropped."""
    return re.sub(r"^###?\s+.*\n+", "", (text or "").strip()).strip()


# ---------------------------------------------------------------------------
# Predict post-processing
# ---------------------------------------------------------------------------

def _norm_id(s) -> str:
    """Normalize delivery_id to integer string (e.g. '10792.0' -> '10792')."""
    s = str(s).strip()
    try:
        # Convert float string → float → int → string to drop .0 suffix
        # Handles cases where CSV readers interpret '10792' as 10792.0
        return str(int(float(s)))
    except (ValueError, OverflowError):
        # Non-numeric or out-of-range IDs: return as-is
        return s


def process_predict(
    predict_summary: str,
    predict_rows: list,
    app_dir: Path,
    display_rows: int,
) -> tuple[str, pd.DataFrame, str]:
    """Return (predict_text, predict_df, predict_csv_path).

    Reads the prediction CSV + sidecar, merges LLM insights, and caps
    the displayed table to *display_rows*.
    """
    # Start with agent's summary text; prepend canonical heading if agent didn't provide one
    predict_text = predict_summary or ""
    if predict_text and not predict_text.lstrip().startswith("#"):
        predict_text = "### Cross-Dimensional Delay Insights\n\n" + predict_text

    predict_df = pd.DataFrame()
    predict_csv_path = None

    # Load sidecar metadata written by the prediction pipeline
    sidecar_dir = _PIPELINE_PROCESSED
    sidecar_path = sidecar_dir / "daily_delivery_delay_prediction_meta.json"
    sidecar: dict = {}
    if sidecar_path.is_file():
        sidecar = _json.loads(sidecar_path.read_text())

    # Append formatted stats (feature importance, confusion matrix) to narrative
    fmt_stats = sidecar.get("formatted_stats", "")
    if fmt_stats:
        predict_text = predict_text.rstrip() + "\n\n" + fmt_stats

    # Determine CSV path: use sidecar's recorded path, or fallback to standard location
    csv_path = (
        sidecar.get("summary", {}).get("delayed_csv_path", "")
        or str(sidecar_dir / "daily_delivery_delay_prediction.csv")
    )
    rows = predict_rows or []

    # --- MAIN PROCESSING: Read CSV, merge LLM insights, copy to app/output ---
    if csv_path and Path(csv_path).is_file():
        predict_df = pd.read_csv(csv_path)
        predict_csv_path = csv_path
        
        # Merge agent-provided llm_insights into the CSV by delivery_id
        if rows:
            # Build lookup dict: normalized_id → llm_insights text
            enriched = {
                _norm_id(r.delivery_id): r.llm_insights
                for r in rows if r.llm_insights
            }
            if enriched:
                # Create normalized ID column for matching (handles 10792.0 vs "10792")
                predict_df["_nid"] = predict_df["delivery_id"].astype(str).map(_norm_id)
                predict_df["llm_insights"] = predict_df["_nid"].map(enriched).fillna("")
                predict_df.drop(columns=["_nid"], inplace=True)
                # Write enriched data back to source CSV for persistence
                predict_df.to_csv(csv_path, index=False)
        
        # Copy enriched CSV and metadata to app/output/ so downstream tools find them
        # (simulate, diagnose, recommend all read from this location)
        output_dir = _ensure_output_dir(app_dir)
        app_csv = output_dir / "daily_delivery_delay_prediction.csv"
        predict_df.to_csv(str(app_csv), index=False)
        predict_csv_path = str(app_csv)
        if sidecar:
            app_meta = output_dir / "daily_delivery_delay_prediction_meta.json"
            app_meta.write_text(_json.dumps(sidecar, indent=2))
        
        # Cap displayed DataFrame to display_rows for UI performance
        predict_df = predict_df.head(display_rows)
    
    # Fallback: No CSV exists, build DataFrame from agent rows directly
    elif rows:
        predict_df = pd.DataFrame([r.model_dump() for r in rows])
        output_dir = _ensure_output_dir(app_dir)
        predict_csv_path = str(output_dir / "daily_delivery_delay_prediction.csv")
        predict_df.to_csv(predict_csv_path, index=False)

    return predict_text, predict_df, predict_csv_path


# ---------------------------------------------------------------------------
# Simulation post-processing
# ---------------------------------------------------------------------------

# Column set + order for the simulate table and CSV — mirrors the
# SimulateDelays Pydantic model so the UI looks identical whether the data
# came from the pipeline CSV or from agent-transcribed rows.
_SIM_DISPLAY_COLS = [
    "delivery_id", "delivery_partner", "delivery_mode", "region",
    "weather_condition", "vehicle_type", "distance_km",
    "original_severity", "simulated_severity", "simulate_delay_reason",
]

def process_simulate(
    simulate_summary: str,
    simulate_rows: list,
    app_dir: Path,
    run_start_ts: float | None = None,
    display_rows: int = 50,
) -> tuple[str, pd.DataFrame, str]:
    """Return (simulate_text, simulate_df, simulate_csv_path).

    Source of truth: the simulation CSV written by the pipeline tool during
    THIS run (mtime >= run_start_ts). The agent only transcribes a capped
    sample of rows, so the CSV is authoritative for counts and distributions;
    the agent's per-row simulate_delay_reason enrichment is merged in by
    delivery_id. Falls back to the agent rows if no fresh CSV exists.

    Builds an accurate overview header from the full data (correct count,
    conditions, severity distribution) and appends the agent's qualitative
    narrative with any duplicate heading/counts stripped. The returned
    DataFrame is capped to *display_rows* for the UI table.
    """
    # --- Load full results from the pipeline CSV if it was written this run ---
    sim_source = _PIPELINE_PROCESSED / "simulation_delivery_delays.csv"
    simulate_df = None
    
    # Check if CSV exists AND was modified after this agent run started
    # (ensures we're reading output from THIS simulation, not a stale one)
    if (run_start_ts is not None and sim_source.is_file()
            and sim_source.stat().st_mtime >= run_start_ts):
        df = pd.read_csv(sim_source)
        if not df.empty:
            simulate_df = df
            
            # Merge agent's per-row reasoning into full results by delivery_id
            reasons = {
                _norm_id(r.delivery_id): r.simulate_delay_reason
                for r in (simulate_rows or [])
                if getattr(r, "simulate_delay_reason", None)
            }
            if reasons and "delivery_id" in simulate_df.columns:
                simulate_df["simulate_delay_reason"] = (
                    simulate_df["delivery_id"].astype(str).map(_norm_id).map(reasons).fillna("")
                )
            
            # Pipeline CSV contains ALL prediction columns plus severity pair appended
            # Trim/reorder to display schema: only the fields in _SIM_DISPLAY_COLS
            # This ensures original_severity and simulated_severity sit side by side
            # in the table (matching the SimulateDelays Pydantic model structure)
            cols = [c for c in _SIM_DISPLAY_COLS if c in simulate_df.columns]
            if cols:
                simulate_df = simulate_df[cols]

    # Fallback: No fresh CSV exists, build DataFrame from agent rows
    if simulate_df is None:
        if not simulate_rows:
            return simulate_summary or "", pd.DataFrame(), None
        simulate_df = pd.DataFrame([r.model_dump() for r in simulate_rows])

    # Write final DataFrame to app/output for download link
    output_dir = _ensure_output_dir(app_dir)
    simulate_csv_path = str(output_dir / "simulate_delays_latest.csv")
    simulate_df.to_csv(simulate_csv_path, index=False)

    # === Build accurate overview header from actual data (not agent's estimate) ===
    n_sim = len(simulate_df)
    conds = []  # Collect scenario parameters detected in the data
    for col, label in [("weather_condition", "weather"), ("region", "region"), ("vehicle_type", "vehicle")]:
        if col in simulate_df.columns:
            vals = simulate_df[col].dropna().unique().tolist()
            if vals:
                conds.append(f"{label}: {', '.join(str(v) for v in vals)}")

    overview = f"### Delivery Delay Simulation Results\n\nAnalyzed **{n_sim}** simulated orders"
    if conds:
        overview += f" ({'; '.join(conds)})"
    overview += ".\n"

    # Add severity distribution breakdown from actual data
    if "simulated_severity" in simulate_df.columns:
        sev = simulate_df["simulated_severity"].value_counts()
        overview += "\n**Simulated Severity Distribution:**\n"
        for s, c in sev.items():
            overview += f"- {s}: {c} orders ({c / n_sim * 100:.1f}%)\n"

    # Append agent's qualitative narrative (strip duplicate heading/count)
    # Remove lines like "Analyzed 50 simulated orders" since we build that above
    narr = _strip_leading_heading(simulate_summary)
    narr = re.sub(r"Analyzed \d+ simulated.*?\.\s*", "", narr).strip()
    simulate_text = overview + ("\n" + narr if narr else "")

    # Cap displayed DataFrame to display_rows for UI performance
    return simulate_text, simulate_df.head(display_rows), simulate_csv_path


# ---------------------------------------------------------------------------
# Diagnosis post-processing
# ---------------------------------------------------------------------------

def process_diagnosis(
    diagnosis_high_risk_rows: list,
    diagnosis_comparison_rows: list,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (diagnosis_high_risk_df, diagnosis_comparison_df)."""
    # Convert Pydantic rows to DataFrames; agent's diagnosis_summary text is
    # shown as-is in the diagnosis tab (these tables are embedded within it)
    hr_df = (
        pd.DataFrame([r.model_dump() for r in diagnosis_high_risk_rows])
        if diagnosis_high_risk_rows else pd.DataFrame()
    )
    comp_df = (
        pd.DataFrame([r.model_dump() for r in diagnosis_comparison_rows])
        if diagnosis_comparison_rows else pd.DataFrame()
    )
    return hr_df, comp_df


# ---------------------------------------------------------------------------
# Recommendation post-processing
# ---------------------------------------------------------------------------

_CAT_HEADERS = {
    "quick-win": "**Quick-Win Actions (Immediate)**",
    "short-term": "**Short-term Actions (1-4 weeks)**",
    "long-term": "**Long-term Actions (1-3 months+)**",
}


def process_recommendations(
    recommendation_summary: str,
    recommendation_rows: list,
) -> str:
    """Return fully-rendered recommendation markdown built from structured rows.

    Groups actions by category with Data/SLA fields guaranteed present.
    Falls back to the agent's summary text if no structured rows exist.
    """
    # Fallback: No structured rows extracted, return agent's prose as-is
    if not recommendation_rows:
        return recommendation_summary or ""

    # Group actions into 3 categories by time horizon
    cats: dict[str, list] = {"quick-win": [], "short-term": [], "long-term": []}
    for r in recommendation_rows:
        # Normalize category name (e.g. "Quick Win" → "quick-win")
        key = r.category.lower().replace(" ", "-")
        if key in cats:
            cats[key].append(r)

    # Build final markdown: canonical heading + agent narrative + grouped actions
    parts = ["### Delivery Optimization Recommendations\n"]
    
    # Prepend agent's qualitative narrative (strip duplicate heading if present)
    narr = _strip_leading_heading(recommendation_summary)
    if narr:
        parts.append(narr + "\n")

    # Render each category with its actions in a structured format
    for ck in ["quick-win", "short-term", "long-term"]:
        actions = cats[ck]
        parts.append(f"\n{_CAT_HEADERS[ck]}\n")
        if not actions:
            parts.append("No actions in this category.\n")
        else:
            # Render each action with guaranteed Data and SLA fields present
            for a in actions:
                parts.append(f"- **{a.action}** ({a.dimension}): {a.action_desc}")
                parts.append(f"  - *Data*: {a.supporting_data}")
                parts.append(f"  - *SLA*: {a.sla_reference}\n")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Email post-processing
# ---------------------------------------------------------------------------

def process_emails(
    email_alert_summary: str,
    email_alerts,              # Optional[EmailsList]
    app_dir: Path,
) -> tuple[str, str | None]:
    """Return (email_alert_text, email_csv_path)."""
    email_text = email_alert_summary or ""

    # If agent returned structured email objects, render them with separators
    if email_alerts and getattr(email_alerts, "content", None):
        _SEP = '\n\n<hr style="border: none; border-top: 1px solid #ccc; margin: 16px 0;">\n\n'
        email_text = _SEP.join(
            f"**To:** {e.email_id}\n\n{e.email_content}"
            for e in email_alerts.content
        )
    # Fallback message if no delayed orders found
    if not email_text:
        email_text = "No delayed orders found; no email alerts generated."

    # === Build email-specific CSV: rows with email_content populated ===
    email_csv_path = None
    
    # Try two possible source locations (app output first, then pipeline processed)
    predict_csv = app_dir / "output" / "daily_delivery_delay_prediction.csv"
    pipeline_csv = (app_dir.parent.parent / "prediction_pipeline"
                    / "data" / "processed" / "daily_delivery_delay_prediction.csv")
    src_csv = pipeline_csv if pipeline_csv.is_file() else (predict_csv if predict_csv.is_file() else None)
    
    if src_csv:
        try:
            df = pd.read_csv(src_csv)
            
            # Filter strategy: prefer rows with email_content, fallback to predict_delay=1
            if "email_content" in df.columns:
                # Keep only rows where email content was actually written
                email_df = df[df["email_content"].notna() & (df["email_content"] != "")].copy()
            elif "predict_delay" in df.columns:
                # No email_content column: assume all delayed orders got emails
                email_df = df[df["predict_delay"] == 1].copy()
            else:
                # Fallback: no filtering possible, export all rows
                email_df = df.copy()
            
            # Write filtered email data to output directory
            out_path = _ensure_output_dir(app_dir) / "email_alerts.csv"
            email_df.to_csv(out_path, index=False)
            email_csv_path = str(out_path)
        except Exception:
            # CSV read/processing failed: skip CSV generation
            pass

    return email_text, email_csv_path
