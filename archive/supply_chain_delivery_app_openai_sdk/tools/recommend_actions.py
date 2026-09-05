"""
Recommend delivery optimization actions based on prediction and diagnosis data.

Reads daily + historical summary tables from the prediction DB and the
delayed-orders CSV to produce data-driven recommendations the LLM agent
can enrich with expert reasoning.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path

import pandas as pd
from agents import function_tool

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BASE = Path(__file__).resolve().parent.parent.parent  # 0_supply_chain_capstone
_DB_PATH = _BASE / "prediction_pipeline" / "db" / "delivery_predictions.db"
_CSV_DIR = _BASE / "prediction_pipeline" / "data" / "processed"
_DAILY_CSV = _CSV_DIR / "daily_delivery_delay_prediction.csv"
_SIDECAR = _CSV_DIR / "daily_delivery_delay_prediction_meta.json"

_CAT_DIMS = [
    "delivery_mode", "weather_condition", "region",
    "vehicle_type", "delivery_partner", "package_type",
]

_LOGGER = logging.getLogger("supply_chain_delivery_app")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _query_df(conn: sqlite3.Connection, sql: str) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn)


def _overall(conn, prefix: str) -> dict:
    """Read *_summary_overall into a {stat_id: stat_value} dict."""
    rows = conn.execute(
        f"SELECT stat_id, stat_value FROM {prefix}summary_overall"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _top_dims(conn, prefix: str, dim: str, metric: str = "delay_rate",
              n: int = 3) -> list[dict]:
    """Top-n rows from a single-dimension summary table, sorted by *metric* desc."""
    table = f"{prefix}summary_by_{dim}"
    try:
        df = _query_df(conn, f"SELECT * FROM {table} ORDER BY {metric} DESC LIMIT {n}")
    except Exception:
        return []
    return df.to_dict(orient="records")


def _compare_dim(conn, dim: str) -> list[dict]:
    """Compare daily vs hist delay_rate for every value of *dim*.
    Return rows where the daily rate differs notably from historical."""
    table_d = f"daily_summary_by_{dim}"
    table_h = f"hist_summary_by_{dim}"
    try:
        sql = f"""
        SELECT d.{dim},
               d.delay_rate  AS daily_delay_rate,
               h.delay_rate  AS hist_delay_rate,
               d.delayed_count AS daily_delayed,
               d.total_deliveries AS daily_total,
               ROUND(d.delay_rate - h.delay_rate, 4) AS rate_delta
        FROM {table_d} d
        JOIN {table_h} h ON d.{dim} = h.{dim}
        ORDER BY rate_delta DESC
        """
        df = _query_df(conn, sql)
    except Exception:
        return []
    return df.to_dict(orient="records")


def _high_risk(conn, prefix: str, n: int = 10) -> list[dict]:
    table = f"{prefix}summary_high_risk_patterns"
    try:
        df = _query_df(conn, f"SELECT * FROM {table} ORDER BY delay_rate DESC LIMIT {n}")
    except Exception:
        return []
    return df.to_dict(orient="records")


def _daily_severity_hotspots(csv_path: Path, n: int = 5) -> list[dict]:
    """Find the top dimension combos with the most Long-severity orders today."""
    if not csv_path.exists():
        return []
    df = pd.read_csv(csv_path)
    if df.empty:
        return []
    longs = df[df["predict_severity_label"].str.lower().str.contains("long", na=False)]
    if longs.empty:
        return []
    grouped = (
        longs.groupby(["delivery_mode", "region", "weather_condition"])
        .agg(long_count=("delivery_id", "count"),
             avg_distance=("distance_km", "mean"))
        .reset_index()
        .sort_values("long_count", ascending=False)
        .head(n)
    )
    grouped["avg_distance"] = grouped["avg_distance"].round(1)
    return grouped.to_dict(orient="records")


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

@function_tool
def recommend_actions() -> str:
    """Analyze prediction and diagnosis results to produce data-driven
    delivery optimization recommendations (long-term, short-term, quick-wins).

    Requires that predict_delivery_delays_tool and diagnose_delay_patterns_tool
    have been run so that the DB summary tables and delayed CSV exist.
    """
    start = time.perf_counter()
    status = "ok"
    _LOGGER.info("tool.recommend_actions.started")
    print("Recommend Best Course of Action Tool Called", flush=True)

    try:
        # ---- 1. Validate prerequisites ----
        if not _DB_PATH.exists():
            status = "missing_db"
            _LOGGER.warning("tool.recommend_actions.missing_db path=%s", _DB_PATH)
            return (
                "ERROR: Prediction database not found. "
                "Run predict_delivery_delays_tool first."
            )
        if not _DAILY_CSV.exists():
            status = "missing_daily_csv"
            _LOGGER.warning("tool.recommend_actions.missing_daily_csv path=%s", _DAILY_CSV)
            return (
                "ERROR: Daily delayed-orders CSV not found. "
                "Run predict_delivery_delays_tool first."
            )

        conn = sqlite3.connect(str(_DB_PATH))

        try:
            # ---- 2. Overall stats ----
            hist_overall = _overall(conn, "hist_")
            daily_overall = _overall(conn, "daily_")

            # ---- 3. Dimension comparisons (daily vs hist) ----
            dim_comparisons: dict[str, list[dict]] = {}
            for dim in _CAT_DIMS:
                rows = _compare_dim(conn, dim)
                if rows:
                    dim_comparisons[dim] = rows

            # ---- 4. High-risk patterns ----
            hist_risk = _high_risk(conn, "hist_", n=10)
            daily_risk = _high_risk(conn, "daily_", n=10)

            # ---- 5. Top problem dimensions (historical) ----
            hist_tops: dict[str, list[dict]] = {}
            for dim in _CAT_DIMS:
                top = _top_dims(conn, "hist_", dim, n=3)
                if top:
                    hist_tops[dim] = top

            # ---- 6. Top problem dimensions (daily) ----
            daily_tops: dict[str, list[dict]] = {}
            for dim in _CAT_DIMS:
                top = _top_dims(conn, "daily_", dim, n=3)
                if top:
                    daily_tops[dim] = top

        finally:
            conn.close()

        # ---- 7. Daily severity hotspots from CSV ----
        severity_hotspots = _daily_severity_hotspots(_DAILY_CSV)

        # ---- 8. Sidecar metadata ----
        meta: dict = {}
        if _SIDECAR.exists():
            meta = json.loads(_SIDECAR.read_text())

        # ---- 9. Build structured output ----
        sections: list[str] = []

        # --- 9a. Overall comparison ---
        sections.append("## Overall Comparison (Daily vs Historical)")
        sections.append(
            f"- **Historical**: {int(hist_overall.get('total_deliveries', 0)):,} deliveries, "
            f"delay rate {hist_overall.get('delay_rate', 0):.1%}, "
            f"Short={int(hist_overall.get('severity_short', 0)):,} / "
            f"Medium={int(hist_overall.get('severity_medium', 0)):,} / "
            f"Long={int(hist_overall.get('severity_long', 0)):,}"
        )
        sections.append(
            f"- **Today**: {int(daily_overall.get('total_deliveries', 0)):,} deliveries, "
            f"delay rate {daily_overall.get('delay_rate', 0):.1%}, "
            f"Short={int(daily_overall.get('severity_short', 0)):,} / "
            f"Medium={int(daily_overall.get('severity_medium', 0)):,} / "
            f"Long={int(daily_overall.get('severity_long', 0)):,}"
        )

        # --- 9b. Dimensions where daily is WORSE than historical ---
        sections.append("\n## Dimensions Where Today is Worse Than Historical")
        for dim, rows in dim_comparisons.items():
            worse = [r for r in rows if r.get("rate_delta", 0) > 0.02]
            if worse:
                sections.append(f"\n### {dim}")
                for r in worse:
                    sections.append(
                        f"- **{r[dim]}**: daily {r['daily_delay_rate']:.1%} vs "
                        f"hist {r['hist_delay_rate']:.1%} "
                        f"(+{r['rate_delta']:.1%}, {int(r['daily_delayed'])} delayed of {int(r['daily_total'])})"
                    )

        # --- 9c. Historical high-risk patterns ---
        sections.append("\n## Historical High-Risk Patterns (Top 10)")
        for p in hist_risk:
            sections.append(
                f"- [{p.get('risk_level', '')}] **{p.get('pattern_description', '')}**: "
                f"delay rate {p.get('delay_rate', 0):.1%} "
                f"({int(p.get('delayed_count', 0))}/{int(p.get('total_deliveries', 0))})"
            )

        # --- 9d. Daily high-risk patterns ---
        sections.append("\n## Today's High-Risk Patterns (Top 10)")
        for p in daily_risk:
            sections.append(
                f"- [{p.get('risk_level', '')}] **{p.get('pattern_description', '')}**: "
                f"delay rate {p.get('delay_rate', 0):.1%} "
                f"({int(p.get('delayed_count', 0))}/{int(p.get('total_deliveries', 0))})"
            )

        # --- 9e. Severity hotspots ---
        if severity_hotspots:
            sections.append("\n## Today's Long-Severity Hotspots")
            for h in severity_hotspots:
                sections.append(
                    f"- **{h['delivery_mode']}** + **{h['region']}** + "
                    f"**{h['weather_condition']}**: {h['long_count']} long-severity orders, "
                    f"avg distance {h['avg_distance']} km"
                )

        # --- 9f. Worst dimensions (historical — for long-term recs) ---
        sections.append("\n## Worst Dimensions — Historical (for long-term strategy)")
        for dim, tops in hist_tops.items():
            items = ", ".join(
                f"{t[dim]} ({t.get('delay_rate', 0):.1%})" for t in tops
            )
            sections.append(f"- **{dim}**: {items}")

        # --- 9g. Worst dimensions (daily — for quick-wins) ---
        sections.append("\n## Worst Dimensions — Today (for quick-wins)")
        for dim, tops in daily_tops.items():
            items = ", ".join(
                f"{t[dim]} ({t.get('delay_rate', 0):.1%})" for t in tops
            )
            sections.append(f"- **{dim}**: {items}")

        tool_output = "\n".join(sections)

        # Data-read summary — mirrors the step-level logging of the other tools
        _LOGGER.info(
            "tool.recommend_actions.data dims_compared=%s hist_high_risk=%s "
            "daily_high_risk=%s severity_hotspots=%s hist_top_dims=%s daily_top_dims=%s output_chars=%s",
            len(dim_comparisons), len(hist_risk), len(daily_risk),
            len(severity_hotspots), len(hist_tops), len(daily_tops), len(tool_output),
        )

        # ---- 10. Retrieve SLA knowledge via RAG ----
        try:
            from tools.rag_knowledge import retrieve_sla_context
            sla_context = retrieve_sla_context(tool_output)
            tool_output += "\n\n" + sla_context
        except Exception as e:
            status = "rag_failed"
            _LOGGER.warning("tool.recommend_actions.rag_failed error=%s", str(e))
            tool_output += f"\n\n[RAG] Failed to retrieve SLA context: {e}"

        return tool_output
    finally:
        _LOGGER.info(
            "tool.recommend_actions.completed status=%s duration_ms=%s",
            status,
            int((time.perf_counter() - start) * 1000),
        )
