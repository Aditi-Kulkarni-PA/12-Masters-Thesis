
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
from typing import Annotated

# Ensure this directory is importable (for config/ and tools/ packages)
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from dotenv import load_dotenv, find_dotenv
load_dotenv(dotenv_path=find_dotenv(), override=False)
from agent_framework import Agent, tool
from config import get_instruction
from core.agents import (
    predict_delivery_delays_agent, diagnose_delay_patterns_agent,
    delay_simulation_agent, recommendation_agent, email_alert_agent,
    fallback_advisor_agent,
)
from core.clients import chat_client, chat_client_mini
from measurement.instrumentation import run_sub_agent, serialize_agent_value
from core.tool_descriptions import TOOL_DESCRIPTIONS
from core.schemas import MasterOutput

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
        """request: the full task for this specialist, INCLUDING any file paths from the user's message verbatim."""
        return await _invoke_sub_agent(agent, tool_name, request)
    return _run_sub_agent


async def _invoke_sub_agent(agent, tool_name: str, task: str) -> str:
    """Run a wrapped sub-agent on `task` and serialise its output.

    Factored out of _wrap_as_tool so the recommendation wrapper below can add an extra
    parameter without duplicating the usage-recording and serialisation contract.

    Thin wrapper around measurement.instrumentation.run_sub_agent / serialize_agent_value:
    the run+record-usage+serialise logic lives there. This function's only job is
    adapting that shared contract to the string return type a @tool-decorated closure
    requires.
    """
    result = await run_sub_agent(agent, tool_name, task)
    return serialize_agent_value(result)


def _wrap_recommendation_as_tool(agent, *, tool_name, tool_description):
    """Recommendation is wrapped with a SECOND required argument, unlike every other
    specialist (23-Aug-26, T105 follow-up).

    Why this one differs: recommend_actions() requires today's written diagnosis as a
    real input and returns upstream_missing without it, so Monolith -- which binds the
    raw tool -- sees `diagnosis_summary` as a required parameter in its function-calling
    schema and cannot miss it. Planner-Executor's uniform `(request: str)` wrapper hid
    that requirement completely: the coordinator would have had to infer that ~18k chars
    of diagnosis belonged inside a free-text task field, which it never does for any
    specialist. Observed run f6a7bdd8 -- every sub-agent request was 160-270 chars, the
    recommendation specialist consequently had no diagnosis to pass on, recommend_actions
    returned upstream_missing, and the run produced an empty recommended_actions list.

    That was an asymmetry in how the two conditions were TOLD about the dependency, not a
    difference in how well they orchestrate -- a measurement artifact that would have
    handicapped Planner-Executor in the comparison. Exposing the parameter here holds both
    conditions to the same discoverable contract.

    Per-parameter guidance MUST live in Annotated[...], not in the function docstring
    (23-Aug-26 follow-up, run 4ee0151f). agent_framework's @tool decorator only reads the
    whole-function docstring as the tool-level `description` (agent_framework/_tools.py,
    `tool_desc: str = description or (f.__doc__ or "")`) -- it does not parse per-argument
    text out of it into the JSON schema. The original version of this wrapper put the
    "pass in full, verbatim, do not summarise" instruction in the docstring under a
    `diagnosis_summary:` line; that text never reached the model. Confirmed against
    run_store: diagnose's own diagnosis_summary field was 4,344 chars, but
    recommendation_tool's combined (request + diagnosis_summary) input_chars was 930 --
    the master had silently paraphrased it down, because nothing in its function-calling
    schema told it not to. Annotated[str, "..."] is read into the schema and is the only
    place this instruction can actually reach the model.
    """
    @tool(name=tool_name, description=tool_description)
    async def _run_sub_agent(
        request: Annotated[
            str,
            "The full task for this specialist, including any file paths from the "
            "user's message verbatim.",
        ],
        diagnosis_summary: Annotated[
            str,
            "Today's delay-pattern diagnosis, copied in FULL and VERBATIM from the "
            "diagnosis specialist's own diagnosis_summary field -- typically several "
            "thousand characters of prose, not a few sentences. Recommendations are "
            "grounded in this text and cannot be produced without it. Do NOT "
            "summarise, shorten, paraphrase, or substitute your own wording: paste "
            "the entire diagnosis_summary text you received, unchanged.",
        ],
    ) -> str:
        task = (
            f"{request}\n\n"
            f"--- Today's delay diagnosis (pass this to your tool verbatim) ---\n"
            f"{diagnosis_summary}"
        )
        return await _invoke_sub_agent(agent, tool_name, task)
    return _run_sub_agent

# ---------------------------------------------------------------------------
# Wrap sub-agents as tools for the master orchestrator agent. Each sub-agent is a 
# tool with its own name and description.
# ---------------------------------------------------------------------------

predict_delivery_delays_tool = _wrap_as_tool(
    predict_delivery_delays_agent,
    tool_name="predict_delivery_delays_tool",
    tool_description=TOOL_DESCRIPTIONS["predict_delivery_delays_tool"],
)

diagnose_delay_patterns_tool = _wrap_as_tool(
    diagnose_delay_patterns_agent,
    tool_name="diagnose_delay_patterns_tool",
    tool_description=TOOL_DESCRIPTIONS["diagnose_delay_patterns_tool"],
)

delay_simulations_tool = _wrap_as_tool(
    delay_simulation_agent,
    tool_name="delay_simulations_tool",
    tool_description=TOOL_DESCRIPTIONS["delay_simulations_tool"],
)

recommendation_tool = _wrap_recommendation_as_tool(
    recommendation_agent,
    tool_name="recommendation_tool",
    tool_description=TOOL_DESCRIPTIONS["recommendation_tool"],
)

email_alert_tool = _wrap_as_tool(
    email_alert_agent,
    tool_name="email_alert_tool",
    tool_description=TOOL_DESCRIPTIONS["email_alert_tool"],
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


# MAF's Agent has no handoffs param — handoff is an orchestration-level pattern. 
# Faithful workaround: expose the fallback advisor as a tool with the handoff description

TOPOLOGY = "planner_executor"


def build_master() -> Agent:
    """Construct the planner-executor master agent.

    A factory rather than a module-level object so the coordinator prompt is read at
    call time from prompts/coordinators/planner_executor/master.md. Building at import
    time would freeze one topology's prompt into the module and make the topology
    unselectable per run.
    """
    return Agent(
        name="Supply Chain Last-Mile Delivery Optimization Expert Agent",
        instructions=get_instruction("master", topology=TOPOLOGY),
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


# Back-compat for existing importers (delivery_chat_app.py). New code should call
# build_master() via topologies.registry.resolve(<name>).builder().
supply_chain_delivery_master_agent = build_master()
