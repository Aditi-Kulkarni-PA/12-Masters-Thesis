"""
Domain output schemas — topology-NEUTRAL, shared by every orchestration condition.

These pydantic models define the I/O contracts of the five domain sub-agents
(predict, diagnose, simulate, recommend, email). They are part of the frozen
experimental assets: identical bytes across all topology conditions.

MasterOutput used to live in topologies/planner_executor.py on the assumption that a
coordinator's output shape is topology-specific. That turned out to be wrong: six of the
seven conditions produce the same four fields, and `shared/output_contract.md` — the
prompt that specifies them — is already shared. A per-topology copy of the model would
let the schema and the prompt drift apart, and any resulting quality difference would be
the schema rather than the topology. Mesh is the sole exception: it has no coordinator
and therefore no MasterOutput at all (Risk Log R17).
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
                    "MUST contain at least 3 quick-win, 3 short-term, AND 3 long-term actions (9+ total)."
                    "Return an EMPTY list if the underlying tool call failed.",
        #min_length=9,  # changed to guidance since min length will fabricate data if there is tool error
    )


# ---------------------------------------------------------------------------
# 5. Email alert
# ---------------------------------------------------------------------------

class EmailAlert(BaseModel):
    email_content: str = Field(description="Professional email body to notify the customer about their delayed order.")
    email_id: str = Field(description="Email ID of the customer; use a realistic placeholder if unknown.", default="first.last@domain.com")

class EmailCounts(BaseModel):
    """The two email figures that are NOT derivable from the rendered samples.

    The email tool emails EVERY delayed order but returns only a few rendered sample
    bodies, so "how many emails exist" and "how many samples am I holding" are different
    numbers -- 10 vs 3 in every run observed. Carried as explicit fields because a
    coordinator that only receives `content` cannot recover the total by counting it.

    Observed 23-Aug-26 (runs 9b1f4e59, 562a4823, dbd1ad39): with no such field, Monolith
    -- which sees the raw tool text directly -- wrote "Generated 3 emails ... Long 3,
    Medium 1, Short 6", self-contradictory because 3 sat next to a breakdown summing to
    10; and Planner-Executor, whose specialist consumed the tool text and passed only the
    3 samples upward, could not state the totals at all and fell back to vague wording.
    Both scored 1.0-3.0 on faithfulness for what was really a missing-channel problem.
    Defining the fields here fixes BOTH shapes at once, so every topology reports the
    same two numbers from the same place.
    """
    total_orders_emailed: int = Field(
        default=0,
        description="Total delayed orders an email was generated for, exactly as the tool "
                    "reports it ('Total delayed orders emailed: N'). NOT the number of "
                    "rendered samples below. 0 if the tool errored or found no delayed orders.")
    template_breakdown: str = Field(
        default="",
        description="Per-severity template counts exactly as the tool reports them, e.g. "
                    "'Long 3 / Medium 1 / Short 6'. These sum to total_orders_emailed, NOT "
                    "to the number of rendered samples. Empty if the tool reported none.")


class EmailsList(EmailCounts):
    content: Annotated[
        list[EmailAlert],
        Field(
            description="List of emails to be sent to customers whose orders are delayed. "
                        "One item per delayed order's sample email when the tool succeeds. "
                        "Return an EMPTY list if the tool errored or found no delayed orders "
                        "-- do NOT fabricate an explanatory email.",
            #min_length=1, # changed to guidance since min length will fabricate data if there is tool error
        ),
    ]


# ---------------------------------------------------------------------------
# 6. Turn-1 triage gate — shared by every condition with a tools-off classify-before-run
#    turn (Static-Graph DAG, Dynamic-Graph). Was a local class duplicated per topology
#    file; identical shape in both, so hoisted here rather than redefined per topology.
# ---------------------------------------------------------------------------
class TriageDecision(BaseModel):
    """Turn 1's only output for a triage-gated condition. `proceed` drives control flow
    in code; `chat_response` is the refusal or informational text when `proceed` is
    False. What happens when `proceed` is True is topology-specific (see the calling
    module) — a fixed plan text stated in code, or nothing at all if the next turn
    states its own plan."""
    proceed: bool = Field(
        description="True only for an in-scope action request. False for a refusal "
                    "or an informational answer already written in chat_response.")
    chat_response: str = Field(
        default="", description="Refusal or informational answer. Required when "
                    "proceed is False, ignored when proceed is True.")


# ---------------------------------------------------------------------------
# 7. Coordinator output — shared by every condition that HAS a coordinator
# ---------------------------------------------------------------------------
class MasterOutput(BaseModel):
    """Slim master output.

    Domain row data and sub-agent summaries are NOT carried here — the app
    captures each sub-agent's full output directly from the tool-call stream
    (re-copying them through the master added 15-30s of generation per run).
    The master returns only conversational text and the thin per-tool notes
    that no sub-agent produces itself."""
    chat_response: str = Field(
        default="",
        description="Direct conversational answer for informational questions "
                    "(answered from fresh prior results or definitions, without running tools). "
                    "Also used for tool error reports. Leave empty when analysis tools ran successfully.",
    )
    simulate_summary: str = Field(
        default="",
        description="Brief qualitative simulation narrative (severity shifts, worst conditions), "
                    "or the tool's exact error message when the simulation returned no rows. "
                    "Empty if simulate was not run.",
    )
    recommendation_summary: str = Field(
        default="",
        description="2-3 sentence narrative of the overall optimization approach and key themes. "
                    "Empty if recommend was not run.",
    )
    email_alert_summary: str = Field(
        default="",
        description="Brief status of email generation (e.g. counts by severity template, or "
                    "'no delayed orders'). Empty if email was not run.",
    )


# ---------------------------------------------------------------------------
# 8. Monolith's coordinator output — MasterOutput PLUS every sub-agent's own
#    structured result (T37 fix, 23-Aug-26).
# ---------------------------------------------------------------------------
class MonolithOutput(MasterOutput, EmailCounts):
    """MasterOutput's docstring above is true for six of the seven conditions: a
    coordinator with SEPARATE specialist contexts never needs to carry predict_summary,
    llm_insights, simulate_delay_reason, recommended_actions, or rendered emails,
    because the app captures each specialist's own structured return value directly
    from the tool-call stream (Planner-Executor's agent-as-tool wrapping IS that
    capture point — see topologies/planner_executor.py).

    Monolith has no such point. It calls the same raw tools everyone else ultimately
    calls, but the raw tools themselves return unenriched data on purpose --
    `prediction_pipeline/src/daily_predict.py` hardcodes every row's llm_insights to ""
    with the comment "filled by the predict agent"; simulate_order_delays returns a
    Markdown text report with no simulate_delay_reason column at all; recommend_actions
    and fetch_delayed_orders_for_email similarly return raw text, not the scored
    RecommendedAction / rendered EmailAlert objects. Those enrichment steps are real
    analytical work described in agents/predict_delivery_delays.md,
    agents/diagnose_delay_patterns.md, agents/delay_simulation.md,
    agents/recommendation.md, and agents/email_alert.md -- the SAME files Swarm's
    wrapped agents use, and monolith/master.md already @include's them. Giving monolith
    MasterOutput's four narrative fields and nowhere else to write that work meant the
    work was silently skipped: confirmed 23-Aug-26 on a real run -- every llm_insights
    empty, no predict_summary anywhere, no simulate_delay_reason, near-instant tool
    durations for what should be substantial per-row reasoning (predict/diagnose/
    simulate/email all finished in under 0.3s; only recommend's own RAG retrieval took
    real time). That is not monolith being efficient -- it is monolith not doing the
    work every other condition's specialists do.

    Reuses the EXACT SAME result models Planner-Executor's specialists already produce
    (RowEnrichment, DiagnosisHighRisk, DiagnosisComparison, SimulateDelays,
    RecommendedAction, EmailAlert) so the identical judged artifacts exist for both --
    see supply_chain_topology_app/score_topology_run.py, which reads whichever of
    (tool payload, this schema) actually has the data.
    """
    predict_summary: str = Field(
        default="", description="Same field/format as DeliveryDelayPredictionResult.predict_summary")
    delayed_orders: list[RowEnrichment] = Field(
        default_factory=list,
        description="One {delivery_id, llm_insights} per delayed row -- every llm_insights non-empty, "
                    "per agents/predict_delivery_delays.md")
    diagnosis_summary: str = Field(
        default="", description="Same field/format as DelayDiagnosisResult.diagnosis_summary")
    high_risk_patterns: list[DiagnosisHighRisk] = Field(default_factory=list)
    comparison: list[DiagnosisComparison] = Field(default_factory=list)
    simulations: list[SimulateDelays] = Field(
        default_factory=list,
        description="Every row's simulate_delay_reason filled, per agents/delay_simulation.md")
    recommended_actions: list[RecommendedAction] = Field(
        default_factory=list,
        description="Full scored actions (quick-win/short-term/long-term) per agents/recommendation.md, "
                    "each with a non-empty sla_reference")
    content: list[EmailAlert] = Field(
        default_factory=list,
        description="Rendered sample emails per agents/email_alert.md -- normally FEWER "
                    "than total_orders_emailed (inherited from EmailCounts)")
