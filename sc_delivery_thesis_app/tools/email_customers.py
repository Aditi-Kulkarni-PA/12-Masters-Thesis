"""
Email alert tool — generates severity-based email templates for delayed orders.

For each delayed order:
1. Selects a template based on delay severity (Long / Medium / Short)
2. Fills in personalized fields (order_id, region, weather, mode, distance, severity)
3. Writes the email_template column back into the prediction CSV
4. Returns the distinct templates used + per-template counts for the agent to summarize
"""

from pathlib import Path
import logging
import os
import time

import pandas as pd
from agents import function_tool

_TOOL_DIR = Path(__file__).resolve().parent
_APP_DIR = _TOOL_DIR.parent                       # supply_chain_delivery_app/
_OUTPUT_CSV = _APP_DIR / "output" / "daily_delivery_delay_prediction.csv"
# Pipeline's canonical CSV (always fresh after a predict run in the same session)
_PIPELINE_CSV = (_APP_DIR.parent.parent / "prediction_pipeline"
                 / "data" / "processed" / "daily_delivery_delay_prediction.csv")

_LOGGER = logging.getLogger("supply_chain_delivery_app")

# Cap on delayed rows to process. Default 10 avoids generating thousands of emails
# during development. Set SC_EMAIL_MAX_ROWS=0 to disable the cap (process all).
_MAX_ROWS: int | None = int(os.getenv("SC_EMAIL_MAX_ROWS", "10")) or None

# ---------------------------------------------------------------------------
# Email templates by severity
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, str] = {
    "Long": (
        "Subject: Urgent -- Significant Delay for Order {order_id}\n\n"
        "Dear Valued Customer,\n\n"
        "We regret to inform you that your order {order_id} is experiencing a "
        "significant delay (estimated 6+ hours beyond the original delivery window). "
        "This is primarily due to {weather} weather conditions affecting {delivery_mode} "
        "deliveries in the {region} region over a distance of {distance_km} km.\n\n"
        "What we are doing:\n"
        "- Your order has been escalated to our priority resolution team\n"
        "- We are actively rerouting through the fastest available channel\n"
        "- A dedicated support agent will follow up within 2 hours\n\n"
        "As a gesture of goodwill, we will apply a delivery credit to your account. "
        "We sincerely apologize for the inconvenience and appreciate your patience.\n\n"
        "Track your order: https://track.example.com/{order_id}\n"
        "Support: support@example.com | 1-800-DELIVERY\n\n"
        "Best regards,\nCustomer Experience Team"
    ),
    "Medium": (
        "Subject: Delivery Update -- Moderate Delay for Order {order_id}\n\n"
        "Dear Valued Customer,\n\n"
        "We would like to inform you that your order {order_id} is experiencing a "
        "moderate delay (estimated 3--5 hours). Current {weather} conditions are "
        "affecting {delivery_mode} service in the {region} region "
        "(delivery distance: {distance_km} km).\n\n"
        "What we are doing:\n"
        "- Our logistics team is monitoring your shipment closely\n"
        "- Route optimization is being applied to minimize further delay\n\n"
        "We expect your delivery to arrive within the revised window. "
        "We apologize for any inconvenience.\n\n"
        "Track your order: https://track.example.com/{order_id}\n"
        "Support: support@example.com | 1-800-DELIVERY\n\n"
        "Best regards,\nCustomer Experience Team"
    ),
    "Short": (
        "Subject: Delivery Update -- Minor Delay for Order {order_id}\n\n"
        "Dear Valued Customer,\n\n"
        "A brief update on your order {order_id}: we anticipate a slight delay "
        "(estimated 1--2 hours) due to {weather} conditions in the {region} region. "
        "Your {delivery_mode} shipment covering {distance_km} km is on its way.\n\n"
        "No action is needed on your part -- your delivery is expected to arrive "
        "shortly after the original window.\n\n"
        "Track your order: https://track.example.com/{order_id}\n\n"
        "Best regards,\nCustomer Experience Team"
    ),
}

_DEFAULT_TEMPLATE_KEY = "Short"  # fallback if severity label not recognized


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _severity_key(label: str) -> str:
    """Map predict_severity_label to a template key."""
    label_lower = str(label).lower()
    if "long" in label_lower:
        return "Long"
    if "medium" in label_lower:
        return "Medium"
    return "Short"


def _generate_email(row: pd.Series) -> tuple[str, str]:
    """Generate an email for one delayed order. Returns (template_key, email_text)."""
    sev_key = _severity_key(row.get("predict_severity_label", "Short"))
    template = _TEMPLATES.get(sev_key, _TEMPLATES[_DEFAULT_TEMPLATE_KEY])
    email = template.format(
        order_id=str(row.get("delivery_id", "N/A")).replace(".0", ""),
        weather=row.get("weather_condition", "unknown"),
        delivery_mode=row.get("delivery_mode", "standard"),
        region=row.get("region", "unknown"),
        distance_km=round(row.get("distance_km", 0), 1),
    )
    return sev_key, email


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

@function_tool
def fetch_delayed_orders_for_email() -> str:
    """
    Generate email alerts for all delayed orders using severity-based templates.
    Writes the email text and template name back into the prediction CSV.
    Returns the distinct templates used with counts for summarization.
    """
    start = time.perf_counter()
    status = "ok"
    _LOGGER.info("tool.fetch_delayed_orders_for_email.started")
    # Prefer the pipeline's freshly-written CSV (populated in the same session
    # as predict_delivery_delays_tool runs); fall back to the app output copy.
    csv_path = _PIPELINE_CSV if _PIPELINE_CSV.exists() else _OUTPUT_CSV

    try:
        if not csv_path.exists():
            status = "missing_prediction_csv"
            _LOGGER.warning("tool.fetch_delayed_orders_for_email.missing_csv path=%s", csv_path)
            print("No prediction output found. Run the prediction pipeline first.", flush=True)
            return "ERROR: No prediction output CSV found. Run predict_delivery_delays_tool first."

        df = pd.read_csv(csv_path)

        # The CSV may be delayed-only (no predict_delay column) or full (with predict_delay column)
        if "predict_delay" in df.columns:
            delayed = df[df["predict_delay"] == 1].copy()
        else:
            # CSV contains only delayed orders already
            delayed = df.copy()

        if delayed.empty:
            status = "no_delayed_orders"
            _LOGGER.info("tool.fetch_delayed_orders_for_email.no_delayed_orders")
            print("No delayed orders found in latest predictions.", flush=True)
            return "No delayed orders found -- no email alerts needed."

        if _MAX_ROWS:
            delayed = delayed.head(_MAX_ROWS)

        # Generate emails for all delayed rows
        template_keys: list[str] = []
        email_texts: list[str] = []
        for _, row in delayed.iterrows():
            sev_key, email = _generate_email(row)
            template_keys.append(sev_key)
            email_texts.append(email)

        # Write back to the CSV
        delayed["email_template_name"] = template_keys
        delayed["email_content"] = email_texts

        if "predict_delay" in df.columns:
            # Full CSV — merge delayed rows back
            df.loc[delayed.index, "email_template_name"] = template_keys
            df.loc[delayed.index, "email_content"] = email_texts
            df["email_template_name"] = df["email_template_name"].fillna("")
            df["email_content"] = df["email_content"].fillna("")
            df.to_csv(csv_path, index=False)
        else:
            # Delayed-only CSV — write directly
            delayed.to_csv(csv_path, index=False)

        print(
            f"Generated {len(email_texts)} email alerts and wrote to CSV.",
            flush=True,
        )

        # Build summary for the agent
        counts = pd.Series(template_keys).value_counts().to_dict()
        lines: list[str] = []
        lines.append("## Email Alert Generation Summary")
        lines.append(f"- Total delayed orders emailed: {len(email_texts)}")
        lines.append("- CSV updated with email_template_name and email_content columns")
        lines.append("\n### Emails by Template")
        for tpl, cnt in sorted(counts.items()):
            lines.append(f"- **{tpl} Delay Template**: {cnt} emails")

        lines.append("\n### Template Definitions")
        for i, (tpl_key, tpl_text) in enumerate(_TEMPLATES.items()):
            if i > 0:
                lines.append("\n---\n")
            lines.append(f"\n#### {tpl_key} Delay Template")
            lines.append(f"```\n{tpl_text}\n```")

        # Include a few sample emails
        sample_count = min(3, len(email_texts))
        lines.append(f"\n### Sample Generated Emails ({sample_count} shown)")
        for i in range(sample_count):
            if i > 0:
                lines.append("\n---\n")
            lines.append(f"\n**Sample {i+1} ({template_keys[i]} severity):**")
            lines.append(f"```\n{email_texts[i]}\n```")

        return "\n".join(lines)
    finally:
        _LOGGER.info(
            "tool.fetch_delayed_orders_for_email.completed status=%s duration_ms=%s csv_path=%s",
            status,
            int((time.perf_counter() - start) * 1000),
            csv_path,
        )
