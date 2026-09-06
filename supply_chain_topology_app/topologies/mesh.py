"""
Mesh B topology (T40) — concurrent peer-to-peer messaging, no coordinator, no owner.

Each capability is an executor node in a MAF `WorkflowBuilder` graph with edges running in
BOTH directions between peers that need to communicate. A node runs its own capability,
then decides for itself which peers should act next and messages them; the receiver acts
on that message on its own account and does NOT return to the sender. Nobody retains
responsibility for the request, and no node ever sees the whole task.

Why not agent-as-tools 
-----------------------------------------------------------------------------------
The first version of this module wrapped peers as callable tools. That is a genuinely
different pattern, and MAF's own documentation says so: "In agent-as-tools, the primary
agent retains overall responsibility for the task, while other agents are treated as
tools… the primary agent manages the overall context and might provide only relevant
information to the tool agents." Three concrete faults followed. `predict` was both entry
AND the run's final responsible party, since every call returned up to it and its response
became the answer — a root that owns the outcome, i.e. a dynamically-shaped hierarchy.
Callees were subroutines: they saw only the string the caller chose to pass, returned, and
stopped. And there was no shared artifact and no iteration, where the literature's
canonical mesh is a feedback loop among peers. The result would have blurred the contrast
against Planner-Executor rather than sharpening it, which is the whole purpose of the
condition. Peer messaging over real graph edges fixes all three.

Why not MAF's HandoffBuilder either
-----------------------------------
Handoff transfers full task OWNERSHIP (exactly one agent ever active) and its
`_clone_chat_agent()` forces `allow_multiple_tool_calls=False` on every participant —
both confirmed by reading the installed 1.11.0 source. That is routing, not peer
collaboration. Preserved separately as Mesh A, a candidate condition; pairing the two
would isolate concurrency as a single variable.

Cycles are legal here. Confirmed against the installed source: nothing in
`agent_framework/_workflows/` rejects cyclic graphs — the only acyclicity restriction is
on `add_chain()`, a convenience helper this module does not use.

Contrast with Static-Graph DAG — the point of the condition
-----------------------------------------------------------
SAME execution engine, so concurrency is measured on identical machinery. DAG fans out to
diagnose/simulate/email because CODE COMPILES that edge. Mesh reaches the same three
because PREDICT DECIDED TO and addressed them. Compiled edges versus agent-decided
messaging, everything else held constant.
"""
import json
from dataclasses import dataclass, field

from agent_framework import (
    Agent, AgentResponse, Executor, WorkflowBuilder, WorkflowContext, handler,
)
from pydantic import BaseModel, Field

from config import get_instruction
from core.clients import chat_client
from core.agents import (
    build_predict_agent, build_diagnose_agent, build_simulate_agent,
    build_recommend_agent, build_email_agent,
)
from core.schemas import MasterOutput
from core.tool_descriptions import CAPABILITY_DESCRIPTIONS, TOOL_NAME_BY_CAPABILITY
# NOTE: TRUE_DEPENDENCIES is deliberately NOT imported. This condition's communication
# graph is complete and owes nothing to the dependency table -- see _peer_edges(). The
# table still governs what each capability consumes (stated to every agent through the
# capability list) and is what check_dependencies() judges runs against.
from measurement.instrumentation import (
    run_agent_as_tool_call, run_sub_agent, serialize_agent_value,
)

TOPOLOGY = "mesh"

# Where the request lands. Fixed and documented so entry choice never becomes an
# uncontrolled variable. Entry is not ownership: this node acts and messages on like any
# other, and the final output is assembled from every node's result, not from its reply.
ENTRY_CAPABILITY = "predict"

# Shared budget for peer messages across the whole run, combined rather than per-agent —
# the same limit self_check.md states in prose. With no coordinator and a cyclic graph,
# this is the only thing that can stop a message loop, so it is enforced in code.
HOP_BUDGET = 10

# A capability may run more than once (a peer may legitimately be re-asked after new
# information arrives), but not without limit. Duplicate work is an EXPECTED mesh cost and
# is recorded rather than suppressed; this only stops a cycle from thrashing.
MAX_RUNS_PER_CAPABILITY = 2

# Recorded name for the per-node routing call, so analysis can separate the cost of
# decentralised decision-making from specialist work — the same treatment Sequential gives
# its forced coordinator turns via `sequential_coordinator_turn`.
ROUTING_CALL_NAME = "mesh_peer_routing"


def _peer_edges() -> dict[str, tuple[str, ...]]:
    """capability -> every other capability. A COMPLETE peer graph: n(n-1) = 20 directed
    edges over 5 nodes, every pair connected in both directions.

    Complete peer-to-peer, rather than dependency-derived. An earlier version
    built these edges from TRUE_DEPENDENCIES, connecting only pairs with a hard data
    prerequisite between them. That produced the DAG's own skeleton with reverse edges --
    exactly half the pairs -- and left diagnose<->simulate, recommend<->email,
    diagnose<->email, email<->simulate and recommend<->simulate with NO channel at all.
    Those are not data dependencies, but they are precisely the lateral flows a mesh
    exists to enable: diagnose telling simulate which patterns are worth exploring,
    recommend informing what the customer emails should say.

    Why that mattered rather than being a tuning choice: it made "does decentralised
    routing find useful lateral paths?" unanswerable BY CONSTRUCTION, since Mesh could
    never route anything the DAG could not. Any "Mesh behaves like the DAG" result would
    have been partly built in. 

    Matches the literature's definition (a mesh is the complete graph, up to n(n-1) edges)
    and sits under the 6-8 agent ceiling it cites before Echo Chamber and O(n^2)
    coordination cost become the dominant failure mode.

    Trade-off to state plainly in the methodology, not to hide: against Static-Graph DAG
    this now varies TWO things -- graph shape AND decision mechanism -- not one. Mesh A
    (Handoff: same roster, sole ownership) and Static-Graph Routed are the intermediate
    controls that sit between them.

    TRUE_DEPENDENCIES is deliberately NOT consulted here. It still governs what each
    capability actually consumes, is stated to every agent through the capability list,
    and is what check_dependencies() judges runs against -- so a peer addressed before its
    inputs exist surfaces as an ordinary dependency violation, the finding, rather than
    being prevented by a missing edge.
    """
    caps = tuple(TOOL_NAME_BY_CAPABILITY)
    return {c: tuple(sorted(p for p in caps if p != c)) for c in caps}


PEER_EDGES = _peer_edges()

# Narrative peers. predict and diagnose narrate themselves inside their own schemas;
# SimulationsList / RecommendedActionsList / EmailsList carry no free-text field, so those
# three narratives exist in other conditions only because a coordinator had somewhere to
# put them. Each becomes a single-purpose peer here. NOT the covering aggregator R17
# rejected: none sees more than one capability's output, so none is a hub.
NARRATIVE_PEERS: dict[str, str] = {
    "simulate": "simulate_summary",
    "recommend": "recommend_summary",
    "email": "email_summary",
}

# MasterOutput field each narrative peer fills. Its Field(description=...) is reused
# verbatim as that peer's own schema description, so Mesh's narrative guidance is
# byte-identical to what every other condition receives through response_format — the
# equalization control enforced structurally rather than by copying text that would drift.
_NARRATIVE_FIELD = {
    "simulate_summary": "simulate_summary",
    "recommend_summary": "recommendation_summary",
    "email_summary": "email_alert_summary",
}


def _task_for(cap: str, query: str, results: dict) -> str:
    """The text handed to *cap*'s own agent for this run.

    Fixed 30-Aug-26 after the second live run found `recommend` returning an empty
    actions list twice, with its own domain prompt's Missing-Input Handling message
    ("today's diagnosis was not supplied") -- confirmed as the actual cause, not a
    routing failure: `recommend_actions()` takes `diagnosis_summary` as a REQUIRED
    argument, and `recommendation.md` tells the agent to "pass through the diagnosis
    text you were given in your task" -- but every Mesh node was invoked with only
    `query`, never another capability's actual output. Every other capability's
    underlying tool self-serves from the prediction DB/CSV predict wrote to disk and
    needs nothing beyond the query; `recommend` is the one documented exception.

    static_graph_dag.py already solved this exact problem (`_recommend_task()`) with the
    same reasoning stated there: "The hand-off is made in code... because no model is
    constructing the arguments." This mirrors it rather than reinventing it, using
    Mesh's own `results` dict (keyed by plain capability name, unlike static_graph_dag's
    tool-name keys). If `recommend` is ever addressed before diagnose has actually
    finished -- which the routing fix above should prevent, but does not structurally
    forbid -- this degrades to an empty diagnosis section, which recommend's own
    Missing-Input Handling already reports honestly rather than fabricating.
    """
    if cap != "recommend":
        return query
    diagnosis = getattr(results.get("diagnose"), "value", None)
    summary = getattr(diagnosis, "diagnosis_summary", "") if diagnosis else ""
    return (f"{query}\n\n"
            f"--- Today's delay diagnosis (pass this to your tool verbatim) ---\n"
            f"{summary}")


@dataclass
class PeerMessage:
    """One peer addressing others. `targets` is what makes routing agent-decided: every
    edge carries a condition testing whether its target was addressed, so a node reaches
    only the peers it chose. Addressing several at once is what produces concurrency —
    the framework runs those edges in parallel."""
    sender: str
    targets: tuple[str, ...]
    note: str = ""


@dataclass
class _RunContext:
    """Per-run state shared by the nodes: the query, the recorder middleware to report
    through, results collected so far, and the shared budgets. Held here rather than
    passed along edges, so no specialist receives another's output as conversation."""
    query: str
    middleware: object = None
    results: dict = field(default_factory=dict)
    run_counts: dict = field(default_factory=dict)
    hops_used: int = 0

    def take_hop(self) -> bool:
        if self.hops_used >= HOP_BUDGET:
            return False
        self.hops_used += 1
        return True


def _narrative_schema(prompt_key: str) -> type[BaseModel]:
    """One-field output schema for a narrative peer, described in MasterOutput's own words."""
    desc = MasterOutput.model_fields[_NARRATIVE_FIELD[prompt_key]].description
    return type(f"{prompt_key.title().replace('_', '')}Output", (BaseModel,),
                {"__annotations__": {"summary": str},
                 "summary": Field(default="", description=desc)})


def _routing_schema(capability: str, peers: tuple[str, ...]) -> type[BaseModel]:
    """Schema for one node's routing decision.

    The allowed peers and the rule live in the FIELD DESCRIPTION rather than in prose,
    because that is the text the model is actually held to when filling the schema.
    """
    allowed = ", ".join(peers) if peers else "(none)"
    return type(f"{capability.title()}Routing", (BaseModel,),
                {"__annotations__": {"targets": list[str]},
                 "targets": Field(
                     default_factory=list,
                     description=(
                         f"Which peers should act next, from exactly this set: {allowed}. "
                         f"Name every peer whose contribution the user's request still "
                         f"needs AND whose own inputs are now available — name them "
                         f"together in one list rather than one at a time, because they "
                         f"will then work at the same time. Leave EMPTY if the request "
                         f"has been served, or if the only peers left still lack their "
                         f"inputs. You will be told which capabilities have already been "
                         f"addressed (running or done) and, separately, which have "
                         f"actually finished: never name one already addressed, even if "
                         f"it has not finished yet, and use the finished list -- not the "
                         f"addressed list -- to tell when a peer whose input was missing "
                         f"before has become ready now. Never name anything outside the "
                         f"set above."))})


def _build_agents(entry_prefix: str) -> dict[str, Agent]:
    """The five domain agents plus three narrative peers.

    middleware stays None on every agent, matching Planner-Executor: a peer's own raw MCP
    calls must not become tool_call rows, or Mesh would record roughly double the rows of
    every other condition for identical work. Capability invocations are recorded instead
    by run_agent_as_tool_call() in PeerNode, producing exactly one canonically-named row
    each.
    """
    agents = {
        # Only the entry node carries the security/scope framing every other condition
        # gets in a turn-1 coordinator call, prepended at CONSTRUCTION so the shared
        # domain prompt underneath stays byte-identical to every other condition's copy.
        # tool_choice="required" (R58 REVERTED 6-Sep-26): "auto" let predict take
        # entry_point.md's text-only decline branch on Q1, but live pilot data (5 of 11
        # mesh/Q1-Q11 re-runs) showed it just as often skipping its own tool call on
        # QUERIES THAT WERE IN SCOPE (e.g. Q3, Q7) -- a text-only "here's my plan..."
        # response with an empty result, cascading into every downstream capability
        # reading nothing. That is a worse trade than the one being fixed: guaranteed
        # over-execution on out-of-scope queries (the original defect) versus
        # non-deterministic silent under-execution on in-scope ones (this regression).
        # Reverted to "required" pending a structural (not prompt-only) way to gate
        # entry restraint without touching whether predict's own tool call happens.
        "predict":   build_predict_agent(chat_client, instructions_prefix=entry_prefix,
                                          tool_choice="required"),
        "diagnose":  build_diagnose_agent(chat_client),
        "simulate":  build_simulate_agent(chat_client),
        "recommend": build_recommend_agent(chat_client),
        "email":     build_email_agent(chat_client),
    }
    for cap, prompt_key in NARRATIVE_PEERS.items():
        agents[prompt_key] = Agent(
            name=f"{cap.title()} Narrative",
            description=f"Writes the short narrative accompanying {cap}'s result.",
            client=chat_client,
            instructions=get_instruction(prompt_key, topology=TOPOLOGY),
            tools=[],                      # tools-off: it only rewrites what it is given
            default_options={"temperature": 0,
                              "response_format": _narrative_schema(prompt_key)},
        )
    return agents


def _build_router(capability: str, peers: tuple[str, ...]) -> Agent:
    """A node's own routing agent: tools-off, decides which peers to address next.

    Deliberately a separate small call rather than a field bolted onto the domain schema,
    which would have meant editing shared schemas every other condition also uses. Its
    cost is real and is a genuine property of decentralised decision-making — recorded
    under ROUTING_CALL_NAME so analysis can separate it from specialist work, exactly as
    Sequential's forced coordinator turns are separated.

    Instructions carry coordinators/mesh/mesh_peers.md verbatim (fixed 30-Aug-26 -- it was
    written in Part 1 and was never actually reachable by any agent after the
    WorkflowBuilder rebuild; see the module docstring). It directly includes
    @dependency_basics and @concurrency_policy -- the identical shared text every other
    order-deciding prompt gets -- plus the malformed-input handling every other condition
    gets via exception_handling, wrapped in addressing-specific rules that have no shared
    equivalent (no reply, no repeat-work, hop budget). mesh_peers.md moved out of shared/
    into this topology's own folder 30-Aug-26 (it is Mesh-only content, and this project's
    own established rule -- shared/README.md's R19/R22 corrections -- is that
    topology-only content does not belong in shared/, however convenient the precedent
    filename made it look). It assumes its own capability list is supplied separately,
    above it; peer_lines below is that list, built per node rather than from
    shared/peer_tool_capabilities.md's unfiltered all-5 render, because this node's own
    identity must be excluded from what it may address and that variant does not filter.

    Also carries @scope_selection directly (fixed 30-Aug-26, fourth live run): confirmed
    over-calling on a query needing only predict/diagnose/email -- simulate and recommend
    both ran anyway. entry_point.md already includes @scope_selection, but only predict's
    OWN decision to act reads it; the router is the actual "who else needs to run"
    decision in this condition, the direct equivalent of every other topology's
    coordinator, and it had never been given it, so it only ever reasoned about readiness
    (dependency_basics/concurrency_policy) and never about whether the ORIGINAL request
    asked for a given peer at all -- readiness without scope defaults to routing the full
    graph regardless of what was asked. Reachable structurally: _route()'s own per-call
    message already carries the original query text (query is never truncated en route --
    see the module's own task-passing, unaffected by this fix), so the router now has
    both what scope_selection needs to reason with AND the instruction to do so.
    """
    peer_lines = "\n".join(f"- {p} -- {CAPABILITY_DESCRIPTIONS[p]}" for p in peers)
    return Agent(
        name=f"{capability.title()} Peer Routing",
        description=f"Decides which peers {capability} should address next.",
        client=chat_client,
        instructions=(
            f"You have just completed the {capability} step of a supply-chain delivery "
            f"request.\n\n"
            f"Your peers:\n{peer_lines}\n\n"
            f"{get_instruction('scope_selection')}\n\n"
            f"{get_instruction('mesh_peers', topology=TOPOLOGY)}\n\n"
            f"This request's shared hop budget is {HOP_BUDGET} total messages, across "
            f"every peer, for the whole request -- not {HOP_BUDGET} each.\n\n"
            f"Decide which of them should act now: apply scope above to the request text "
            f"you are given with each call, not just readiness -- a peer whose input is "
            f"ready but whose contribution the request never asked for should not be "
            f"named. Follow your output schema."
        ),
        tools=[],
        default_options={"temperature": 0,
                          "response_format": _routing_schema(capability, peers)},
    )


class PeerNode(Executor):
    """One capability, acting on its own account.

    Runs its capability, has its narrative written if it has one, then decides which peers
    to address and messages them. It does not reply to whoever messaged it — there is no
    caller waiting, which is what distinguishes this from the agent-as-tools version.
    """

    def __init__(self, capability: str, agents: dict[str, Agent], run: _RunContext):
        super().__init__(id=capability)
        self._capability = capability
        self._agents = agents
        self._run = run
        self._peers = PEER_EDGES[capability]
        self._router = _build_router(capability, self._peers)

    @handler
    async def from_entry(self, query: str, ctx: WorkflowContext[PeerMessage]) -> None:
        """The workflow's start executor receives the raw query."""
        await self._act(ctx)

    @handler
    async def from_peer(self, message: PeerMessage, ctx: WorkflowContext[PeerMessage]) -> None:
        """A peer addressed this node. Nothing is returned to the sender."""
        await self._act(ctx, addressed_by=message.sender)

    async def _act(self, ctx: WorkflowContext[PeerMessage], addressed_by: str | None = None) -> None:
        cap, run = self._capability, self._run

        # Re-entry guard. A capability may legitimately run twice; beyond that a cycle is
        # thrashing rather than working. Exceeding it is recorded by the run's tool_call
        # rows either way -- duplicate work is a measured mesh cost, not an error to hide.
        ran = run.run_counts.get(cap, 0)
        if ran >= MAX_RUNS_PER_CAPABILITY:
            print(f"  -- mesh: {cap} declined a {ran + 1}th run, MAX_RUNS_PER_CAPABILITY is {MAX_RUNS_PER_CAPABILITY} "
                  f"(addressed by {addressed_by or 'entry'})", flush=True)
            return
        run.run_counts[cap] = ran + 1
        # Who addressed whom is the trace that shows whether the peer graph was actually
        # used as a graph, or collapsed into one chain. Printed rather than inferred later
        # from timings, which cannot distinguish "B ran after A" from "A addressed B".
        print(f"  -- mesh: {addressed_by or 'entry'} -> {cap} "
              f"(run {ran + 1}, hops {run.hops_used}/{HOP_BUDGET})", flush=True)

        result = await run_agent_as_tool_call(
            self._agents[cap], TOOL_NAME_BY_CAPABILITY[cap], _task_for(cap, run.query, run.results),
            run.middleware)
        run.results[cap] = result

        # A capability whose own schema has no free-text field gets its narrative from its
        # dedicated peer. Single-purpose and single-input: it never sees another
        # capability's output, so it is not a hub.
        if cap in NARRATIVE_PEERS:
            key = NARRATIVE_PEERS[cap]
            narrative = await run_agent_as_tool_call(
                self._agents[key], f"{key}_tool", serialize_agent_value(result), run.middleware)
            run.results[key] = narrative

        await self._route(ctx)

    async def _route(self, ctx: WorkflowContext[PeerMessage]) -> None:
        """Decide which peers to address, and address them. Silence ends this branch."""
        if not self._peers or not self._run.take_hop():
            return
        # Fixed 30-Aug-26 after the first live run: recommend was never once named across
        # 8 routing decisions, and predict/diagnose/email were repeatedly re-addressed
        # after already completing (confirmed against the run's trace, not inferred). Root
        # cause: a router was told its OWN capability just finished, but never told which
        # OTHER capabilities had already produced a result anywhere else in the mesh -- so
        # naming recommend (which needs BOTH predict's and diagnose's output) required an
        # unstated two-hop inference no router's instructions actually walked it through.
        #
        # Fixed AGAIN 30-Aug-26 after the SECOND live run, which caught something the first
        # fix missed: it told routers only who had FINISHED (run.results), not who had
        # already been ADDRESSED and was still mid-flight. Confirmed against that run's own
        # timeline: predict fanned out to diagnose/email/simulate at once; email finished
        # fast (~8s) while diagnose/simulate were still running (~30-40s each); email's
        # router, seeing them absent from "finished", redundantly re-addressed both --
        # exactly the "most expensive mistake" mesh_peers.md warns against, caused by a race
        # this design hadn't accounted for, not by the model reasoning badly. Same mechanism
        # produced a duplicate `recommend` call: diagnose addressed it, then simulate's
        # concurrent router -- unaware -- addressed it again before it finished. Fixed by
        # splitting the signal in two: `addressed` (run.run_counts, stamped the INSTANT a
        # node begins, before its capability call even completes) tells a router what is
        # already dispatched or done and must not be re-addressed; `finished` (run.results,
        # only set once a capability's own output actually exists) is the narrower, slower
        # signal readiness reasoning needs (e.g. recommend's inputs aren't just "dispatched",
        # they must actually exist). Both are real, accurate accounts of shared state every
        # node already holds via _RunContext -- stating them doesn't reintroduce a
        # coordinator (nothing aggregates completeness, each node still decides alone), it
        # gives each routing decision the same honest status a real peer would have if it
        # could see the shared run log.
        addressed = sorted(k for k in self._run.run_counts if k in PEER_EDGES)
        finished = sorted(k for k in self._run.results if k in PEER_EDGES)
        addressed_line = ", ".join(addressed) if addressed else "none yet"
        finished_line = ", ".join(finished) if finished else "none yet"
        try:
            decision = await run_sub_agent(
                self._router, ROUTING_CALL_NAME,
                f"The request was:\n{self._run.query}\n\n"
                f"You produced:\n{serialize_agent_value(self._run.results[self._capability])}\n\n"
                f"Already addressed somewhere in this request, yours included -- some of "
                f"these may still be running; do NOT re-address any of them: "
                f"{addressed_line}.\n"
                f"Of those, already FINISHED with real output to work from: "
                f"{finished_line}. A peer not on this second list has no output yet, "
                f"even if it is on the first."
            )
            targets = tuple(t for t in (getattr(decision.value, "targets", None) or [])
                            if t in self._peers)
        except Exception as exc:
            # A routing failure must not take the whole run down: this branch stops, other
            # branches continue, and the gap shows up as an unrun capability -- which is
            # exactly Mesh's documented failure mode, recorded rather than papered over.
            print(f"  !! mesh routing failed for {self._capability}: {exc}", flush=True)
            return

        # Enforce the router's OWN stated rule in code rather than hoping it holds --
        # R63. _routing_schema's field description already tells the router "never name
        # one already addressed, even if it has not finished yet"; confirmed live (pilot
        # batch, mesh/Q1 and mesh/Q7) that nano names one anyway when two routers fire
        # moments apart off the same predict output, before either has learned anything
        # new. Dropping it here does not restrict WHICH peers a router may choose --
        # that scope judgment stays entirely the router's own, unconstrained by code, per
        # this module's own design contrast with Static-Graph DAG -- it only stops a
        # second, redundant dispatch of something already in flight with nothing new to
        # act on. A target already FINISHED is still allowed through unfiltered: that is
        # the legitimate "re-ask once new information exists" case MAX_RUNS_PER_CAPABILITY
        # exists for, and this filter does not touch it.
        finished_caps = set(self._run.results)
        addressed_caps = set(self._run.run_counts)
        in_flight = addressed_caps - finished_caps
        redundant = tuple(t for t in targets if t in in_flight)
        if redundant:
            print(f"  -- mesh: {self._capability} router named {list(redundant)} "
                  f"again while still in flight (addressed, not yet finished) -- "
                  f"dropped, not sent (R63)", flush=True)
        targets = tuple(t for t in targets if t not in in_flight)

        if targets:
            await ctx.send_message(
                PeerMessage(sender=self._capability, targets=targets,
                            note=f"{self._capability} completed"))


def _build_workflow(agents: dict[str, Agent], run: _RunContext):
    """Bidirectional peer graph. Every edge carries a condition testing whether its target
    was addressed, so a message reaches only the peers its sender named — routing is
    decided by the agent, while the framework does the scheduling and runs addressed peers
    concurrently."""
    nodes = {c: PeerNode(c, agents, run) for c in PEER_EDGES}
    builder = WorkflowBuilder(start_executor=nodes[ENTRY_CAPABILITY])
    for source, peers in PEER_EDGES.items():
        for target in peers:
            builder.add_edge(
                nodes[source], nodes[target],
                # default arg binds the target per iteration rather than by closure
                condition=lambda m, t=target: isinstance(m, PeerMessage) and t in m.targets,
            )
    return builder.build()


class MeshEntryPoint:
    """Exposes the surface execute_topology.py drives.

    Assembly happens in CODE after the graph goes idle, from what each node produced. That
    is deliberately not an aggregator node: R17 rejected a covering agent because one LLM
    seeing every capability's output rebuilds a hub. Serialising captured results is the
    same thing write_run() already does for every condition, and no agent gains a global
    view.

    Turn 2 does NOT re-enter the graph -- fixed 30-Aug-26 (third live run) after the
    entry_point.md fix alone proved insufficient. That fix stopped predict's OWN tool
    call on "Yes, proceed.", correctly and visibly (chat_response confirmed it worked) --
    but predict's ROUTER still ran as a separate agent call, still saw predict's capability
    listed as "already produced a result" (true in a narrow sense: it ran, just with
    nothing in it), and still addressed diagnose/email/simulate, which then addressed
    recommend -- the entire graph fired a second time regardless, on a router-reasoning
    gap no amount of prompt wording closes cleanly, because "ran" and "produced something
    worth building on" are genuinely different facts and the router only had the first.

    The fix follows the same shape sequential.py already uses for the identical harness
    problem, adapted to Mesh's shape: sequential.py holds `self._plan`/`self._query` across
    its two `run()` calls on one instance and turn 2 ignores the harness's "Yes, proceed."
    text entirely, using state from turn 1 instead. Mesh has no plan/confirm step to
    separate -- turn 1 already IS full execution -- so its equivalent is simpler: hold the
    turn-1 response, and let turn 2 return it again without invoking any agent at all. No
    router runs, no capability re-fires, no tokens spent -- correct by construction rather
    than by asking every router to reason its way to the same conclusion.
    """

    def __init__(self):
        self._entry_instructions = get_instruction("entry_point", topology=TOPOLOGY)
        self._done = False
        self._final_response: AgentResponse | None = None

    def create_session(self, *, session_id: str | None = None):
        return None      # no shared conversation: peers exchange messages, not history

    async def run(self, message: str, *, session=None, middleware=None) -> AgentResponse:
        # Turn 2 (or any call after the first): the graph already ran in full on turn 1,
        # and this message -- the harness's scripted "Yes, proceed." -- carries no new
        # information Mesh needs, the same reasoning sequential.py's own run() states for
        # ignoring it. Returning the stored response again is honest: nothing changed.
        if self._done:
            return self._final_response

        # Rebuilt per run so no budget or result leaks between runs.
        run = _RunContext(query=message, middleware=middleware)
        agents = _build_agents(self._entry_instructions)
        await _build_workflow(agents, run).run(message)

        print(f"  mesh: {run.hops_used}/{HOP_BUDGET} peer hops used; "
              f"ran {sorted(run.run_counts)}", flush=True)

        entry_result = run.results.get(ENTRY_CAPABILITY)
        response = entry_result if isinstance(entry_result, AgentResponse) else None
        if response is None:
            raise RuntimeError(
                "Mesh produced no result from its entry capability -- the graph did not "
                "run. Check that the start executor received the query.")
        # `.value` is a read-only @property on the installed 1.11.0 AgentResponse (backed
        # by `_value`/`_value_parsed`, confirmed by reading agent_framework/_types.py --
        # there is no setter, which is what crashed the first live run 30-Aug-26). Setting
        # the backing fields directly mirrors exactly what AgentResponse.__init__ does with
        # its own `value=` kwarg, and forcing `_value_parsed = True` stops the property's
        # lazy-parse branch from later re-parsing predict's raw message text against
        # predict's OWN response_format (not MasterOutput) if `.value` is ever read again.
        response._value = self._assemble(run.results)
        response._value_parsed = True
        self._final_response = response
        self._done = True
        return response

    @staticmethod
    def _assemble(results: dict) -> MasterOutput:
        """Build MasterOutput from whatever the nodes actually produced.

        Empty for a capability that never ran -- the honest record. Nothing here invents a
        narrative for a capability that produced nothing, which is the failure the
        aggregator fix of 29-Aug-26 addressed for Static-Graph DAG.
        """
        def narrative(prompt_key: str) -> str:
            res = results.get(prompt_key)
            if res is None:
                return ""
            raw = serialize_agent_value(res)
            try:
                return str(json.loads(raw).get("summary", "")).strip()
            except (json.JSONDecodeError, TypeError, AttributeError):
                return raw.strip()

        # Companion to the entry_point.md fix (30-Aug-26): predict now declines cleanly
        # in predict_summary when there was nothing actionable to run on, but its own
        # schema has no chat_response field and chat_response was previously hardcoded
        # empty here regardless -- so a correct decline was still invisible to the user.
        # Surfaced only when delayed_orders is empty: a real run with real predictions
        # speaks through its own data, unchanged from before.
        predict_value = getattr(results.get("predict"), "value", None)
        delayed_orders = getattr(predict_value, "delayed_orders", None) or []
        chat_response = "" if delayed_orders else (
            getattr(predict_value, "predict_summary", "") or "")

        return MasterOutput(
            chat_response=chat_response,
            simulate_summary=narrative("simulate_summary"),
            recommendation_summary=narrative("recommend_summary"),
            email_alert_summary=narrative("email_summary"),
        )


def build_master() -> MeshEntryPoint:
    """Matches every other topology's builder contract: an object with
    .run(message, session=, middleware=)."""
    return MeshEntryPoint()
