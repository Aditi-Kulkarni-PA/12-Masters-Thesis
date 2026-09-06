"""
Swarm topology (T99) -- pure emergent agent architecture: an LLM plans the specialists
that can start immediately, each specialist decides for itself, in its own turn,
whether the request still needs another capability and requests it directly, and all
coordination happens through a shared, Pydantic-typed blackboard. No fixed roster, no
central re-planner after the seed call, no dispatch resolver, and no code-level
dependency check anywhere -- timing is entirely agent-decided.

Rebuilt 30-Aug-26 (T88). The prior design (dispatch_specialist(specialist_needed,
request), one long-lived dispatcher agent calling one generic tool repeatedly in a
single session) is, by this project's own topology reference, closer to Handoffs than
Swarm. An intermediate rebuild attempt (free-text capability description + a
deterministic keyword resolver) was corrected by Aditi, 30-Aug-26: a resolver that maps
free text onto a fixed, pre-known set of dispatch targets is functionally a routing
table -- that is what distinguishes Mesh's peer-addressing, not Swarm.

Split from Constrained Adaptive Swarm 30-Aug-26 (T99): the two topologies share the
same specialist-construction shape, blackboard, and tools, and differ only in WHEN a
needed capability runs. This module leaves that entirely to each specialist's own
`request_specialist` call. topologies/swarm_constrained_adaptive.py instead gates
timing in code against measurement/dependencies.py's TRUE_DEPENDENCIES table. Recorded
as two topologies, not one design iterated in place, because this module's own
free-text-propagation failures are themselves the finding for this condition.

Correction 30-Aug-26 (same day): specialists initially had NO dependency-graph
information at all -- domain agent prompts (agents/*.md) carry no @includes, so a
specialist knew only its own role, not what any other capability consumes. This let
`predict` request `recommend` directly, unaware `recommend` also needs `diagnose`'s
output -- a logged, genuine dependency violation, not a guessing failure. Fixed by
giving each specialist the SAME cross-capability facts (@participant_capabilities +
@dependency_discovery) the seed planner already had, plus an instruction to check
read_blackboard for a downstream capability's OTHER inputs before requesting it. This
equalizes INFORMATION with Constrained Adaptive Swarm without changing WHO decides
timing -- the agent still judges when to call request_specialist and can still get it
wrong; the fix removes an unfair informational handicap, not the agent's decision
role, keeping the two conditions comparable on the axis that actually varies.

Mechanism
------------------------------------------------------------------------------
1. A single planning call decides which capabilities can start RIGHT NOW, given
   nothing has posted to the blackboard yet, as structured output
   (`WavePlan.needed_capabilities`). Each entry (`AgentSpec`) names one of the 5 real,
   tool-backed capabilities (predict/diagnose/simulate/recommend/email -- a hard
   constraint: this codebase has exactly 5 implemented tools, emergence governs the
   ROSTER, not what tools exist), the task for that instance, and a write_instruction:
   what the planner wants that agent to post to the blackboard, and why.
2. Wave 1 runs exactly what the seed named -- no code-level readiness check. A
   specialist whose own output another capability depends on is responsible for
   requesting that capability itself, via request_specialist, once its own work is
   done. Nothing else triggers a downstream capability; if a specialist omits the
   request, that capability simply never runs. OBSERVED (n=2 live runs, same query,
   same handoff, confirmed from the per-agent instruction audit files at
   runs/swarm/<run_uid>/): diagnose never requested recommend in either run, and
   recommend never ran. A separate n=2 (two earlier runs, same query) show a different
   failure mode under the same free-text mechanism: a specialist requesting a
   downstream capability before that capability's OWN prerequisite had posted, causing
   the requested capability to start prematurely. Both are treated as findings for this
   condition, not defects awaiting a further prompt fix -- see
   plan/Thesis_Project_Tracker.xlsx, Topology Comparison rows 11-12.
3. Each wave's specialists run concurrently (asyncio.gather).
4. Writing to the blackboard is MANDATED for every specialist -- not code-automatic.
   Each specialist gets its own write_blackboard tool alongside its domain tool, and
   its instructions (composed from the planner's write_instruction for this spawn) say
   explicitly what to post and why. Mandating it in the prompt does not force the
   framework to guarantee the call happens -- compliance is measured, not assumed.
5. A specialist that needs another capability's output (only `recommend`, which
   requires `diagnose`'s summary as an explicit argument) reads it via read_blackboard,
   a real tool call and a genuine measurable (read-usage correctness).
6. Any specialist may emit a request_specialist call -- the SAME AgentSpec shape the
   planner uses, a direct structured choice, not free text. Whatever accumulates in
   the blackboard's pending_requests since the previous wave becomes the NEXT wave,
   run unconditionally -- no check that the requested capability's own prerequisites
   have actually posted yet.
7. The run ends when a wave produces no new requests, or at MAX_WAVES (a code-level
   backstop only).

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
from tools import recommend_actions, fetch_delayed_orders_for_email
from measurement.dependencies import TRUE_DEPENDENCIES
from measurement.instrumentation import run_agent_as_tool_call, run_sub_agent

TOPOLOGY = "swarm"

# Recorded name for the seed planning call, so analysis can separate the cost of
# deciding wave 1 from specialist work -- the same treatment Mesh gives its per-node
# routing calls (ROUTING_CALL_NAME) and Sequential gives its forced coordinator turns.
SEED_CALL_NAME = "swarm_seed_planning"

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
    """The seed planner's structured output: gate AND wave-1 decomposition in one
    call -- the same combination static_graph_routed's own router already establishes
    as this codebase's precedent for "refuse/inform/proceed AND selection together"
    (registry.py's own note on that condition), reused here rather than inventing a
    separate triage call Mesh's own entry point doesn't have either.

    needed_capabilities is WAVE 1 ONLY -- what can start given nothing has posted to
    the blackboard yet. Anything else is requested later, by whichever specialist's
    output it depends on, via that specialist's own request_specialist call. This
    planner never reasons about the full dependency chain in one shot; each hop is
    decided locally, by whichever agent is running when that hop becomes relevant.

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
        description="ONLY the specialists that can start immediately, given nothing "
                    "has posted to the blackboard yet -- not the full set the request "
                    "will eventually need. Do not include a capability that depends on "
                    "another capability's output; that capability's own specialist will "
                    "request it later, once its output exists. Empty is valid even when "
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
                    "unconditionally by the framework; this makes Swarm's capture the "
                    "same guarantee, while still recording whether the mandated "
                    "self-report actually happened, for orchestration-behaviour analysis.")


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
_RUN_ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "runs" / "swarm"


def _write_agent_instruction_file(run_uid: str, wave_num: int, agent_name: str,
                                   capability: Capability, instructions: str,
                                   task: str = "") -> None:
    """Persist the exact instruction text AND task one constructed agent received. One
    file per agent instance -- a capability spawned in two different waves gets two
    files, since each is a separate construction decision, not an overwrite of the
    same one.

    task added 30-Aug-26: the audit file previously captured only the system prompt
    (instructions), never the per-instance task/user-message text -- so a scenario-wording
    loss between a requesting specialist and the one it requests (e.g. predict
    requesting simulate) was not diagnosable from disk, only from live console output.
    task is the same string passed to run_agent_as_tool_call() as that specialist's
    user turn."""
    run_dir = _RUN_ARTIFACTS_DIR / run_uid
    safe_name = agent_name.replace(" ", "_").replace("/", "-")
    header = (f"# {agent_name}\n\n- capability: `{capability}`\n- wave: {wave_num}\n"
              f"- run: `{run_uid}`\n\n## Task (user turn)\n\n{task}\n\n---\n\n"
              f"## Instructions (system prompt)\n\n")
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / f"wave{wave_num}_{safe_name}.md").write_text(
            header + instructions, encoding="utf-8")
    except OSError as e:
        # Audit trail is best-effort -- a disk/permission problem must not stop the
        # run; the agent still gets built and used with these instructions regardless.
        print(f"[swarm] warning: could not write instruction file for {agent_name!r}: {e}")


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
        print(f"  -- swarm: {reader_capability} read_blackboard({capability}) -> "
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


def _true_prereq_capabilities(capability: Capability) -> set[str]:
    """The capability-name form of *capability*'s real prerequisites, per
    measurement/dependencies.py's own TRUE_DEPENDENCIES table (keyed by wrapped tool
    names) mapped back through TOOL_NAME_BY_CAPABILITY. Same helper as Constrained
    Adaptive Swarm's own (swarm_constrained_adaptive.py) -- kept as a duplicate
    function rather than a shared import because the two modules' capability sets and
    TOOL_NAME_BY_CAPABILITY mapping are otherwise independent of each other; this is
    the one fact both need from measurement/dependencies.py."""
    tool_name = TOOL_NAME_BY_CAPABILITY[capability]
    prereq_tools = set(TRUE_DEPENDENCIES.get(tool_name, ()))
    return {cap for cap, name in TOOL_NAME_BY_CAPABILITY.items() if name in prereq_tools}


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
    # The write instruction rides on the system prompt, not just the tool's own
    # docstring -- belt-and-braces, the same reasoning dispatch_router_spec.md gave for
    # Planner-Executor's structural pass-through: schema-level alone is weaker than
    # schema + prompt-level reinforcement together.
    # Full cross-capability facts (all 5 consumes/produces, plus the reasoning
    # instruction for deriving order from them) -- the SAME material the seed
    # planner gets via master.md's @participant_capabilities and
    # @dependency_discovery. Without this, a specialist only knows its own role and
    # cannot see a downstream capability's OTHER prerequisites: predict knows
    # recommend depends on predict, but has no way to know recommend ALSO depends
    # on diagnose unless it can read that fact here. Confirmed missing before this
    # fix -- domain agent prompts (agents/*.md) carry no @includes at all, so a
    # specialist previously had zero dependency-graph information of any kind.
    dependency_facts = (
        get_instruction("participant_capabilities") + "\n\n" +
        get_instruction("dependency_discovery")
    )
    # Fix 30-Aug-26: request_specialist lets a specialist author ANOTHER specialist's
    # task string, the same act master.md's seed call performs for wave 1 -- so it
    # needs the same @input_handling rule the seed call already gets (via R23), not a
    # new one. Confirmed missing before this fix: _build_specialist() included
    # dependency_facts but never input_handling, so a specialist requesting a
    # downstream capability had no instruction to carry the user's scenario wording
    # (region, weather, file path, etc.) into that capability's task string. Observed
    # consequence: predict requesting simulate posted a task too bare for simulate to
    # build filters/changes from, and simulate correctly returned an empty result per
    # its own error-handling instructions -- a genuinely underspecified task, not a
    # simulate-side failure.
    input_handling = get_instruction("input_handling")
    # Read mandate for any TRUE_DEPENDENCIES prerequisite -- R62. spec.task was
    # authored by whichever specialist called request_specialist for this one (or by
    # the seed call, for wave 1), at a point before this specialist's prerequisites
    # necessarily had real content yet -- it cannot carry that content. Without this,
    # tool_choice="required" (below) is satisfied by calling the domain tool first,
    # with the prerequisite argument left empty (confirmed live, R62: recommend called
    # recommendation_tool with an empty diagnosis_summary, which recommend_actions()
    # itself immediately rejected via its own _MIN_DIAGNOSIS_CHARS guard -- fast, thin,
    # valid JSON, no error surfaced). dependency_facts above tells this specialist WHAT
    # depends on what; this tells it what to DO about its own prerequisites.
    prereq_caps = sorted(_true_prereq_capabilities(capability))
    read_mandate = (
        f"Before calling your domain tool, call read_blackboard for each of: "
        f"{', '.join(prereq_caps)}. Pass what it returns as that argument -- do not "
        f"call your domain tool with that argument empty or invented.\n\n"
        if prereq_caps else ""
    )
    instructions = (
        f"{domain_instructions}\n\n---\n\n"
        f"{dependency_facts}\n\n---\n\n"
        f"{input_handling}\n\n---\n\n"
        f"## Blackboard\n"
        f"{read_mandate}"
        f"Before you finish, you MUST call write_blackboard to post your result. "
        f"What to post: {spec.write_instruction}\n\n"
        f"Nothing else in this system checks what the request still needs -- that is "
        f"your job, right now. There may be MORE THAN ONE capability whose own work "
        f"depends on what you just posted -- go through the full capability list "
        f"above and check each one, not just the first that comes to mind. For each "
        f"one, also check whether it needs anything ELSE besides you, using the same "
        f"capability list. If it does, call read_blackboard for that OTHER input "
        f"first: if read_blackboard shows it has already been posted, this capability "
        f"is ready, request it now; if read_blackboard shows nothing posted yet, do "
        f"NOT request it -- leave it for whichever specialist produces that missing "
        f"input to request once its own work is done. Call request_specialist ONCE "
        f"FOR EACH capability you confirm is ready this way, all before you finish; "
        f"any capability you do not request will not run. Check this even for a "
        f"capability you do not use yourself: your output can be a required input "
        f"for a capability several steps downstream of you, not just the very next "
        f"one. Before requesting a capability, check read_blackboard for it; if it "
        f"has already posted, do not request it again unless the task explicitly "
        f"changed."
    )
    _write_agent_instruction_file(run_uid, wave_num, agent_name, capability, instructions,
                                   task=spec.task)

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


# ---------------------------------------------------------------------------
# Wave orchestration -- the entry point execute_topology.py drives. No dependency
# table is consulted anywhere below: every wave after the first is exactly whatever
# specialists requested via request_specialist in the previous wave, run
# unconditionally. This is the deliberate difference from
# topologies/swarm_constrained_adaptive.py -- see this module's own docstring.
# ---------------------------------------------------------------------------

class SwarmEntryPoint:
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

        # Wave 1 is exactly what the seed named -- no code-level readiness check
        # anywhere in this topology (see module docstring). Keyed by capability so a
        # duplicate name in the plan collapses to one spec rather than running twice.
        wave1: dict[str, AgentSpec] = {}
        for spec in plan.needed_capabilities:
            if spec.capability in wave1:
                print(f"  -- swarm: seed plan named {spec.capability} more than once, "
                      f"keeping the first", flush=True)
                continue
            wave1[spec.capability] = spec

        wave_num = 0
        current_wave = wave1
        while wave_num < MAX_WAVES and current_wave:
            wave_num += 1
            run_this_wave: dict[str, AgentSpec] = {}
            for cap, spec in current_wave.items():
                if run_counts.get(cap, 0) >= MAX_RUNS_PER_CAPABILITY:
                    print(f"  -- swarm: {cap} requested again but already hit "
                          f"MAX_RUNS_PER_CAPABILITY ({MAX_RUNS_PER_CAPABILITY}), "
                          f"skipping", flush=True)
                    continue
                run_this_wave[cap] = spec
            if not run_this_wave:
                break

            agents = {cap: _build_specialist(spec, blackboard, run_uid=run_uid, wave_num=wave_num)
                      for cap, spec in run_this_wave.items()}
            for cap in run_this_wave:
                run_counts[cap] = run_counts.get(cap, 0) + 1
            print(f"  -- swarm: wave {wave_num} running {sorted(run_this_wave)} "
                  f"concurrently (agent-requested; no readiness check)", flush=True)

            # Genuine concurrency, not the model choosing to call two tools in one
            # turn: asyncio.gather actually awaits every specialist's own LLM call in
            # parallel. Uses run_agent_as_tool_call -- Mesh's and Static-Graph DAG's own
            # proven mechanism for recording a code-scheduled agent's invocation as a
            # canonical tool_call row -- rather than MAF's ConcurrentBuilder, whose
            # result-to-capability correlation and construction-time middleware
            # attachment were not verified against a live run in time for this build.
            before = len(blackboard.entries)
            results = await asyncio.gather(*[
                run_agent_as_tool_call(agents[cap], TOOL_NAME_BY_CAPABILITY[cap],
                                        spec.task, middleware)
                for cap, spec in run_this_wave.items()
            ], return_exceptions=True)
            for cap, result in zip(run_this_wave, results):
                if isinstance(result, Exception):
                    # One specialist failing must not take the whole run down -- the
                    # gap shows up as a capability that never posted, the same
                    # philosophy Mesh's own routing-failure handling uses.
                    print(f"  !! swarm: {cap} failed in wave {wave_num}: {result}", flush=True)

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
            for cap, spec in run_this_wave.items():
                if cap in posted:
                    continue
                print(f"  !! swarm: {cap} did not post to the blackboard in wave "
                      f"{wave_num} despite the write mandate -- write-compliance "
                      f"miss (R61: forcing capture of its own structured result)",
                      flush=True)
                result = dict(zip(run_this_wave, results)).get(cap)
                value = getattr(result, "value", None)
                forced_result = value.model_dump() if isinstance(value, BaseModel) else {}
                blackboard.write(
                    capability=cap, agent_name=agents[cap].name, wave=wave_num,
                    note="Auto-captured by the wave loop -- the specialist completed "
                         "its work but did not call write_blackboard itself.",
                    result=forced_result, self_reported=False)

            # The NEXT wave is exactly what got requested during this one, run
            # unconditionally -- no check that a requested capability's own
            # prerequisites have actually posted yet. That check does not exist
            # anywhere in this topology; the requesting specialist's own judgment is
            # the only gate. Deduping a repeated request the same way wave 1 dedupes
            # the seed's plan.
            current_wave = {}
            for spec in blackboard.pending_requests:
                current_wave[spec.capability] = spec
            blackboard.pending_requests = []

        print(f"  swarm: {wave_num} wave(s) run; capabilities that posted a result: "
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


def build_master() -> SwarmEntryPoint:
    """Matches every other topology's builder contract: an object with
    .run(message, session=, middleware=)."""
    return SwarmEntryPoint()
