
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

from agent_framework import Agent, tool
from pydantic import BaseModel, Field

from config import get_instruction
from tools import (
    recommend_actions,
    fetch_delayed_orders_for_email,
)
from core.schemas import (
    RowEnrichment,
    TopEntry,
    DeliveryDelaySummary,
    DeliveryDelayPredictionResult,
    DiagnosisHighRisk,
    DiagnosisComparison,
    DelayDiagnosisResult,
    SimulateDelays,
    SimulationsList,
    RecommendedAction,
    RecommendedActionsList,
    EmailAlert,
    EmailsList,
)

from core.clients import chat_client, chat_client_mini
from core.mcp_tools import pipeline_mcp

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
# 1. Predict delivery delays — agent/tool definition (schemas in core/schemas.py)
# ---------------------------------------------------------------------------

predict_delivery_delays_agent, predict_delivery_delays_tool = _sub_agent_as_tool(
    agent_name="Predict Delivery Delays",
    prompt_key="predict_delivery_delays",
    output_type=DeliveryDelayPredictionResult,
    tool_name="predict_delivery_delays_tool",
    tool_description="Run the two-stage ML pipeline to predict delayed orders and classify severity",
    use_pipeline_mcp=True,
)


# ---------------------------------------------------------------------------
# 2. Diagnose delay patterns — agent/tool definition (schemas in core/schemas.py)
# ---------------------------------------------------------------------------

diagnose_delay_patterns_agent, diagnose_delay_patterns_tool = _sub_agent_as_tool(
    agent_name="Diagnose & Analyse Delay Patterns",
    prompt_key="diagnose_delay_patterns",
    output_type=DelayDiagnosisResult,
    tool_name="diagnose_delay_patterns",
    tool_description="Diagnose delay patterns comparing today's predictions vs historical data across all dimensions",
    use_pipeline_mcp=True,
)


# ---------------------------------------------------------------------------
# 3. Delay simulation — agent/tool definition (schemas in core/schemas.py)
# ---------------------------------------------------------------------------

delay_simulation_agent, delay_simulations_tool = _sub_agent_as_tool(
    agent_name="Simulate & Analyse Delay Prediction",
    prompt_key="delay_simulation",
    output_type=SimulationsList,
    tool_name="delay_simulations_tool",
    tool_description="Simulate weather and traffic conditions to analyze delay impact",
    use_pipeline_mcp=True,
)


# ---------------------------------------------------------------------------
# 4. Recommendation — agent/tool definition (schemas in core/schemas.py)
# ---------------------------------------------------------------------------

recommendation_agent, recommendation_tool = _sub_agent_as_tool(
    agent_name="Recommendation Expert Agent to Optimize Order Delivery",
    prompt_key="recommendation",
    output_type=RecommendedActionsList,
    tool_name="recommendation_tool",
    tool_description="Recommendations to optimize order delivery and minimize delays",
    function_tools=[recommend_actions],
)


# ---------------------------------------------------------------------------
# 5. Email alert — agent/tool definition (schemas in core/schemas.py)
# ---------------------------------------------------------------------------

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
    default_options={"tool_choice": "auto", "response_format": MasterOutput, "temperature": 0},
)
