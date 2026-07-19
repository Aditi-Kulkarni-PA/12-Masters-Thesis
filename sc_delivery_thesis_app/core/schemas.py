"""
Domain output schemas — topology-NEUTRAL, shared by every orchestration condition.

These pydantic models define the I/O contracts of the five domain sub-agents
(predict, diagnose, simulate, recommend, email). They are part of the frozen
experimental assets: identical bytes across all topology conditions.

Coordinator/topology-specific output models (e.g. MasterOutput for the
planner-executor) live with their topology in topologies/, NOT here.
"""

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 1. Predict delivery delays
# ---------------------------------------------------------------------------

class RowEnrichment(BaseModel):
    """Slim model: only delivery_id + llm_insights.
    The full row data lives in the CSV on disk — no need for the LLM to copy it."""
    delivery_id: str = Field(description="Delivery ID — must match the value from the tool's delayed_orders")
    llm_insights: str = Field(min_length=10, description="REQUIRED — 1-2 sentence cross-functional explanation referencing at least two derived features (e.g. schedule_risk, vehicle_load_strain, km_per_expected_hr, vehicle_type). Must not be empty.")

class TopEntry(BaseModel):
    name: str = Field(description="Category name (e.g. region name, weather condition, partner name)")
    count: int = Field(description="Number of delayed orders in this category")
    pct: float = Field(description="Percentage of total delayed orders")

class DeliveryDelaySummary(BaseModel):
    total_orders: int = Field(description="Total orders analysed")
    total_delayed: int = Field(description="Total predicted delayed orders")
    pct_delayed: float = Field(description="Percentage of orders predicted delayed")
    severity_short: int = Field(description="Count of Short (1-2h) delayed orders")
    severity_medium: int = Field(description="Count of Medium (3-5h) delayed orders")
    severity_long: int = Field(description="Count of Long (6+h) delayed orders")
    delayed_csv_path: str = Field(default="", description="Path to the delayed-only prediction CSV")
    showing_top_n: int = Field(default=0, description="Number of delayed rows shown in the table")
    top_regions: list[TopEntry] = Field(default_factory=list, description="Top affected regions")
    top_weather: list[TopEntry] = Field(default_factory=list, description="Top affected weather conditions")
    top_partners: list[TopEntry] = Field(default_factory=list, description="Top affected delivery partners")
    enrich_rows_cap: int = Field(default=50, description="Number of rows sent to the agent for delay_reason enrichment (SC_MCP_ENRICH_ROWS)")

class DeliveryDelayPredictionResult(BaseModel):
    predict_summary: str = Field(description="Cross-dimensional insight paragraph written by the agent — Markdown bullets with quantitative derived-feature stats")
    delayed_orders: list[RowEnrichment] = Field(
        default_factory=list,
        description="One {delivery_id, llm_insights} entry per delayed row. Must have exactly enrich_rows_cap entries, each with non-empty llm_insights.",
    )


# ---------------------------------------------------------------------------
# 2. Diagnose delay patterns
# ---------------------------------------------------------------------------

class DiagnosisHighRisk(BaseModel):
    pattern_type: str = Field(description="Type of pattern combination (mode_weather, mode_distance, weather_vehicle)")
    pattern_description: str = Field(description="Human-readable pattern description (e.g. 'same_day + Stormy')")
    total_deliveries: int = Field(description="Total deliveries matching this pattern")
    delayed_count: int = Field(description="Number of delayed deliveries")
    delay_rate_pct: float = Field(description="Delay rate as percentage")
    risk_level: str = Field(description="Risk level: critical (50%+), high (40-50%), medium (30-40%)")

class DiagnosisComparison(BaseModel):
    dimension: str = Field(description="Dimension name (region, weather_condition, delivery_partner, etc.)")
    category: str = Field(description="Category value (East, Stormy, DHL, etc.)")
    daily_total: int = Field(description="Today's total deliveries for this category")
    daily_delayed: int = Field(description="Today's delayed count")
    daily_delay_rate_pct: float = Field(description="Today's delay rate %")
    hist_total: int = Field(description="Historical total deliveries")
    hist_delayed: int = Field(description="Historical delayed count")
    hist_delay_rate_pct: float = Field(description="Historical delay rate %")
    rate_change_pct: float = Field(description="Change in delay rate (daily - hist), negative means improvement")

class DelayDiagnosisResult(BaseModel):
    high_risk_patterns: list[DiagnosisHighRisk] = Field(description="High-risk delay pattern combinations for today")
    comparison: list[DiagnosisComparison] = Field(description="Today vs historical delay rate comparison across all dimensions")
    diagnosis_summary: str = Field(default="", description="Formatted Markdown summary of delay pattern diagnosis, generated by the agent")


# ---------------------------------------------------------------------------
# 3. Delay simulation
# ---------------------------------------------------------------------------

class SimulateDelays(BaseModel):
    delivery_id: str = Field(description="Delivery ID")
    delivery_partner: str = Field(description="Delivery Partner")
    delivery_mode: str = Field(description="Delivery Mode")
    region: str = Field(description="Region")
    weather_condition: str = Field(description="Simulated weather condition")
    vehicle_type: str = Field(description="Simulated vehicle type")
    distance_km: str = Field(description="Distance in km")
    original_severity: str = Field(description="Original predicted severity label")
    simulated_severity: str = Field(description="Simulated severity under new conditions")
    simulate_delay_reason: Optional[str] = Field(description="Reason for simulated delay")

class SimulationsList(BaseModel):
    simulations: list[SimulateDelays] = Field(description="List of simulations for order delivery delays")


# ---------------------------------------------------------------------------
# 4. Recommendation
# ---------------------------------------------------------------------------

class RecommendedAction(BaseModel):
    action: str = Field(description="Recommendation Action - Short Description")
    action_desc: str = Field(description="Recommendation Action - Full Description with supporting data")
    category: Literal["quick-win", "short-term", "long-term"] = Field(description="One of: quick-win, short-term, long-term")
    dimension: str = Field(description="Which dimension this targets: delivery_mode, weather, region, vehicle, partner, or general")
    supporting_data: str = Field(description="Specific numbers from the analysis that justify this recommendation")
    sla_reference: str = Field(description="Quote the actual SLA text from the Retrieved Sections — include the section heading and specific metric, target, penalty, or rule. Example: 'SLA 2.1 On-Time Delivery Commitments by Mode: Express current target OTD is 40%. SLA 3.2 Weather-Specific Operational Protocols: Stormy — halt same-day and express dispatches if wind speed > 60 km/h.' Do NOT write generic labels like 'SLA Reference 3' — always quote the content itself.")

class RecommendedActionsList(BaseModel):
    recommended_actions: list[RecommendedAction] = Field(
        description="List of recommended actions for delivery optimization. "
                    "MUST contain at least 3 quick-win, 3 short-term, AND 3 long-term actions (9+ total).",
        min_length=9,
    )


# ---------------------------------------------------------------------------
# 5. Email alert
# ---------------------------------------------------------------------------

class EmailAlert(BaseModel):
    email_content: str = Field(description="Professional email body to notify the customer about their delayed order.")
    email_id: str = Field(description="Email ID of the customer; use a realistic placeholder if unknown.", default="first.last@domain.com")

class EmailsList(BaseModel):
    content: Annotated[
        list[EmailAlert],
        Field(
            description="List of emails to be sent to customers whose orders are delayed. If there is at least one delayed order, this MUST have at least one item.",
            min_length=1,
        ),
    ]
