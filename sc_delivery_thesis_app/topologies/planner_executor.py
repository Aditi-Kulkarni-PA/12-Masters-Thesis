
"""
Supply Chain Delivery – Master Agent definition.

Key logic:
    - Planner-executor topology: master agent orchestrates sub-agents and tools, validates output
    - Master orchestrator agent (supply_chain_delivery_master_agent) coordinates all tools and output
    - Fallback agents handle edge cases and summary formatting
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
from core.agents import (
    predict_delivery_delays_agent, diagnose_delay_patterns_agent,
    delay_simulation_agent, recommendation_agent, email_alert_agent,
    fallback_advisor_agent,
)
from core.clients import chat_client, chat_client_mini
from core.instrumentation import record_sub_agent_usage

def _wrap_as_tool(agent, *, tool_name, tool_description):
    """Wrap the domain sub agent as a tool for the master agent.

    Every domain sub-agent follows the same recipe:
      - instructions read verbatim from config/prompts/agents/<prompt_key>.md
      - deterministic settings (temperature=0) with forced tool use
      - a strongly-typed Pydantic output_type
      - tools come either from the shared pipeline MCP server or a local
        @function_tool
    Returns (agent, agent-as-tool).
    """

    # deep inside _wrap_as_tool, after the sub-agent finishes, 
    # it calls record_sub_agent_usage(tool_name, result), which does _sub_agent_bucket.get() 
    # to find that same list _sub_agent_bucket.get() and appends the usage numbers to it .

    # Deep inside _wrap_as_tool, record_sub_agent_usage(tool_name, result) runs
    # bucket = _sub_agent_bucket.get() — this hands back that same list L. 
    # Then bucket.append({...}) mutates L directly, in place.

    @tool(name=tool_name, description=tool_description)
    async def _run_sub_agent(request: str) -> str:
        result = await agent.run(request)
        record_sub_agent_usage(tool_name, result)
        if isinstance(result.value, BaseModel):
            return result.value.model_dump_json()
        return str(result.value)
    return _run_sub_agent


# ---------------------------------------------------------------------------
# Wrap sub-agents as tools for the master orchestrator agent. Each sub-agent is a 
# tool with its own name and description.
# ---------------------------------------------------------------------------

predict_delivery_delays_tool = _wrap_as_tool(
    predict_delivery_delays_agent,
    tool_name="predict_delivery_delays_tool",
    tool_description="Run the two-stage ML pipeline to predict delayed orders and classify severity",
)

diagnose_delay_patterns_tool = _wrap_as_tool(
    diagnose_delay_patterns_agent,
    tool_name="diagnose_delay_patterns_tool",
    tool_description="Diagnose delay patterns comparing today's predictions vs historical data across all dimensions",
)

delay_simulations_tool = _wrap_as_tool(
    delay_simulation_agent,
    tool_name="delay_simulations_tool",
    tool_description="Simulate weather, vehicle, regional conditions to analyze delay impact",
)

recommendation_tool = _wrap_as_tool(
    recommendation_agent,
    tool_name="recommendation_tool",
    tool_description="Recommendations to optimize order delivery and minimize delays",
)

email_alert_tool = _wrap_as_tool(
    email_alert_agent,
    tool_name="email_alert_tool",
    tool_description="Emails to be sent to customers",
)

# ---------------------------------------------------------------------------
# 6. Fallback advisor agent (for handoff; no Pydantic output)
# ---------------------------------------------------------------------------

# wrap-as-tool belongs to core if the wrapping itself carries no orchestration decision, 
# and to topology if the wrapping is the orchestration decision. Fallback's wrapping 
# encodes "how does this topology cope with missing data" — that's a real design choice 
# per topology, so keep it local to planner_executor.py when we extract topologies.
# Hence we keep the fallback advisor tool wrapping local to this module.

fallback_advisor_tool = fallback_advisor_agent.as_tool(
    name="fallback_advisor_tool",
    description="Use this when no tool results and datasets are found and you need alternative suggestions",
    arg_name="request",
)

# ---------------------------------------------------------------------------
# 7. Master orchestrator — Pydantic output and agent: coordinates all tools, validates output
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
