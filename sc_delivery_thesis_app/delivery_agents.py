
"""
Supply Chain Delivery – Agent definitions.

Key logic:
    - Defines all Pydantic models for agent tool I/O (predict, diagnose, simulate, recommend, email)
    - Sets up MCP server for prediction/diagnosis tools (pipeline_mcp)
    - Each sub-agent is an Agent with its own prompt, model, and tool wiring
    - Master orchestrator agent (supply_chain_delivery_master_agent) coordinates all tools and output
    - All tool calls are strongly typed and validated via Pydantic
    - Model selection, tool_choice, and output_type are set for each agent
    - Fallback and formatting agents handle edge cases and summary formatting
"""

import os
import sys
from pathlib import Path

# Ensure this directory is importable (for config/ and tools/ packages)
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from dotenv import load_dotenv, find_dotenv
load_dotenv(dotenv_path=find_dotenv(), override=False)

from agent_framework import Agent, tool, MCPStdioTool
from agent_framework.openai import OpenAIChatClient
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from typing import Annotated, Literal, Optional

from config import get_instruction
from tools import (
    recommend_actions,
    fetch_delayed_orders_for_email,
)

# ---------------------------------------------------------------------------
# MCP server for predict + diagnosis (lives in prediction_pipeline/): shared by sub-agents
# ---------------------------------------------------------------------------
_PREDICTION_SERVER = str(
    _APP_DIR.parent / "prediction_pipeline" / "prediction_server.py"
)
_PYTHON = sys.executable

# cwd pinned to the project root so relative file paths (e.g. from evals) resolve
# the same way regardless of the directory the parent process was launched from.
_MCP_PARAMS = {"command": _PYTHON, "args": [_PREDICTION_SERVER], "cwd": str(_APP_DIR.parent)}

# The only pipeline tools agents may call via MCP. Also imported by the chat
# app for per-tool timing logs, so the list is defined exactly once.
PIPELINE_TOOL_NAMES = [
    "predict_delivery_delays",
    "get_delay_diagnosis",
    "simulate_order_delays",
]

# One MCP server instance shared by the pipeline sub-agents.
# tool_filter restricts agents to only the pipeline tools, blocking
# everything else that may exist on the server now or in the future.
# Each sub-agent's prompt further pins it to its specific tool by name.
pipeline_mcp = MCPStdioTool(
    name="prediction_pipeline",
    command=_PYTHON,
    args=[_PREDICTION_SERVER],
    allowed_tools=PIPELINE_TOOL_NAMES,   # MAF kwarg (was: tool_filter)
    request_timeout=120,                 # MAF kwarg (was: client_session_timeout_seconds)
)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
MODEL_MINI = os.getenv("OPENAI_MODEL_MINI", "gpt-4.1-mini")

def _configure_llm_backend() -> OpenAIChatClient:
    """Configure provider routing for OpenAI cloud or local LM Studio."""
    backend = os.getenv("LLM_BACKEND", "openai").strip().lower()

    if backend == "lmstudio":
        return OpenAIChatClient(
            model=MODEL,
            base_url = os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1").strip(),
            api_key = os.getenv("LLM_API_KEY", "lm-studio").strip() or "lm-studio",
        )
    return OpenAIChatClient(model=MODEL)  # default: OpenAI cloud

chat_client = _configure_llm_backend()
chat_client_mini = OpenAIChatClient(model=MODEL_MINI)  # for lightweight formatting agent

async def _json_output_extractor(run_result) -> str:
    """Serialize a sub-agent's structured final_output back to JSON.

    agent.as_tool()'s default extractor returns final_output's Python str()/repr()
    when no custom_output_extractor is given, which is not valid JSON for a Pydantic
    model. The app's chat handler parses each tool's output with json.loads() to
    progressively fill the UI tabs, so this must always hand back real JSON.
    """
    final_output = run_result.final_output
    if isinstance(final_output, BaseModel):
        return final_output.model_dump_json()
    return str(final_output)


def _sub_agent_as_tool(
    *,
    agent_name: str,
    prompt_key: str,
    output_type: type[BaseModel],
    tool_name: str,
    tool_description: str,
    use_pipeline_mcp: bool = False,
    function_tools: list | None = None,
) -> tuple[Agent, object]:
    """Create a domain sub-agent and wrap it as a tool for the master agent.

    Every domain sub-agent follows the same recipe:
      - instructions read verbatim from config/prompts/agents/<prompt_key>.md
      - deterministic settings (temperature=0) with forced tool use
      - a strongly-typed Pydantic output_type
      - tools come either from the shared pipeline MCP server or a local
        @function_tool
    Returns (agent, agent-as-tool).
    """
    agent = Agent(
        name=agent_name,
        client=chat_client,
        instructions=get_instruction(prompt_key),
        tools=(function_tools or []) + ([pipeline_mcp] if use_pipeline_mcp else []),
        default_options={"temperature": 0, "tool_choice": "required", "response_format": output_type},
    )

    @tool(name=tool_name, description=tool_description)
    async def _run_sub_agent(request: str) -> str:
        """Run the sub-agent and return its final_output as JSON."""
        result = await agent.run(request)
        if isinstance(result.value, BaseModel):
            return result.value.model_dump_json()
        return str(result.value)
    
    return agent, _run_sub_agent


# ---------------------------------------------------------------------------
# 1. Predict delivery delays — Pydantic models and agent/tool definition
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

predict_delivery_delays_agent, predict_delivery_delays_tool = _sub_agent_as_tool(
    agent_name="Predict Delivery Delays",
    prompt_key="predict_delivery_delays",
    output_type=DeliveryDelayPredictionResult,
    tool_name="predict_delivery_delays_tool",
    tool_description="Run the two-stage ML pipeline to predict delayed orders and classify severity",
    use_pipeline_mcp=True,
)


# ---------------------------------------------------------------------------
# 2. Diagnose delay patterns — Pydantic models and agent/tool definition
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

diagnose_delay_patterns_agent, diagnose_delay_patterns_tool = _sub_agent_as_tool(
    agent_name="Diagnose & Analyse Delay Patterns",
    prompt_key="diagnose_delay_patterns",
    output_type=DelayDiagnosisResult,
    tool_name="diagnose_delay_patterns",
    tool_description="Diagnose delay patterns comparing today's predictions vs historical data across all dimensions",
    use_pipeline_mcp=True,
)


# ---------------------------------------------------------------------------
# 3. Delay simulation — Pydantic models and agent/tool definition
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

delay_simulation_agent, delay_simulations_tool = _sub_agent_as_tool(
    agent_name="Simulate & Analyse Delay Prediction",
    prompt_key="delay_simulation",
    output_type=SimulationsList,
    tool_name="delay_simulations_tool",
    tool_description="Simulate weather and traffic conditions to analyze delay impact",
    use_pipeline_mcp=True,
)


# ---------------------------------------------------------------------------
# 4. Recommendation — Pydantic models and agent/tool definition
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

recommendation_agent, recommendation_tool = _sub_agent_as_tool(
    agent_name="Recommendation Expert Agent to Optimize Order Delivery",
    prompt_key="recommendation",
    output_type=RecommendedActionsList,
    tool_name="recommendation_tool",
    tool_description="Recommendations to optimize order delivery and minimize delays",
    function_tools=[recommend_actions],
)


# ---------------------------------------------------------------------------
# 5. Email alert — Pydantic models and agent/tool definition
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

email_alert_agent, email_alert_tool = _sub_agent_as_tool(
    agent_name="Email Alert Agent",
    prompt_key="email_alert",
    output_type=EmailsList,
    tool_name="email_alert_tool",
    tool_description="Emails to be sent to customers",
    function_tools=[fetch_delayed_orders_for_email],
)


# ---------------------------------------------------------------------------
# 6. Fallback advisor agent (for handoff; no Pydantic output)
# ---------------------------------------------------------------------------

fallback_advisor_agent = Agent(
    name="Fallback Supply Chain Optimization Agent",
    client=chat_client,
    instructions=get_instruction("fallback_advisor"),
)

fallback_advisor_tool = fallback_advisor_agent.as_tool(
    name="fallback_advisor_tool",
    description="Use this when no tool results and datasets are found and you need alternative suggestions",
    arg_name="request",
)

# ---------------------------------------------------------------------------
# 7. Format summary agent — lightweight formatter (uses MODEL_MINI)
# ---------------------------------------------------------------------------

format_summary_agent = Agent(
    name="Summary Formatting Specialist",
    client=chat_client_mini,
    instructions=get_instruction("format_summary"),
    default_options={"temperature": 0},
)   # still not wired in, matching baseline

format_summary_tool = format_summary_agent.as_tool(
    name="format_summary_tool",
    description=(
        "Format structured data into a clean Markdown summary. "
        "Pass a message with: summary_type (predict, diagnosis, simulate, recommendation, email_alert) "
        "and the raw data from the domain tool."
    ),
)


# ---------------------------------------------------------------------------
# 8. Master orchestrator — Pydantic output and agent: coordinates all tools, validates output
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

# MAF's Agent has no handoffs param — handoff is an orchestration-level pattern. 
# Faithful workaround: expose the fallback advisor as a tool with the handoff description

supply_chain_delivery_master_agent = Agent(
    name="Supply Chain Last-Mile Delivery Optimization Expert Agent",
    instructions=get_instruction("master_expert"),
    client=chat_client,
    # format_summary_tool is intentionally NOT wired in: all display formatting
    # is deterministic in helpers/post_processing.py (the agent remains defined
    # above and can be re-attached if a use case returns).
    tools=[
        predict_delivery_delays_tool,
        diagnose_delay_patterns_tool,
        delay_simulations_tool,
        recommendation_tool,
        email_alert_tool,
        fallback_advisor_tool,
    ],
    default_options={"tool_choice": "auto", "response_format": MasterOutput},
)
