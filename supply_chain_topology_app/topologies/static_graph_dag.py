"""
Static-Graph DAG topology (T39) — the dependency graph declared as a MAF Workflow.

The graph is expressed with the framework's own primitive, `WorkflowBuilder`: edges are
declared up front, `add_fan_out_edges` sends a completed capability to every dependent
at once, and `add_fan_in_edges` holds a capability until all of its prerequisites have
finished. Scheduling is the framework's usage of DAG.

Edges are DERIVED from measurement/dependencies.py's TRUE_DEPENDENCIES, never written
out: one prerequisite becomes a plain edge, several become a fan-in group, and
capabilities sharing a prerequisite become one fan-out group. That table is the verified
graph the dependency checker judges runs against, so restating it here would let the two
drift.

Each node is a custom `Executor` rather than a bare agent. An agent dropped into a
workflow consumes the conversation flowing along the edge; a specialist here must be
invoked the way every other condition invokes it — one isolated, self-contained task
string. The node builds that string, calls the specialist through
`run_agent_as_tool_call`, and passes only a completion signal downstream. Results travel
in the run's own dict, not through the graph, so no specialist ever sees another's
conversation.

Scope: an in-scope action request always runs the whole graph. No model narrows it to a
subset or reorders it — that is faithful to the pattern and is itself measurable: on a
narrow query this condition does work the model-driven conditions skip, visible as
`unprompted` tool calls.

Turn 1 is a tools-off classification call (`coordinator.md`) — not a plan, since there
is nothing to choose: it only sorts the message into refuse / answer informationally /
proceed. It exists so this condition applies the same security and conversational
rules every other coordinator does, rather than running all five specialists against
whatever text arrives, unexamined. Turn 2 runs the workflow (only if turn 1 set
`proceed=true`), then a tools-off aggregator writes MasterOutput.

Design rationale is in config/prompts/coordinators/static_graph_dag/README.md.

Interfaces this module must satisfy
-------------------------------------
execute_topology.py calls `create_session()` once, then `run(msg, session=,
middleware=)` twice, and reads `.value`/`.text` off each response.

If turn 1 classifies the message as a refusal or an informational answer, turn 2's
harness message ("Yes, proceed.") has nothing to confirm — `run()` returns the turn-1
response again rather than running the graph.

A Workflow exposes no per-edge hook for `recorder.middleware`, so each node drives
it through `run_agent_as_tool_call`, producing tool_call rows under the same canonical
names, with the same timings, as every other condition — including the overlapping
offsets the concurrency report needs.

The hand-off is made in code: `recommend` requires the diagnosis verbatim, so
`_TASK_BUILDERS` injects diagnose's own `diagnosis_summary` into recommend's task.
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

from config import get_instruction
from core.agents import (
    predict_delivery_delays_agent, diagnose_delay_patterns_agent,
    delay_simulation_agent, recommendation_agent, email_alert_agent,
)
from core.clients import chat_client
from core.schemas import MasterOutput, TriageDecision
from measurement.dependencies import TRUE_DEPENDENCIES
from measurement.instrumentation import run_agent_as_tool_call, serialize_agent_value

TOPOLOGY = "static_graph_dag"

# canonical tool name -> the domain sub-agent providing it. Keys are exactly
# TRUE_DEPENDENCIES' keys, so the graph and the dependency checker cannot disagree.
_AGENTS = {
    "predict_delivery_delays_tool": predict_delivery_delays_agent,
    "diagnose_delay_patterns_tool": diagnose_delay_patterns_agent,
    "delay_simulations_tool":       delay_simulation_agent,
    "recommendation_tool":          recommendation_agent,
    "email_alert_tool":             email_alert_agent,
}


# TriageDecision (core/schemas.py) is turn 1's only output here. `proceed` drives
# control flow in code; `chat_response` is the refusal or informational text when
# `proceed` is False, and is otherwise replaced by `_FIXED_PLAN` in code — see `run()`.

# The five-capability list never varies by query — this condition does not narrow
# scope — so the plan text is a constant stated in code, not a model decision.
_FIXED_PLAN = (
    "Here's my plan:\n"
    "1. Predict delivery delays\n"
    "2. Diagnose delay patterns\n"
    "3. Simulate what-if scenarios\n"
    "4. Generate optimization recommendations\n"
    "5. Generate customer email alerts\n"
    "Shall I proceed?"
)


def _recommend_task(query: str, results: dict) -> str:
    """recommend_actions() requires today's diagnosis in full, not summarised. Code
    supplies it here because no model is constructing the arguments."""
    diagnosis = getattr(results.get("diagnose_delay_patterns_tool"), "value", None)
    summary = getattr(diagnosis, "diagnosis_summary", "") if diagnosis else ""
    return (f"{query}\n\n"
            f"--- Today's delay diagnosis (pass this to your tool verbatim) ---\n"
            f"{summary}")


# capability -> (query, results so far) -> that specialist's task string. Only
# recommend needs anything beyond the query itself.
_TASK_BUILDERS = {"recommendation_tool": _recommend_task}


class _RunContext:
    """Per-run state the nodes share: the query they were asked, the recorder middleware
    to report through, and the results collected so far. Held here rather than passed
    along the graph so that no specialist receives another's output as conversation."""

    def __init__(self, query: str, middleware):
        self.query = query
        self.middleware = middleware
        self.results: dict = {}


class CapabilityNode(Executor):
    """One capability in the graph.

    Two handlers because the builder routes by input type: a capability with a single
    prerequisite receives that one's signal (`str`), while one behind a fan-in group
    receives a list of signals. Both ignore the payload — the message is a completion
    signal, and the task comes from the run context.
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
        build = _TASK_BUILDERS.get(self._capability, lambda q, r: q)
        result = await run_agent_as_tool_call(
            _AGENTS[self._capability], self._capability,
            build(run.query, run.results), run.middleware,
        )
        run.results[self._capability] = result
        await ctx.send_message(self._capability)


def _describe_results(results: dict) -> str:
    """Render what each capability actually returned, not just whether it ran — the
    aggregator needs the real content to write simulate_summary/recommendation_summary/
    email_alert_summary; a boolean flag cannot produce a narrative. predict/diagnose
    are included too so a tool-level error on either is visible to `chat_response`,
    even though the app captures their successful output directly from the tool-call
    stream, not from here."""
    lines = []
    for name in _AGENTS:
        response = results.get(name)
        if response is None:
            lines.append(f"- {name}: did not run")
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
            name="Static-Graph DAG Result Aggregator",
            instructions=get_instruction("aggregator", topology=TOPOLOGY),
            client=chat_client,
            default_options={"temperature": 0, "response_format": MasterOutput},
        )
        await ctx.yield_output(await agent.run(
            f"Original request:\n{self._ctx.query}\n\n"
            f"Each capability below has already run, scheduled in dependency order by "
            f"the graph. Write your narrative fields from what these actually "
            f"returned:\n\n{_describe_results(self._ctx.results)}"
        ))


def _build_workflow(run: _RunContext):
    """Declare the graph from TRUE_DEPENDENCIES and let the framework schedule it.

    One prerequisite -> a plain edge. Several -> a fan-in group, so the capability waits
    for all of them. Capabilities sharing a prerequisite -> one fan-out group, which is
    what makes independent work run concurrently. All five capabilities fan in to the
    aggregator directly, so it runs exactly once, after every one of them -- not just
    the graph's leaves (see the comment below the loop for why that distinction matters).
    """
    nodes = {c: CapabilityNode(c, run) for c in _AGENTS}
    aggregator = AggregatorNode(run)

    roots = [c for c in _AGENTS if not TRUE_DEPENDENCIES.get(c)]
    if len(roots) != 1:
        raise ValueError(
            f"Expected exactly one capability with no prerequisites to start the graph, "
            f"found {roots}. TRUE_DEPENDENCIES must describe a single-rooted DAG for "
            f"this condition."
        )
    builder = WorkflowBuilder(start_executor=nodes[roots[0]])

    # Group dependents by their prerequisite set: a shared single prerequisite becomes
    # one fan-out (concurrent), multiple prerequisites become a fan-in (join).
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

    # Every capability, not just the ones with no dependent -- the aggregator reads
    # every result directly (_describe_results), including diagnose's, even though
    # diagnose also feeds recommend. Fanning in only the graph's leaves (28-Aug-26,
    # since fixed) relied on an accident of this specific shape: recommend cannot start
    # until diagnose finishes, so waiting for recommend incidentally waited for diagnose
    # too. That stops holding the moment a middle node's result is needed without
    # something downstream transitively depending on it -- a node can be a source in
    # more than one edge group (the same mechanism fan-out already relies on), so there
    # is no reason to route through dependents instead of listing every capability.
    builder.add_fan_in_edges([nodes[c] for c in sorted(_AGENTS)], aggregator)
    return builder.build()


class StaticGraphCoordinator:
    """Exposes the Agent surface execute_topology.py drives.

    Turn 1 is a tools-off classification call (coordinator.md): refuse, answer
    informationally, or proceed. It does not choose scope or order — those stay fixed
    in code — it only decides whether the graph should run at all. Turn 2 runs the
    workflow (only if turn 1 set proceed=true) and returns the aggregator's response;
    otherwise it returns turn 1's response again, since there is nothing to confirm.
    """

    def __init__(self):
        self._query: str = ""
        self._triaged = False
        self._proceed = False
        self._final: AgentResponse | None = None   # turn 1's response when proceed=False

    def create_session(self, *, session_id: str | None = None):
        # Required by the harness. Nothing to hold across turns beyond the two flags
        # above: no model spans both turns, and each specialist is invoked with a
        # self-contained task string.
        return None

    async def run(self, message: str, *, session=None, middleware=None) -> AgentResponse:
        if not self._triaged:
            self._triaged = True
            self._query = message
            triage = Agent(
                name="Static-Graph DAG Triage",
                instructions=get_instruction("coordinator", topology=TOPOLOGY),
                client=chat_client,
                default_options={"temperature": 0, "response_format": TriageDecision},
            )
            response = await triage.run(message)
            decision: TriageDecision = response.value
            self._proceed = bool(decision and decision.proceed)
            if not self._proceed:
                self._final = response
                return response
            # The five-capability list is a constant, not a per-query decision (see
            # module docstring) — code states it rather than asking the model to.
            self._final = AgentResponse(
                value=TriageDecision(proceed=True, chat_response=_FIXED_PLAN))
            return self._final

        if not self._proceed:
            # Turn 1 already refused or answered informationally — the harness's
            # "Yes, proceed." has nothing to confirm.
            return self._final

        run = _RunContext(self._query, middleware)
        result = await _build_workflow(run).run(self._query)
        outputs = result.get_outputs()
        if not outputs:
            raise RuntimeError(
                "Static-graph workflow produced no output. The aggregator is the only "
                "node that yields one, so it did not run — check that every leaf "
                "capability completed."
            )
        return outputs[0]


def build_master() -> StaticGraphCoordinator:
    """Entry point used by topologies/registry.py. A factory, not a module-level object,
    so prompts are read at call time rather than frozen at import."""
    return StaticGraphCoordinator()
