"""
Sequential topology (T38) — plan once, then execute that plan rigidly.

Turn 1: a tool-less sequence planner Agent (sequence_planner.md) returns the capabilities
needed, in execution order, plus the plan text. Its AgentResponse is returned as-is,
so `.value.chat_response` carries the plan and `.value.capabilities` the step list.

Turn 2: a master Agent is built with only the planned tools and driven one turn per
step, `tool_choice` forced to that step's tool by name, then a closing turn with tools
off returns MasterOutput. Forcing one named tool per turn is what keeps execution to
one tool at a time in plan order — MAF would otherwise let the master emit several
tool calls in a turn and run them concurrently.

The five specialist tools are imported from topologies/planner_executor.py and used
unmodified.

Design rationale is in config/prompts/coordinators/sequential/README.md.

Interfaces this module must satisfy
-------------------------------------
execute_topology.py calls `create_session()` once, then `run(msg, session=,
middleware=)` twice — turn 1 with the query, turn 2 with "Yes, proceed." — and reads
`.value`/`.text` off each response, so both turns return real AgentResponse objects.

`recorder.middleware` attaches to the master's own tool-calling loop and produces
tool_call rows keyed on the canonical names in measurement/dependencies.py.

Token accounting: the harness sums usage over the two responses it receives, so the
forced turns in between would go uncounted. Each is recorded through
`record_sub_agent_usage` under `_COORDINATOR_USAGE`, which reaches the run total but
places coordinator tokens in the sub-agent bucket — analysis separating the two must
filter that name out.
"""

import sys
from pathlib import Path
from typing import Literal

_APP_DIR = Path(__file__).resolve().parent.parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from dotenv import load_dotenv, find_dotenv
load_dotenv(dotenv_path=find_dotenv(), override=False)

from agent_framework import Agent
from agent_framework import AgentResponse
from pydantic import BaseModel, Field

from config import get_instruction
from core.clients import chat_client, make_chat_client
from core.schemas import MasterOutput
from measurement.instrumentation import record_sub_agent_usage
from topologies.planner_executor import (
    predict_delivery_delays_tool, diagnose_delay_patterns_tool,
    delay_simulations_tool, recommendation_tool, email_alert_tool,
)

TOPOLOGY = "sequential"

Capability = Literal["predict", "diagnose", "simulate", "recommend", "email"]

# label -> (wrapped tool object, canonical tool name). The names must match
# measurement/dependencies.py's TRUE_DEPENDENCIES keys -- the dependency checker and the
# analysis scripts key on them.
_TOOLS: dict[str, tuple[object, str]] = {
    "predict":   (predict_delivery_delays_tool, "predict_delivery_delays_tool"),
    "diagnose":  (diagnose_delay_patterns_tool, "diagnose_delay_patterns_tool"),
    "simulate":  (delay_simulations_tool, "delay_simulations_tool"),
    "recommend": (recommendation_tool, "recommendation_tool"),
    "email":     (email_alert_tool, "email_alert_tool"),
}

# Fallback plan, used when the planner returns nothing usable.
_FULL_PLAN: list[str] = ["predict", "diagnose", "simulate", "recommend", "email"]

# Usage-record name for a forced coordinator turn. Deliberately not a canonical tool
# name, so analysis can tell coordinator turns from specialist calls.
_COORDINATOR_USAGE = "sequential_coordinator_turn"

# Sent on the closing turn, in place of the harness's "Yes, proceed." -- see
# _execute_turn for why that message cannot be reused once tools are off.
_CLOSING_MESSAGE = (
    "Every planned step has been attempted and its result is in this conversation. "
    "Write the final structured response from what actually happened, including any "
    "step that returned an error or no data."
)


class CapabilityPlan(BaseModel):
    """Turn 1's output: the plan as text for the harness to read, and as data for the
    executor to follow."""
    chat_response: str = Field(
        default="",
        description="The action plan, in the standard 'Here's my plan: ... Shall I "
                    "proceed?' format.")
    capabilities: list[Capability] = Field(
        default_factory=list,
        description="ONLY the capabilities this request actually needs, in the order "
                    "they must run. The five allowed values are the full roster to "
                    "choose FROM, not a list to reproduce — most requests need two or "
                    "three. Include a capability only if the request asks for its "
                    "outcome, or another included capability consumes its output.")


def _build_sequence_planner() -> Agent:
    """Turn 1's agent — decides scope and order, calls nothing, sees no results.

    Deliberately tool-less. Binding the five tools with tool_choice "none" was tried, to
    match the channel through which Planner-Executor's master receives the capability
    descriptions, and it did change behaviour — but from omitting a prerequisite to
    selecting every capability regardless of the request, on a query implying three. The
    capability descriptions reach this agent through @tool_capabilities in its prompt;
    scope is constrained by @scope_selection.
    """
    return Agent(
        name="Sequence Planner",
        instructions=get_instruction("sequence_planner", topology=TOPOLOGY),
        client=chat_client,
        default_options={"temperature": 0, "response_format": CapabilityPlan},
    )


def _build_master(plan: list[str]) -> Agent:
    """Build the executing agent, carrying only the tools *plan* names.

    max_function_calls=1 is required, not tuning. MAF re-applies tool_choice on every
    iteration of its internal tool-calling loop and only exits early when the model
    returns no function call — impossible while a tool is forced — so without this cap a
    forced turn would repeat that tool up to max_iterations (40) times. On reaching the
    cap the framework switches tool_choice to "none", letting the model write its
    response and the loop finish: exactly one tool call per turn.

    tool_choice defaults to "none": every turn overrides it with one forced tool name,
    so a turn that failed to would produce no tool call at all rather than silently
    letting the master pick one.
    """
    return Agent(
        name="Sequential Coordinator",
        instructions=get_instruction("coordinator", topology=TOPOLOGY),
        client=make_chat_client({"max_function_calls": 1}),
        tools=[_TOOLS[label][0] for label in plan],
        default_options={"tool_choice": "none", "response_format": MasterOutput,
                          "temperature": 0},
    )


class SequentialCoordinator:
    """Exposes the Agent surface execute_topology.py drives: turn 1 plans, turn 2
    executes the plan and returns the final response."""

    def __init__(self):
        self._plan: list[str] = []
        self._query: str = ""

    def create_session(self, *, session_id: str | None = None):
        # Required by the harness; this topology keeps no cross-turn state of its own,
        # and the session the tool calls share is the master's, created on turn 2.
        return None

    async def run(self, message: str, *, session=None, middleware=None) -> AgentResponse:
        # Turn 2's message is the harness's "Yes, proceed." and carries no information
        # this topology needs -- the plan and the query are already held.
        if not self._plan:
            return await self._plan_turn(message)
        return await self._execute_turn(middleware)

    async def _plan_turn(self, message: str) -> AgentResponse:
        """Turn 1: plan, and return the sequence planner's response for the harness."""
        self._query = message
        response = await _build_sequence_planner().run(message)
        planned = getattr(response.value, "capabilities", None) or []
        self._plan = [c for c in planned if c in _TOOLS] or list(_FULL_PLAN)
        print(f"  >> plan: {' -> '.join(self._plan)}", flush=True)
        return response

    async def _execute_turn(self, middleware) -> AgentResponse:
        """Turn 2: one forced turn per planned step, then a closing turn with tools off.

        Nothing is caught here — an exception ends the run, as it does for every other
        topology. Tool-level problems (upstream_missing, empty results) are ordinary
        return values, not exceptions.
        """
        master = _build_master(self._plan)
        session = master.create_session()

        for label in self._plan:
            tool_name = _TOOLS[label][1]
            response = await master.run(
                self._query, session=session, middleware=middleware,
                options={"tool_choice": {"mode": "required",
                                          "required_function_name": tool_name}},
            )
            record_sub_agent_usage(_COORDINATOR_USAGE, response)

        # The closing call with tool_choice: "none" is where the four narrative fields 
        # get produced from the whole session.
        # The closing turn cannot reuse the harness's "Yes, proceed." message: with
        # tools off the model reads it as a request it cannot fulfil and reports being
        # unable to proceed, summarising only the last tool result. It is told the work
        # is finished instead. Nothing capability-specific is composed here.
        return await master.run(_CLOSING_MESSAGE, session=session, middleware=middleware,
                                 options={"tool_choice": "none"})


def build_master() -> SequentialCoordinator:
    """Entry point used by topologies/registry.py. A factory, not a module-level
    object, so prompts are read at call time rather than frozen at import."""
    return SequentialCoordinator()
