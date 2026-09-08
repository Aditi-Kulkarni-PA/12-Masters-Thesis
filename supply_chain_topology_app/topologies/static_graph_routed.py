"""
Static-Graph Routed topology (T108) — an intent router gates a subset of Static-Graph
DAG's graph, keeping the same fan-out/fan-in concurrency.

Where this differs from Static-Graph DAG (T39): DAG's turn 1 only classifies the
message (refuse / inform / proceed) and then always runs all five capabilities — scope
never narrows. This condition's turn 1 does that AND decides which capabilities the
request needs and the order they must run in (`coordinator.md`, `RoutedPlan`), the same
job Sequential's planner does, reasoning dependency order out from the capability
descriptions itself (`@dependency_basics`) rather than having code close the selected
set under `TRUE_DEPENDENCIES` — a router handed the answer would face an easier task
than Sequential's or Planner-Executor's planners, a real difference worth avoiding
rather than working around (see T108 tracker Notes).

Where this differs from Dynamic-Graph (T87): Dynamic-Graph's scope is discovered but
its scheduling has no concurrency — Magentic is structurally one-next_speaker-per-round
(Risk Log R42-R42.2). This condition keeps DAG's real fan-out/fan-in concurrency for
whatever subset the router selects, so it isolates "cheap intent-filtering" from
"parallel execution" on its own axis, rather than conflating the two the way
Dynamic-Graph and DAG each do in opposite directions.

Fan-in design — Option A, "always fire, skip internally" (confirmed 29-Aug-26,
AskUserQuestion)
-----------------------------------------------------------------------------------
`FanInEdgeRunner._is_ready_to_send()` (`agent_framework/_workflows/_edge_runner.py`,
byte-identical in pinned 1.11.0 and latest 1.16.0 — confirmed via direct source diff,
not a version issue) always waits for a buffered message from EVERY configured source,
regardless of any upstream selection. A design that truly excluded a non-selected
specialist from the graph (MAF's `add_multi_selection_edge_group`) would leave the
shared final fan-in waiting on a source that never sends — a real deadlock risk,
confirmed against MS's own multi-selection sample, which never shares a fan-in across
conditionally-selected branches.

This module keeps `_build_workflow()`'s graph IDENTICAL to Static-Graph DAG's —
unconditional `add_fan_out_edges`/`add_fan_in_edges` over all five capabilities, still
derived from `measurement/dependencies.py`'s `TRUE_DEPENDENCIES` — so the shared fan-in
always has all five sources to wait for and can never deadlock, by construction. Routing
happens INSIDE each `CapabilityNode`: a node not in the router's selected set skips the
real work entirely (no LLM call, no tool call, near-zero cost) and still sends its
completion signal downstream, so the join is satisfied exactly as if every capability
had run. The measured cost difference between a routed run and DAG's always-all-five run
is therefore the LLM/tool cost of the skipped capabilities, not a change in graph shape.

What this means for T108.1's original "hard runtime check for a deadlocked selection":
that requirement targeted a true-exclusion design (Option B, rejected) where an
unsatisfiable selection really could hang the workflow. Under Option A the framework
never waits on a node that won't send, so that specific hang cannot occur — superseded
by the fan-in design decision, not silently dropped. A DIFFERENT, non-hanging failure
mode remains possible and is deliberately NOT guarded against in code: the router can
select a capability without selecting its prerequisite (e.g. `recommend` without
`diagnose`) — the prerequisite's node still fires but skips itself, so the dependent
runs against an empty upstream result. Auto-closing the selection under
`TRUE_DEPENDENCIES` would silently fix this and erase the very thing under measurement
(whether the router derives dependency order correctly). Left alone, it surfaces as an
ordinary "missing" dependency violation in `measurement/dependencies.py`'s
`check_dependencies()` — the same mechanism, and the same finding-not-defect
classification, already applied to Dynamic-Graph's R42.1.

Turn 1 is a single tools-off model call (`coordinator.md`) combining what DAG's
`coordinator.md` and Sequential's `sequence_planner.md` do separately: refuse / inform /
proceed, AND — only when proceeding — which capabilities and in what order. Turn 2 runs
the workflow (only if turn 1 set `proceed=true`), then a tools-off aggregator writes
MasterOutput from whichever capabilities actually ran.

Design rationale is in config/prompts/coordinators/static_graph_routed/README.md.

Interfaces this module must satisfy
-------------------------------------
execute_topology.py calls `create_session()` once, then `run(msg, session=,
middleware=)` twice, and reads `.value`/`.text` off each response.

If turn 1 classifies the message as a refusal or an informational answer, turn 2's
harness message ("Yes, proceed.") has nothing to confirm — `run()` returns the turn-1
response again rather than running the graph.

A Workflow exposes no per-edge hook for `recorder.middleware`, so a node that actually
runs its capability drives it through `run_agent_as_tool_call`, producing tool_call rows
under the same canonical names, with the same timings, as every other condition. A
skipped node calls neither the model nor `run_agent_as_tool_call`, so it produces no
tool_call row at all — indistinguishable, by design, from a capability that simply never
ran, which is exactly what "not selected" means for measurement purposes.
"""

import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from dotenv import load_dotenv, find_dotenv
load_dotenv(dotenv_path=find_dotenv(), override=False)

from agent_framework import (
    Agent, AgentResponse, Executor, WorkflowBuilder, WorkflowContext, handler,
)
from pydantic import BaseModel, Field

from config import get_instruction
from core.agents import (
    predict_delivery_delays_agent, diagnose_delay_patterns_agent,
    delay_simulation_agent, recommendation_agent, email_alert_agent,
)
from core.clients import chat_client
from core.schemas import MasterOutput
from core.tool_descriptions import TOOL_NAME_BY_CAPABILITY
from measurement.dependencies import TRUE_DEPENDENCIES
from measurement.instrumentation import run_agent_as_tool_call, serialize_agent_value
from topologies.sequential import Capability

# Capability is already a Literal, not a free string — it's imported directly from topologies/sequential.py:
# Capability = Literal["predict", "diagnose", "simulate", "recommend", "email"]

TOPOLOGY = "static_graph_routed"

# canonical tool name -> the domain sub-agent providing it. Identical roster to
# Static-Graph DAG's -- the graph shape does not change, only which nodes do real work.
_AGENTS = {
    "predict_delivery_delays_tool": predict_delivery_delays_agent,
    "diagnose_delay_patterns_tool": diagnose_delay_patterns_agent,
    "delay_simulations_tool":       delay_simulation_agent,
    "recommendation_tool":          recommendation_agent,
    "email_alert_tool":             email_alert_agent,
}


class RoutedPlan(BaseModel):
    """Turn 1's only output. Combines what Static-Graph DAG's TriageDecision and
    Sequential's CapabilityPlan each do separately: a proceed/refuse/inform gate AND,
    only when proceeding, the capabilities needed and the order they must run in."""
    proceed: bool = Field(
        description="True only for an in-scope action request. False for a refusal "
                    "or an informational answer already written in chat_response.")
    chat_response: str = Field(
        default="", description="Refusal or informational answer when proceed is "
                    "False. When proceed is True, the action plan itself.")
    capabilities: list[Capability] = Field(
        default_factory=list,
        description="ONLY meaningful when proceed is True: the capabilities this "
                    "request needs, in the order they must run. The five allowed "
                    "values are the full roster to choose FROM, not a list to "
                    "reproduce. Leave empty when proceed is False.",
    )


def _recommend_task(query: str, results: dict) -> str:
    """recommend_actions() requires today's diagnosis in full, not summarised. Code
    supplies it here because no model is constructing the arguments.

    If diagnose was not selected this run, results has no entry for it and summary is
    empty -- recommend then runs against a blank diagnosis, which is the router's own
    missing-prerequisite mistake surfacing as measured data, not something this
    function papers over. See the module docstring."""
    diagnosis = getattr(results.get("diagnose_delay_patterns_tool"), "value", None)
    summary = getattr(diagnosis, "diagnosis_summary", "") if diagnosis else ""
    return (f"{query}\n\n"
            f"--- Today's delay diagnosis (pass this to your tool verbatim) ---\n"
            f"{summary}")


# capability -> (query, results so far) -> that specialist's task string. Only
# recommend needs anything beyond the query itself.
_TASK_BUILDERS = {"recommendation_tool": _recommend_task}


class _RunContext:
    """Per-run state the nodes share: the query, the recorder middleware, the router's
    selected set (canonical tool names), and results collected so far. Held here rather
    than passed along the graph so that no specialist receives another's output as
    conversation."""

    def __init__(self, query: str, middleware, selected: set[str]):
        self.query = query
        self.middleware = middleware
        self.selected = selected
        self.results: dict = {}


class CapabilityNode(Executor):
    """One capability in the graph, always present regardless of routing (Option A —
    see module docstring). A capability the router did not select skips the real work
    (no LLM call, no tool call) and still forwards its completion signal, so the shared
    fan-in downstream is satisfied exactly as if every capability had run.
    """

    def __init__(self, capability: str, ctx: _RunContext):
        super().__init__(id=capability)
        self._capability = capability
        self._ctx = ctx

    @handler
    async def from_single(self, _signal: str, ctx: WorkflowContext[str]) -> None:
        await self._execute(ctx)

    @handler
    async def from_many(self, _signals: list[str], ctx: WorkflowContext[str]) -> None:
        await self._execute(ctx)

    async def _execute(self, ctx: WorkflowContext[str]) -> None:
        run = self._ctx
        if self._capability in run.selected:
            build = _TASK_BUILDERS.get(self._capability, lambda q, r: q)
            result = await run_agent_as_tool_call(
                _AGENTS[self._capability], self._capability,
                build(run.query, run.results), run.middleware,
            )
            run.results[self._capability] = result
        # Not selected: no entry in run.results, no tool_call row -- the app's own
        # definition of "did not run", same as every other condition.
        await ctx.send_message(self._capability)


def _describe_results(selected: set[str], results: dict) -> str:
    """Render what each capability actually returned, distinguishing three states the
    aggregator must not conflate: not selected by the router (never attempted), selected
    but produced no result (an error the run didn't otherwise raise), and selected with
    a real result."""
    lines = []
    for name in _AGENTS:
        if name not in selected:
            lines.append(f"- {name}: not selected by the router")
            continue
        response = results.get(name)
        if response is None:
            lines.append(f"- {name}: selected but did not run")
        else:
            lines.append(f"- {name}:\n{serialize_agent_value(response)}")
    return "\n\n".join(lines)


class AggregatorNode(Executor):
    """Terminal node: one tools-off model turn writing MasterOutput from what ran."""

    def __init__(self, ctx: _RunContext):
        super().__init__(id="aggregator")
        self._ctx = ctx

    @handler
    async def summarise(self, _signals: list[str],
                        ctx: WorkflowContext[None, AgentResponse]) -> None:
        agent = Agent(
            name="Static-Graph Routed Result Aggregator",
            instructions=get_instruction("aggregator", topology=TOPOLOGY),
            client=chat_client,
            default_options={"temperature": 0, "response_format": MasterOutput},
        )
        selected_readable = sorted(n for n in _AGENTS if n in self._ctx.selected)
        await ctx.yield_output(await agent.run(
            f"Original request:\n{self._ctx.query}\n\n"
            f"Router-selected capabilities this run: "
            f"{', '.join(selected_readable) or '(none)'}\n\n"
            f"Write your narrative fields only from what these actually "
            f"returned:\n\n{_describe_results(self._ctx.selected, self._ctx.results)}"
        ))


def _build_workflow(run: _RunContext):
    """Declare the graph from TRUE_DEPENDENCIES -- IDENTICAL shape to Static-Graph
    DAG's, unconditional, regardless of what the router selected. See the module
    docstring for why routing lives inside each node instead of in the graph."""
    nodes = {c: CapabilityNode(c, run) for c in _AGENTS}
    aggregator = AggregatorNode(run)

    roots = [c for c in _AGENTS if not TRUE_DEPENDENCIES.get(c)]
    if len(roots) != 1:
        raise ValueError(
            f"Expected exactly one capability with no prerequisites to start the "
            f"graph, found {roots}. TRUE_DEPENDENCIES must describe a single-rooted "
            f"DAG for this condition."
        )
    builder = WorkflowBuilder(start_executor=nodes[roots[0]])

    by_prereqs: dict[tuple, list[str]] = {}
    for cap in _AGENTS:
        prereqs = tuple(sorted(TRUE_DEPENDENCIES.get(cap, ())))
        if prereqs:
            by_prereqs.setdefault(prereqs, []).append(cap)

    for prereqs, dependents in by_prereqs.items():
        if len(prereqs) == 1:
            builder.add_fan_out_edges(nodes[prereqs[0]],
                                      [nodes[d] for d in sorted(dependents)])
        else:
            for dependent in sorted(dependents):
                builder.add_fan_in_edges([nodes[p] for p in prereqs], nodes[dependent])

    # Every capability, not just leaves, feeds the aggregator directly -- same reasoning
    # as Static-Graph DAG's identically-shaped final fan-in.
    builder.add_fan_in_edges([nodes[c] for c in sorted(_AGENTS)], aggregator)
    return builder.build()


class StaticGraphRoutedCoordinator:
    """Exposes the Agent surface execute_topology.py drives.

    Turn 1 (coordinator.md, RoutedPlan) both classifies the message and, for an
    in-scope action request, selects scope and order -- one model call doing what DAG's
    triage and Sequential's planner do in separate files. Turn 2 runs the workflow
    (only if turn 1 set proceed=true) and returns the aggregator's response; otherwise
    it returns turn 1's response again, since there is nothing to confirm.
    """

    def __init__(self):
        self._triaged = False
        self._proceed = False
        self._query: str = ""
        self._selected: set[str] = set()
        self._final: AgentResponse | None = None   # turn 1's response when proceed=False

    def create_session(self, *, session_id: str | None = None):
        # Required by the harness. No model spans both turns and each specialist is
        # invoked with a self-contained task string, so there is nothing to hold here.
        return None

    async def run(self, message: str, *, session=None, middleware=None) -> AgentResponse:
        if not self._triaged:
            return await self._route_turn(message)
        # Turn 2 used to replay the stored response whenever turn 1 declined, so a
        # coordinator that asked a question never received the answer: the harness sent
        # one and this early return discarded it. Turn 1's decision was final by
        # construction, which made "cannot act on a clarification" and "was never given
        # one" indistinguishable in the data (7-Sep-26).
        #
        # A declined turn 1 now re-enters the coordinator once, with the original query
        # and the harness's turn-2 message together, so the decision is retaken with the
        # information a conversation would have supplied. Retry is capped at one: the
        # harness sends two turns, and a topology that declines twice has answered.
        # A turn 1 that proceeded is untouched -- the stored response is still replayed.
        if not self._proceed:
            if not getattr(self, "_retried", False):
                # Re-decide in place and fall through to execution. Recursing into run()
                # instead returns the routing response, because routing is turn-1
                # behaviour and execution belongs to the turn after it -- there is no
                # turn after this one. Observed 8-Sep-26: the retry re-routed correctly,
                # reported proceed=true and a three-capability plan, then returned it and
                # ran nothing.
                self._retried = True
                self._triaged = False
                combined = f"{self._query}\n\n{message}" if self._query else message
                await self._route_turn(combined)
                if not self._proceed:
                    return self._final
            else:
                # Declined twice: the second decision stands.
                return self._final

        run = _RunContext(self._query, middleware, self._selected)
        result = await _build_workflow(run).run(self._query)
        outputs = result.get_outputs()
        if not outputs:
            raise RuntimeError(
                "Static-graph-routed workflow produced no output. The aggregator is "
                "the only node that yields one, so it did not run -- check that every "
                "capability's node (selected or skipped) completed and sent its "
                "signal."
            )
        return outputs[0]

    async def _route_turn(self, message: str) -> AgentResponse:
        """Turn 1: classify and, for an action request, select scope and order."""
        self._triaged = True
        self._query = message
        router = Agent(
            name="Static-Graph Routed Router",
            instructions=get_instruction("coordinator", topology=TOPOLOGY),
            client=chat_client,
            default_options={"temperature": 0, "response_format": RoutedPlan},
        )
        response = await router.run(message)
        decision: RoutedPlan = response.value
        self._proceed = bool(decision and decision.proceed)
        if self._proceed:
            capabilities = (decision.capabilities or []) if decision else []
            self._selected = {
                TOOL_NAME_BY_CAPABILITY[c] for c in capabilities
                if c in TOOL_NAME_BY_CAPABILITY
            }
            print(f"  >> router selected: {' -> '.join(capabilities) or '(none)'}",
                  flush=True)
        else:
            self._final = response
        return response


def build_master() -> StaticGraphRoutedCoordinator:
    """Entry point used by topologies/registry.py. A factory, not a module-level
    object, so prompts are read at call time rather than frozen at import."""
    return StaticGraphRoutedCoordinator()
