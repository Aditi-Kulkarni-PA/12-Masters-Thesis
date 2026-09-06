"""
Constrained Adaptive Swarm topology (T99) -- emergent agent architecture with runtime
dependency gating: an LLM plans the full set of specialists a request needs, each
specialist can request further specialists in its own turn, and all coordination
happens through a shared, Pydantic-typed blackboard. No fixed roster, no central
re-planner after the seed call, no dispatch resolver.

Split from plain Swarm 30-Aug-26 (T99), after two live runs of the shared free-text
design showed a specialist cannot be trusted to correctly re-state a multi-hop
dependency handoff ("diagnose must request recommend") inside its own write_instruction
-- confirmed directly from the per-agent instruction audit files, not inferred. The two
topologies share everything except WHEN a needed capability actually runs: plain Swarm
(topologies/swarm.py) leaves that to each specialist's own request_specialist call;
this module gates it in code instead (see _prerequisites_met below). Recorded as two
separate topologies rather than one design iterated in place, because plain Swarm's
free-text failures are themselves a finding for that condition, not a defect that was
quietly patched -- see plan/Thesis_Project_Tracker.xlsx, Topology Comparison rows 11-12.

Mechanism
------------------------------------------------------------------------------
1. A single planning call decomposes the raw query into the FULL set of capabilities
   the request needs -- not just what can start immediately -- as structured output
   (`WavePlan.needed_capabilities`), not free text needing a separate resolution step.
   Each entry (`AgentSpec`) names one of the 5 real, tool-backed capabilities
   (predict/diagnose/simulate/recommend/email -- a hard constraint: this codebase has
   exactly 5 implemented tools, emergence governs the ROSTER, not what tools exist),
   the task for that instance, and a write_instruction: what the planner wants that
   agent to post to the blackboard, and why. Agent STRUCTURE (which capabilities run,
   how many, in what combination) is emergent; each agent's OUTPUT SCHEMA is not --
   still one of the 5 fixed Pydantic response types.
2. WHEN each needed capability actually runs is decided by CODE, every wave, not by any
   specialist's own free-text reasoning. Revised 30-Aug-26 after two live runs showed a
   specialist cannot be trusted to correctly re-state a multi-hop handoff ("diagnose
   must request recommend") inside its own write_instruction -- confirmed both times
   directly from the per-agent instruction audit files (runs/swarm/<run_uid>/), not
   inferred. The wave loop checks measurement/dependencies.py's own TRUE_DEPENDENCIES
   table -- the SAME table already used to JUDGE every other topology's dependency
   order -- against what has actually posted to the blackboard, and starts whatever is
   ready. This is a FACT about what a tool needs to function (confirmed against
   tools/recommend_actions.py: recommend_actions() takes diagnosis_summary as a
   required argument), not an orchestration structure imposed on the agents -- the same
   distinction that already lets Mesh's measurement layer use this table to judge runs
   without Mesh's own agents ever being told about it. What remains emergent: WHICH
   capabilities the request needs (the seed call's own judgment), how many waves that
   takes, and any capability added mid-run via request_specialist (below) that the seed
   call did not foresee.
3. Each wave's ready capabilities run concurrently (asyncio.gather).
4. Writing to the blackboard is MANDATED for every specialist -- not code-automatic.
   Each specialist gets its own write_blackboard tool alongside its domain tool, and
   its instructions (composed from the planner's write_instruction for this spawn) say
   explicitly what to post and why. Mandating it in the prompt does not force the
   framework to guarantee the call happens -- compliance is measured, not assumed, the
   same lesson Mesh's own router-compliance findings already established this session.
5. A specialist that needs another capability's output (only `recommend`, which
   requires `diagnose`'s summary as an explicit argument) reads it via read_blackboard,
   a real tool call and a genuine measurable (read-usage correctness).
6. Any specialist may still emit a request_specialist call -- the SAME AgentSpec shape
   the planner uses, a direct structured choice, not free text -- but now only for a
   capability the seed call's own needed_capabilities did not already cover: a
   genuinely emergent addition, not the routine, already-known dependency chain. Merged
   into the needed set and subject to the same readiness check as everything else.
7. The run ends when nothing in the needed set is ready and nothing new was requested,
   or at MAX_WAVES (a code-level backstop only).

Blackboard is Pydantic end to end (BlackboardEntry, WavePlan, AgentSpec) -- entries are
appended, not keyed by a fixed 5-slot dict -- so the blackboard's own shape does not
bake in a predetermined roster either.
"""

import asyncio
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from agent_framework import Agent, AgentResponse, tool

from config import get_instruction
from core.clients import chat_client
from core.mcp_tools import pipeline_mcp
from core.schemas import (
    DeliveryDelayPredictionResult, DelayDiagnosisResult,
    SimulationsList, RecommendedActionsList, EmailsList, MasterOutput,
)
from core.tool_descriptions import CAPABILITY_DESCRIPTIONS, TOOL_NAME_BY_CAPABILITY
from measurement.dependencies import TRUE_DEPENDENCIES
from tools import recommend_actions, fetch_delayed_orders_for_email
from measurement.instrumentation import run_agent_as_tool_call, run_sub_agent

TOPOLOGY = "swarm_constrained_adaptive"

# Recorded name for the seed planning call, so analysis can separate the cost of
# deciding wave 1 from specialist work -- the same treatment Mesh gives its per-node
# routing calls (ROUTING_CALL_NAME) and Sequential gives its forced coordinator turns.
SEED_CALL_NAME = "swarm_constrained_adaptive_seed_planning"

Capability = Literal["predict", "diagnose", "simulate", "recommend", "email"]

# A specialist may be spawned more than once across waves (two different waves may
# legitimately both need, say, simulate under different framing) but not without
# bound -- same reasoning as Mesh's MAX_RUNS_PER_CAPABILITY: duplicate work is a real,
# measured cost of a topology with no fixed plan, not an error to hide.
MAX_RUNS_PER_CAPABILITY = 2

# Shared backstop across the whole run, independent of MAX_RUNS_PER_CAPABILITY -- bounds
# total WAVES, not total specialist runs. Not comparable to Mesh's message-hop budget;
# the two counters are different units.
MAX_WAVES = 5

# capability -> get_instruction() prompt key, response schema, domain tool(s). These
# prompt keys are core/agents.py's own hardcoded strings, copied here rather than
# imported -- core/agents.py exposes only fully-built module-level agent singletons,
# not the keys themselves. NOTE: these do NOT match core/tool_descriptions.py's
# RAW_TOOL_NAME_BY_CAPABILITY (that dict names raw MCP tool FUNCTIONS, a different
# namespace) -- only "predict" happens to coincide. Confirmed against core/agents.py
# directly, 30-Aug-26.
_PROMPT_KEY_BY_CAPABILITY: dict[str, str] = {
    "predict": "predict_delivery_delays",
    "diagnose": "diagnose_delay_patterns",
    "simulate": "delay_simulation",
    "recommend": "recommendation",
    "email": "email_alert",
}
_SCHEMA_BY_CAPABILITY = {
    "predict": DeliveryDelayPredictionResult,
    "diagnose": DelayDiagnosisResult,
    "simulate": SimulationsList,
    "recommend": RecommendedActionsList,
    "email": EmailsList,
}
_TOOLS_BY_CAPABILITY = {
    "predict": [pipeline_mcp],
    "diagnose": [pipeline_mcp],
    "simulate": [pipeline_mcp],
    "recommend": [recommend_actions],
    "email": [fetch_delayed_orders_for_email],
}
assert (set(_PROMPT_KEY_BY_CAPABILITY) == set(_SCHEMA_BY_CAPABILITY)
        == set(_TOOLS_BY_CAPABILITY) == set(TOOL_NAME_BY_CAPABILITY)), (
    "Swarm's local capability tables must cover exactly the five real capabilities -- "
    "a missing or extra entry would silently make one capability unreachable, or let "
    "the plan schema accept a capability that does not exist.")


# ---------------------------------------------------------------------------
# Structured planning / spawn-request shape -- used identically by the seed planner
# and by any specialist's own request_specialist call. Same shape both places on
# purpose: a specialist asking for the next agent is doing exactly what the planner
# did, just later and locally -- there is no separate "resolver" vocabulary to learn.
# ---------------------------------------------------------------------------

class AgentSpec(BaseModel):
    """One agent the plan calls for: which capability, its task, and what it must post
    to the blackboard. capability is Literal-constrained to the 5 real tool-backed
    capabilities -- structured output enforces this at the schema level; no free text,
    no separate resolution step."""
    capability: Capability
    task: str = Field(description="The task for this specialist, including any file "
                                   "paths or scenario wording from the user's message, verbatim.")
    write_instruction: str = Field(description="What this specialist must post to the "
                                                 "shared blackboard when it finishes, and why -- "
                                                 "carried into its own instructions verbatim.")


class WavePlan(BaseModel):
    """The seed planner's structured output: gate AND full-request decomposition in one
    call -- the same combination static_graph_routed's own router already establishes
    as this codebase's precedent for "refuse/inform/proceed AND selection together"
    (registry.py's own note on that condition), reused here rather than inventing a
    separate triage call Mesh's own entry point doesn't have either.

    Revised 30-Aug-26 (Aditi), after two live runs (30-Aug-26) showed a specialist
    cannot be trusted to correctly re-state a multi-hop handoff ("diagnose must request
    recommend") inside its own free-text write_instruction -- confirmed both times
    directly from the per-agent instruction audit files, not inferred. needed_capabilities
    now asks the planner for a much easier judgment -- EVERY capability the request
    needs, in any order, not who-hands-off-to-whom -- and the wave loop (ConstrainedAdaptiveSwarmEntryPoint)
    decides WHEN each one is actually ready by checking measurement/dependencies.py's own
    TRUE_DEPENDENCIES table against the blackboard, every wave. This removes the
    free-text propagation step entirely for the ROUTINE, already-known dependency
    structure. request_specialist remains on every specialist for genuinely emergent
    additions the seed call did not foresee -- that is still fully agent-decided.

    proceed/chat_response: same semantics as the shared TriageDecision schema every
    other triage-gated condition uses (core/schemas.py) -- not reused directly because
    TriageDecision has no room for needed_capabilities, but the two fields mean exactly
    what they mean there. needed_capabilities is only meaningful when proceed is True."""
    proceed: bool = Field(
        description="True only for an in-scope action request. False for a refusal "
                    "or an informational answer already written in chat_response.")
    chat_response: str = Field(
        default="", description="Refusal or informational answer. Required when "
                    "proceed is False, ignored when proceed is True.")
    needed_capabilities: list[AgentSpec] = Field(
        default_factory=list,
        description="EVERY specialist this request needs, in any order -- not just "
                    "which can start immediately. The system checks each one's real "
                    "prerequisites against what has actually been posted so far and "
                    "starts it the moment they are met; you do not need to reason about "
                    "sequencing or say who should trigger whom. Empty is valid even when "
                    "proceed is True, though unusual -- only if chat_response alone "
                    "already answers the request.")


# ---------------------------------------------------------------------------
# Blackboard -- Pydantic end to end, entries appended not keyed, so the blackboard's
# own shape does not assume a fixed 5-slot roster either.
# ---------------------------------------------------------------------------

class BlackboardEntry(BaseModel):
    """One specialist's posted result. `result` is that capability's own schema, dumped
    to a plain dict -- re-validate via _SCHEMA_BY_CAPABILITY[capability] if a typed
    object is needed back; stored as dict rather than a discriminated union because the
    five response schemas were not designed with a shared type tag."""
    capability: Capability
    agent_name: str
    wave: int
    note: str = Field(description="The specialist's own short note on what it posted "
                                    "and why -- its answer to the write_instruction it was given.")
    result: dict
    self_reported: bool = Field(
        default=True,
        description="True if the specialist itself called write_blackboard. False if "
                    "it did not, and the wave loop captured its already-produced "
                    "structured response on its behalf (R61) -- every other topology's "
                    "final answer is a Pydantic response_format, which is captured "
                    "unconditionally by the framework; this makes this topology's "
                    "capture the same guarantee, while still recording whether the "
                    "mandated self-report actually happened, for orchestration-"
                    "behaviour analysis.")


class BlackboardReadLogRow(BaseModel):
    """One read_blackboard call. hit distinguishes 'asked for something posted' from
    'asked for something not there yet' -- both real, expected outcomes under a
    no-fixed-order topology."""
    reader_capability: Capability
    capability_read: Capability
    hit: bool


class Blackboard(BaseModel):
    """Per-run shared state. Fresh per run, never persisted across runs -- same lifetime
    as Mesh's _RunContext."""
    entries: list[BlackboardEntry] = Field(default_factory=list)
    read_log: list[BlackboardReadLogRow] = Field(default_factory=list)
    # Where request_specialist calls land -- how later waves get decided. No central
    # re-planner reads this and picks; the wave loop just dedupes and builds whatever
    # is here after each wave. Pydantic like the rest of the blackboard, for the same
    # reason: this is also part of the run's shared, inspectable state.
    pending_requests: list[AgentSpec] = Field(default_factory=list)

    def write(self, *, capability: Capability, agent_name: str, wave: int,
              note: str, result: dict, self_reported: bool = True) -> None:
        self.entries.append(BlackboardEntry(
            capability=capability, agent_name=agent_name, wave=wave,
            note=note, result=result, self_reported=self_reported))

    def request(self, spec: AgentSpec) -> None:
        """Called from the request_specialist tool -- a specialist proposing what it
        thinks the NEXT wave needs. Appending, not deciding: the wave loop is the one
        that dedupes and turns this list into a real wave, after this wave finishes."""
        self.pending_requests.append(spec)

    def read(self, reader_capability: Capability, capability: Capability) -> dict | None:
        """Returns the MOST RECENT posted result for *capability*, or None on a miss --
        a specialist reading before its dependency has posted is a real, expected
        outcome here, not an error. Most recent rather than first because
        MAX_RUNS_PER_CAPABILITY allows a capability to run more than once across
        waves -- a later post supersedes an earlier one for anyone reading now."""
        matches = [e for e in self.entries if e.capability == capability]
        hit = bool(matches)
        self.read_log.append(BlackboardReadLogRow(
            reader_capability=reader_capability, capability_read=capability, hit=hit))
        return matches[-1].result if matches else None


# ---------------------------------------------------------------------------
# Per-run instruction-file audit trail. The controller decides which agent to build and
# what to name it, so that decision needs a readable trace on disk, the same way every
# other topology's prompts are readable .md files (load_config.py's WYSIWYG rule).
# No existing per-run file-artifact convention was found elsewhere in this codebase
# (checked helpers/logging_utils.py's log/ dir -- a generic timestamped execution log,
# unconnected to run identity or agent instructions); this directory is new.
# ---------------------------------------------------------------------------
_RUN_ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "runs" / "swarm_constrained_adaptive"


def _write_agent_instruction_file(run_uid: str, wave_num: int, agent_name: str,
                                   capability: Capability, instructions: str) -> None:
    """Persist the exact instruction text one constructed agent received. One file per
    agent instance -- a capability spawned in two different waves gets two files, since
    each is a separate construction decision, not an overwrite of the same one."""
    run_dir = _RUN_ARTIFACTS_DIR / run_uid
    safe_name = agent_name.replace(" ", "_").replace("/", "-")
    header = (f"# {agent_name}\n\n- capability: `{capability}`\n- wave: {wave_num}\n"
              f"- run: `{run_uid}`\n\n---\n\n")
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / f"wave{wave_num}_{safe_name}.md").write_text(
            header + instructions, encoding="utf-8")
    except OSError as e:
        # Audit trail is best-effort -- a disk/permission problem must not stop the
        # run; the agent still gets built and used with these instructions regardless.
        print(f"[swarm_constrained_adaptive] warning: could not write instruction file for {agent_name!r}: {e}")


def _make_read_blackboard_tool(blackboard: Blackboard, reader_capability: Capability):
    """This specialist's own read_blackboard tool, bound by closure to this run's
    blackboard and to which capability is reading -- needed so the read log records who
    asked, not just what. Every specialist gets it, symmetric access -- withholding it
    from capabilities unlikely to need it would be an assumption, not a fact."""
    @tool
    def read_blackboard(capability: Capability) -> str:
        """Read another specialist's already-posted result from this request's shared
        blackboard, by capability name: predict, diagnose, simulate, recommend, or
        email. Returns its result as text if already posted, or a plain message saying
        it has not posted yet."""
        result = blackboard.read(reader_capability, capability)
        # Printed, not just logged into blackboard.read_log, so a specialist's own
        # dependency-fetching behaviour is visible in the run's console/log output the
        # same way write-compliance already is -- read_log was populated before this
        # print existed but never surfaced anywhere a run's log could show it.
        print(f"  -- swarm_ca: {reader_capability} read_blackboard({capability}) -> "
              f"{'hit' if result is not None else 'miss (nothing posted yet)'}", flush=True)
        return f"No result posted yet for '{capability}'." if result is None else str(result)
    return read_blackboard


def _make_write_blackboard_tool(blackboard: Blackboard, capability: Capability,
                                 agent_name: str, wave_num: int):
    """This specialist's MANDATED write tool. Posting is not code-automatic -- the
    specialist must call this itself, with its own note on what it posted and why (its
    answer to the write_instruction it was given). Bound by closure to identify the
    writer without exposing that plumbing to the model.

    Mandating this in the prompt does not make the framework guarantee the call
    happens -- tool_choice="required" forces A tool call, not a specific ordered
    sequence of two. Whether every specialist actually complies is therefore a
    measurable (write-compliance), not a guarantee -- checked by the wave loop, not
    assumed here."""
    @tool
    def write_blackboard(note: str, result: dict) -> str:
        """Post your result to the shared blackboard so other specialists can read it.
        REQUIRED before you finish -- every specialist must call this exactly once.
        note: a short explanation of what you are posting and why, answering the write
        instruction you were given. result: your finding as a JSON-serializable object."""
        blackboard.write(capability=capability, agent_name=agent_name, wave=wave_num,
                          note=note, result=result)
        return "Posted to blackboard."
    return write_blackboard


def _make_request_specialist_tool(blackboard: Blackboard, capability: Capability):
    """This specialist's own request_specialist tool -- how the NEXT wave gets decided,
    locally, with no central re-planner recurring after the seed call (step 5 of the
    module docstring's mechanism). Optional, unlike write_blackboard: not every
    specialist will find it needs another capability's help finishing the request."""
    @tool
    def request_specialist(needed_capability: Capability, task: str,
                            write_instruction: str) -> str:
        """Ask for another specialist to run in the NEXT wave, if the request is not
        fully served yet. Same fields the plan itself is made of: needed_capability
        (predict, diagnose, simulate, recommend, or email), task (its specific task,
        including any file paths or scenario wording verbatim), write_instruction
        (what it must post to the blackboard, and why). Only call this if more work is
        genuinely needed -- do not call it just to confirm you are finished."""
        blackboard.request(AgentSpec(capability=needed_capability, task=task,
                                      write_instruction=write_instruction))
        return f"Requested {needed_capability} for the next wave."
    return request_specialist


def agent_name_for(capability: Capability, wave_num: int) -> str:
    """Naming convention for a wave-constructed specialist -- factored out once so
    plain Swarm and Constrained Adaptive Swarm, which share this construction shape,
    cannot silently drift apart on how an agent is named."""
    return f"Swarm {capability.title()} Specialist (wave {wave_num})"


def _build_specialist(spec: AgentSpec, blackboard: Blackboard, *,
                       run_uid: str, wave_num: int) -> Agent:
    """Construct one specialist agent, fresh, at the moment a wave decides it needs
    that capability. Deterministic, in-process, no LLM call of its own -- an earlier
    alternative (an LLM writes the agent's Python source instead of this function
    assembling it directly, swarm_codegen.py) was built, never wired into either
    Swarm variant, and deleted 30-Aug-26 as unused.

    Built entirely in this module rather than through core/agents.py's build_X_agent()
    factories -- correction from Aditi, 30-Aug-26: those factories are shared, static
    wiring every other topology also depends on unmodified; adding Swarm's own
    read_blackboard/write_blackboard tools there would leak topology-specific machinery
    into a file nothing else needs it in (R19/R22 -- the same class of mistake already
    corrected once this session for mesh_peers.md/mesh_handoff.md). The controller
    decides which agent is needed and builds it itself, at the moment a wave needs it --
    not by reaching into a shared factory and bolting a parameter on.

    What stays shared, because none of it is Swarm-specific: the domain prompt text
    (get_instruction(), the same file every topology reads for that capability), the
    response schemas, and the underlying domain tools. Only the three blackboard tools
    (read, write, request), the write mandate, and the per-agent instruction-file audit
    trail are added here.

    middleware stays None on the Agent itself, matching Mesh: this specialist is
    invoked via run_agent_as_tool_call() (in the wave loop), which records ONE
    canonical tool_call row per invocation under TOOL_NAME_BY_CAPABILITY -- the same
    mechanism Mesh and Static-Graph DAG already use for code-scheduled agents, proven
    correct in this codebase rather than a new pattern invented for Swarm.
    """
    capability = spec.capability
    agent_name = agent_name_for(capability, wave_num)

    domain_instructions = get_instruction(_PROMPT_KEY_BY_CAPABILITY[capability])
    # Read mandate for any TRUE_DEPENDENCIES prerequisite -- R62. The wave loop only
    # starts a capability once its prerequisites have posted (_prerequisites_met), but
    # "posted" is not "in this specialist's own context": spec.task was written by the
    # seed planner in turn 1, before any prerequisite ran, so it cannot carry that
    # prerequisite's actual content. Without this, tool_choice="required" (below) is
    # satisfied by calling the domain tool first, with the prerequisite argument left
    # empty (confirmed live, R62: recommend called recommendation_tool with an empty
    # diagnosis_summary, which recommend_actions() itself immediately rejected via its
    # own _MIN_DIAGNOSIS_CHARS guard -- fast, thin, valid JSON, no error surfaced).
    prereq_caps = sorted(_true_prereq_capabilities(capability))
    read_mandate = (
        f"Before calling your domain tool, call read_blackboard for each of: "
        f"{', '.join(prereq_caps)}. Pass what it returns as that argument -- do not "
        f"call your domain tool with that argument empty or invented.\n\n"
        if prereq_caps else ""
    )
    # The write instruction rides on the system prompt, not just the tool's own
    # docstring -- belt-and-braces, the same reasoning dispatch_router_spec.md gave for
    # Planner-Executor's structural pass-through: schema-level alone is weaker than
    # schema + prompt-level reinforcement together.
    instructions = (
        f"{domain_instructions}\n\n---\n\n"
        f"## Blackboard\n"
        f"{read_mandate}"
        f"Before you finish, you MUST call write_blackboard to post your result. "
        f"What to post: {spec.write_instruction}\n\n"
        f"You do not need to request any other specialist for this request's known "
        f"needs -- the system already tracks every capability the request calls for "
        f"and starts each one once its own inputs exist. Only call request_specialist "
        f"if, from your own work, you discover the request needs something genuinely "
        f"unforeseen -- not already part of the plan."
    )
    _write_agent_instruction_file(run_uid, wave_num, agent_name, capability, instructions)

    tools = list(_TOOLS_BY_CAPABILITY[capability]) + [
        _make_read_blackboard_tool(blackboard, capability),
        _make_write_blackboard_tool(blackboard, capability, agent_name, wave_num),
        _make_request_specialist_tool(blackboard, capability),
    ]
    return Agent(
        name=agent_name,
        description=CAPABILITY_DESCRIPTIONS[capability],
        client=chat_client,
        instructions=instructions,
        tools=tools,
        default_options={"temperature": 0, "tool_choice": "required",
                          "response_format": _SCHEMA_BY_CAPABILITY[capability]},
    )


# ---------------------------------------------------------------------------
# Seed planner -- gates the request and decomposes it into wave 1, in one call.
# ---------------------------------------------------------------------------

def _build_seed_planner() -> Agent:
    """The one call that both gates the request (refuse/inform/proceed, the same
    semantics every triage-gated condition's TriageDecision carries) and decomposes it
    into wave 1 -- combined into one call rather than two, following
    static_graph_routed's own precedent for exactly this combination (see WavePlan's
    own docstring for why). tools=[], response_format=WavePlan -- it only plans, it
    never touches a domain tool or the blackboard itself."""
    return Agent(
        name="Swarm Seed Planner",
        client=chat_client,
        instructions=get_instruction("master", topology=TOPOLOGY),
        tools=[],
        default_options={"temperature": 0, "response_format": WavePlan},
    )


def _true_prereq_capabilities(capability: Capability) -> set[str]:
    """The capability-name form of *capability*'s real prerequisites, per
    measurement/dependencies.py's own TRUE_DEPENDENCIES table (keyed by wrapped tool
    names) mapped back through TOOL_NAME_BY_CAPABILITY. Factored out of
    _prerequisites_met so the scheduling gate and the specialist's own instructions
    (below) read the same fact from one place -- a capability whose dependency this
    table does not know about cannot be told to check for it, and vice versa."""
    tool_name = TOOL_NAME_BY_CAPABILITY[capability]
    prereq_tools = set(TRUE_DEPENDENCIES.get(tool_name, ()))
    return {cap for cap, name in TOOL_NAME_BY_CAPABILITY.items() if name in prereq_tools}


def _prerequisites_met(capability: Capability, posted_caps: set[str]) -> bool:
    """True once every real prerequisite *capability* needs has actually posted to the
    blackboard. This is a FACT about what a tool needs to function (confirmed against
    tools/recommend_actions.py for recommend's own case), not an orchestration
    structure being imposed on the agents -- the same table Mesh's measurement layer
    already uses to JUDGE dependency order on every other topology's runs, reused here
    to GATE Swarm's own scheduling after two live runs (30-Aug-26) showed free-text
    propagation of this exact fact was not reliable. predict, with no prerequisites,
    is always ready."""
    return _true_prereq_capabilities(capability) <= posted_caps


# ---------------------------------------------------------------------------
# Wave orchestration -- the entry point execute_topology.py drives.
# ---------------------------------------------------------------------------

class ConstrainedAdaptiveSwarmEntryPoint:
    """Exposes the surface execute_topology.py drives: an object with
    .create_session() and .run(message, session=, middleware=) -- the same contract
    every other topology's build_master() returns (see Mesh's MeshEntryPoint).

    Turn 2 does NOT re-plan or re-run anything -- same fix Mesh and Sequential already
    established for the identical two-turn harness problem (execute_topology.py always
    sends a scripted "Yes, proceed." second turn). Swarm has no plan/confirm split to
    separate either: the seed call already gates AND starts execution on turn 1, so
    turn 2's only correct behaviour is to return the stored response again.
    """

    def __init__(self):
        self._done = False
        self._final_response: AgentResponse | None = None

    def create_session(self, *, session_id: str | None = None):
        return None  # no shared conversation -- waves coordinate via the blackboard, not chat history

    async def run(self, message: str, *, session=None, middleware=None) -> AgentResponse:
        if self._done:
            return self._final_response

        run_uid = str(uuid.uuid4())
        blackboard = Blackboard()
        run_counts: dict[str, int] = {}

        seed_response = await run_sub_agent(_build_seed_planner(), SEED_CALL_NAME, message)
        plan: WavePlan | None = seed_response.value

        if plan is None or not plan.proceed:
            chat_response = plan.chat_response if plan else (
                "Unable to plan this request -- the seed planner returned no usable output.")
            seed_response._value = MasterOutput(chat_response=chat_response)
            seed_response._value_parsed = True
            self._final_response = seed_response
            self._done = True
            return seed_response

        # The full requirement set, decided once by the seed call. WHEN each one
        # actually runs is now a code decision (see module docstring, step 2) -- keyed
        # by capability so a request_specialist call for something already in here
        # (an emergent RE-ask, not a new addition) can update its spec rather than
        # duplicate it.
        still_needed: dict[str, AgentSpec] = {}
        for spec in plan.needed_capabilities:
            if spec.capability in still_needed:
                print(f"  -- swarm_ca: seed plan named {spec.capability} more than once, "
                      f"keeping the first", flush=True)
                continue
            still_needed[spec.capability] = spec

        wave_num = 0
        while wave_num < MAX_WAVES:
            # Fold in anything requested emergently since the last wave -- genuinely
            # unforeseen additions the seed call's own needed_capabilities did not
            # cover, or a legitimate re-ask of something already run (subject to
            # MAX_RUNS_PER_CAPABILITY below either way).
            for spec in blackboard.pending_requests:
                if spec.capability not in still_needed:
                    print(f"  -- swarm_ca: {spec.capability} added mid-run via "
                          f"request_specialist (not in the original plan)", flush=True)
                still_needed[spec.capability] = spec
            blackboard.pending_requests = []

            posted_caps = {e.capability for e in blackboard.entries}
            ready: dict[str, AgentSpec] = {}
            for cap, spec in still_needed.items():
                if run_counts.get(cap, 0) >= MAX_RUNS_PER_CAPABILITY:
                    continue
                if _prerequisites_met(cap, posted_caps):
                    ready[cap] = spec
            if not ready:
                # Either genuinely done (nothing left needs anything further) or
                # everything remaining is stuck on a prerequisite that will never post
                # (e.g. that prerequisite itself failed) -- both end the run the same
                # way; the gap is visible in "capabilities that posted a result" below
                # either way, not silently swallowed.
                break
            wave_num += 1
            for cap in ready:
                del still_needed[cap]

            agents = {cap: _build_specialist(spec, blackboard, run_uid=run_uid, wave_num=wave_num)
                      for cap, spec in ready.items()}
            for cap in ready:
                run_counts[cap] = run_counts.get(cap, 0) + 1
            print(f"  -- swarm_ca: wave {wave_num} running {sorted(ready)} concurrently "
                  f"(prerequisites met); still waiting on "
                  f"{sorted(still_needed) or 'nothing'}", flush=True)

            # Genuine concurrency, not the model choosing to call two tools in one
            # turn: asyncio.gather actually awaits every specialist's own LLM call in
            # parallel. Uses run_agent_as_tool_call -- Mesh's and Static-Graph DAG's own
            # proven mechanism for recording a code-scheduled agent's invocation as a
            # canonical tool_call row -- rather than MAF's ConcurrentBuilder, whose
            # result-to-capability correlation and construction-time middleware
            # attachment were not verified against a live run in time for this build.
            # Flagged as an open item, not silently substituted.
            before = len(blackboard.entries)
            results = await asyncio.gather(*[
                run_agent_as_tool_call(agents[cap], TOOL_NAME_BY_CAPABILITY[cap],
                                        spec.task, middleware)
                for cap, spec in ready.items()
            ], return_exceptions=True)
            for cap, result in zip(ready, results):
                if isinstance(result, Exception):
                    # One specialist failing must not take the whole run down -- the
                    # gap shows up as a capability that never posted, the same
                    # philosophy Mesh's own routing-failure handling uses.
                    print(f"  !! swarm_ca: {cap} failed in wave {wave_num}: {result}", flush=True)

            # Write-compliance check -- mandating write_blackboard in the prompt does
            # not guarantee the framework enforces the call (confirmed R61: MAF resets
            # tool_choice="required" to "auto" after one iteration, agent_framework
            # _tools.py's own function-invocation loop, so only the specialist's FIRST
            # tool call is ever forced). Still measured, not silently assumed -- but no
            # longer left to cost a run its data either: every other topology's final
            # answer is captured unconditionally via response_format, so a specialist
            # that skipped write_blackboard gets its already-produced structured result
            # force-captured here, the same guarantee, with self_reported=False so the
            # miss stays visible for analysis instead of disappearing into a silent fix.
            posted = {e.capability for e in blackboard.entries[before:]}
            for cap in ready:
                if cap in posted:
                    continue
                print(f"  !! swarm_ca: {cap} did not post to the blackboard in wave "
                      f"{wave_num} despite the write mandate -- write-compliance "
                      f"miss (R61: forcing capture of its own structured result)",
                      flush=True)
                result = dict(zip(ready, results)).get(cap)
                value = getattr(result, "value", None)
                forced_result = value.model_dump() if isinstance(value, BaseModel) else {}
                blackboard.write(
                    capability=cap, agent_name=agents[cap].name, wave=wave_num,
                    note="Auto-captured by the wave loop -- the specialist completed "
                         "its work but did not call write_blackboard itself.",
                    result=forced_result, self_reported=False)

        if still_needed:
            print(f"  !! swarm_ca: run ended with {sorted(still_needed)} still needed but "
                  f"never ready -- a prerequisite likely never posted", flush=True)
        print(f"  swarm_ca: {wave_num} wave(s) run; capabilities that posted a result: "
              f"{sorted(set(e.capability for e in blackboard.entries))}", flush=True)

        seed_response._value = self._assemble(blackboard)
        seed_response._value_parsed = True
        self._final_response = seed_response
        self._done = True
        return seed_response

    @staticmethod
    def _assemble(blackboard: Blackboard) -> MasterOutput:
        """Build MasterOutput from whatever the blackboard actually holds. Code-level,
        no covering aggregator agent -- the same R17-respecting precedent Mesh's own
        _assemble() follows: nothing here gains a view of every capability's output.

        Each entry's own `note` -- not `result` -- supplies the narrative fields:
        write_blackboard's note argument is already "a short explanation of what you
        are posting and why", the same shape these fields need, and it is guaranteed to
        be a plain string, unlike `result`, which is free-form. The most recent entry
        for a capability wins, matching Blackboard.read()'s own most-recent
        convention -- a capability run twice leaves its LATER post authoritative.
        """
        def latest_note(capability: str) -> str:
            matches = [e for e in blackboard.entries if e.capability == capability]
            return matches[-1].note if matches else ""

        return MasterOutput(
            chat_response="",
            simulate_summary=latest_note("simulate"),
            recommendation_summary=latest_note("recommend"),
            email_alert_summary=latest_note("email"),
        )


def build_master() -> ConstrainedAdaptiveSwarmEntryPoint:
    """Matches every other topology's builder contract: an object with
    .run(message, session=, middleware=)."""
    return ConstrainedAdaptiveSwarmEntryPoint()
