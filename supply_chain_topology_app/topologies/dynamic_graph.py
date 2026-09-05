"""
Dynamic-Graph topology (T87, optional) — MagenticBuilder's ledger-driven orchestration.

`MagenticBuilder(participants=..., manager_agent=...).build()` returns a `Workflow` (the
same type `WorkflowBuilder` produces for Static-Graph DAG). The manager_agent drives a
Task Ledger / Progress Ledger loop that decides which participant acts next from what
has actually been produced so far — no edges, no fixed order, no fixed scope. That is
the property this condition exists to isolate against Static-Graph DAG's fixed graph;
see config/prompts/coordinators/dynamic_graph/README.md.

Three tools-off/tools-on turns, not one:
1. `coordinator.md` (tools-off triage) — refuse / answer informationally / proceed.
   Unlike Static-Graph DAG's triage, this one states no plan: scope and order are the
   manager_agent's own job, decided inside the workflow, not stated here. `chat_response`
   is therefore empty whenever `proceed` is true, so `run.plan_presented` reads false for
   every Dynamic-Graph run — a genuine, documented property of this condition (see the
   README), not a measurement gap.
2. The workflow itself (only if turn 1 set `proceed=true`): `manager.md` drives the
   ledger; each of the five domain specialists is a participant.
3. `aggregator.md` (tools-off) — the manager's own final answer is plain text with no
   schema hook (`prepare_final_answer()` returns `response.text` directly — MAF's
   Magentic implementation, confirmed by reading `agent_framework_orchestrations/
   _magentic.py`), so a separate turn converts it into real `MasterOutput` JSON. It
   receives the manager's final text, not each participant's raw output — the ledger has
   already synthesised those into that text, and the app captures the row-level data
   directly from the tool-call stream, the same as every other condition.

Interfaces this module must satisfy
-------------------------------------
Same two-turn contract as static_graph_dag.py: execute_topology.py calls
`create_session()` once, then `run(msg, session=, middleware=)` twice, and reads
`.value`/`.text` off each response. If turn 1 refuses or answers informationally, turn
2's harness message ("Yes, proceed.") has nothing to confirm — `run()` returns the
turn-1 response again rather than building the workflow.

Instrumentation: a `Workflow`'s `.run()` has no `middleware=` parameter — the harness's
`middleware=[recorder.middleware]` (passed to `master.run()` for every Agent-based
topology) cannot reach calls a workflow's internal executors make. MagenticAgentExecutor
calls each participant's `agent.run()` directly, with no per-call hook either (confirmed
by reading `agent_framework/_workflows/_agent_executor.py`). The only place left is Agent
construction: `Agent.run()` merges `self.middleware` (set at construction) with any
`middleware=` passed at call time (confirmed by reading `agent_framework/_middleware.py`),
so a participant built with `middleware=[recorder.middleware]` is captured on every round
the manager calls it, however many rounds that turns out to be. `core/agents.py`'s
build_X_agent() functions take this as an optional kwarg; every other topology still
builds them with `middleware=None` and is unaffected. This means fresh participant
instances per run, not the shared singletons other topologies import — a singleton built
once at import time cannot carry a per-run recorder.
"""

import os
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from dotenv import load_dotenv, find_dotenv
load_dotenv(dotenv_path=find_dotenv(), override=False)

from agent_framework import Agent, AgentResponse
from agent_framework_orchestrations import MagenticBuilder
from agent_framework_orchestrations._magentic import (
    MagenticOrchestratorEvent, MagenticOrchestratorEventType,
)

from config import get_instruction
from core.agents import (
    build_predict_agent, build_diagnose_agent, build_simulate_agent,
    build_recommend_agent, build_email_agent,
)
from core.clients import chat_client
from core.schemas import MasterOutput, TriageDecision
from measurement.dependencies import canonical
from measurement.instrumentation import get_active_recorder

TOPOLOGY = "dynamic_graph"

# label -> canonical tool name, same names topologies/sequential.py's _TOOLS uses and
# measurement/dependencies.py's TRUE_DEPENDENCIES is keyed on. Used both to pick each
# participant's own capability score artifact under the right key (R45) and, for the
# manager, a name that intentionally matches none of these so its usage is bucketed as
# orchestration cost, not specialist cost (see agent_completion_middleware's docstring).
_CANONICAL_TOOL_NAMES: dict[str, str] = {
    "predict":   "predict_delivery_delays_tool",
    "diagnose":  "diagnose_delay_patterns_tool",
    "simulate":  "delay_simulations_tool",
    "recommend": "recommendation_tool",
    "email":     "email_alert_tool",
}
_MANAGER_USAGE_NAME = "dynamic_graph_manager_turn"

# Ledger trace visibility -- off by default. Prints the manager's internal facts/plan/
# progress-ledger reasoning to the console (see _print_ledger_trace); off by default
# because it is verbose and console-only (never persisted, see that function's
# docstring). Same boolean-env pattern as SC_NO_CACHE elsewhere in this app.
_SHOW_LEDGER = os.getenv("SC_SHOW_LEDGER", "").strip().lower() in ("1", "true", "yes")


def _build_participants(middleware) -> list[Agent]:
    """Fresh per-run specialist agents, each carrying the recorder's middleware at
    construction (see module docstring for why construction time, not call time)."
    The default plan prompt (ORCHESTRATOR_TASK_LEDGER_PLAN_PROMPT, in the installed
    agent_framework_orchestrations/_magentic.py) — it already tells the manager explicitly:
    'Remember, there is no requirement to involve all team members. A team member's particular
    expertise may not be needed for this task'

    Each participant also gets its own agent_completion_middleware (R45, 29-Aug-26): MAF
    calls a participant's own agent.run() directly, invisible to record_sub_agent_usage()
    and to the tool_call-based capture every other topology relies on for scoring -- see
    measurement/instrumentation.py's module docstring. Without it every Dynamic-Graph run
    shows $0.0000/0 tokens for all 5 specialists and diagnose/recommendation/email are
    mathematically capped at their raw-artifact-only weight regardless of quality.
    get_active_recorder() returns None outside a track_sub_agents() scope (e.g. a
    standalone/dev call) -- falls back to function-middleware only, same as before."""
    fn_mw = [middleware] if middleware else []
    recorder = get_active_recorder()

    def _mw(label: str) -> list | None:
        m = list(fn_mw)
        if recorder is not None:
            m.append(recorder.agent_completion_middleware(
                _CANONICAL_TOOL_NAMES[label], capture_artifact=True))
        return m or None

    return [
        build_predict_agent(chat_client, middleware=_mw("predict")),
        build_diagnose_agent(chat_client, middleware=_mw("diagnose")),
        build_simulate_agent(chat_client, middleware=_mw("simulate")),
        build_recommend_agent(chat_client, middleware=_mw("recommend")),
        build_email_agent(chat_client, middleware=_mw("email")),
    ]


def _print_ledger_trace(result) -> None:
    """

    Print to Console Gated by SC_SHOW_LEDGER (0 i.e. off by default) -- called only when that flag is set; see
    its definition near the top of this module. If SC_SHOW_LEDGER is 1, 
    print the manager's internal facts/plan/progress-ledger reasoning to the console.

    Unlike Static-Graph DAG, this condition states no plan on turn 1 (see module
    docstring) -- the manager's own plan() call happens INSIDE the workflow instead, and
    without this, nothing surfaces it anywhere: not the console, not the run record.
    `WorkflowRunResult` is itself a list of `WorkflowEvent`s (agent_framework/_workflows/
    _workflow.py), and Magentic emits one `magentic_orchestrator` event per plan/replan/
    progress-ledger step (agent_framework_orchestrations/_magentic.py) -- this just reads
    them back. Console-only, not persisted to run_store: plan_presented/turn1_plan_text
    specifically mean "shown to the user before any capability ran", which this text
    deliberately is not (that is the property under measurement) -- reusing those columns
    for it would misrepresent what actually happened.
    """
    for event in result:
        data = getattr(event, "data", None)
        if not isinstance(data, MagenticOrchestratorEvent):
            continue
        if data.event_type in (MagenticOrchestratorEventType.PLAN_CREATED,
                               MagenticOrchestratorEventType.REPLANNED):
            label = "PLAN" if data.event_type == MagenticOrchestratorEventType.PLAN_CREATED else "REPLAN"
            print(f"  >> ledger {label}:")
            for line in (data.content.text or "").splitlines():
                print(f"       {line}")
        elif data.event_type == MagenticOrchestratorEventType.PROGRESS_LEDGER_UPDATED:
            ledger = data.content
            print(f"  >> ledger round: satisfied={ledger.is_request_satisfied.answer}  "
                  f"next={ledger.next_speaker.answer!r}")
            print(f"       reason: {ledger.next_speaker.reason}")


def _build_manager_agent() -> Agent:
    """The Magentic manager: drives facts/plan/progress-ledger/final-answer from
    manager.md. No response_format here — that would apply to every one of those
    internal calls uniformly, including the progress-ledger's own strict JSON parsing,
    which manager.md's instructions must not compete with.

    store=False is required, not optional (29-Aug-26, first live run). Magentic folds
    each participant's own messages -- including its internal tool-call/tool-result pair
    -- into the shared chat_history it resends in full on every manager call
    (_process_participant_response() in agent_framework_orchestrations/
    _base_group_chat_orchestrator.py returns them unfiltered). StandardMagenticManager's
    session is stateful by default (OpenAI's client captures a conversation_id/
    previous_response_id from every response unless store=False -- confirmed in
    agent_framework_openai/_chat_client.py). Combined, the manager was sending an
    explicit history containing a participant's tool-call id alongside a server-side
    previous_response_id that never saw that id, which the Responses API rejects with
    'No tool call found for function call output with call_id ...' -- reproduced on a
    live Q6 run, identically on every Magentic-triggered reset. No other topology in
    this codebase replays one agent's tool-call messages into a second agent's session,
    which is why this is specific to Dynamic-Graph.

    Also carries agent_completion_middleware (R45, 29-Aug-26), capture_artifact=False:
    StandardMagenticManager._complete() calls self._agent.run() directly for every
    internal facts/plan/progress-ledger/final-answer call (agent_framework_orchestrations/
    _magentic.py) -- none of that reaches record_sub_agent_usage() either, so the
    manager's own real reasoning cost was previously invisible, same root cause as the
    participants'. _MANAGER_USAGE_NAME deliberately matches no canonical tool name, so
    RunRecorder.split_usage() counts it as orchestration cost, not specialist cost --
    same convention topologies/sequential.py uses for its own forced coordinator turns."""
    recorder = get_active_recorder()
    mw = ([recorder.agent_completion_middleware(_MANAGER_USAGE_NAME, capture_artifact=False)]
          if recorder is not None else None)
    return Agent(
        name="Dynamic-Graph Manager",
        instructions=get_instruction("manager", topology=TOPOLOGY),
        client=chat_client,
        middleware=mw,
        default_options={"temperature": 0, "store": False},
    )


class DynamicGraphCoordinator:
    """Exposes the Agent surface execute_topology.py drives.

    Turn 1 is a tools-off classification call (coordinator.md): refuse, answer
    informationally, or proceed — it does not choose scope or order, and states no
    plan (see module docstring). Turn 2 builds and runs the Magentic workflow (only if
    turn 1 set proceed=true), then a tools-off aggregator turn converts the manager's
    plain-text final answer into MasterOutput; otherwise turn 2 returns turn 1's
    response again, since there is nothing to confirm.
    """

    def __init__(self):
        self._query: str = ""
        self._triaged = False
        self._proceed = False
        self._final: AgentResponse | None = None   # turn 1's response when proceed=False

    def create_session(self, *, session_id: str | None = None):
        # Required by the harness. Nothing to hold across turns: no model spans both
        # turns, and the workflow is built fresh from the stored query in turn 2.
        return None

    async def run(self, message: str, *, session=None, middleware=None) -> AgentResponse:
        if not self._triaged:
            self._triaged = True
            self._query = message
            triage = Agent(
                name="Dynamic-Graph Triage",
                instructions=get_instruction("coordinator", topology=TOPOLOGY),
                client=chat_client,
                default_options={"temperature": 0, "response_format": TriageDecision},
            )
            response = await triage.run(message)
            decision: TriageDecision = response.value
            self._proceed = bool(decision and decision.proceed)
            self._final = response
            return response

        if not self._proceed:
            # Turn 1 already refused or answered informationally — the harness's
            # "Yes, proceed." has nothing to confirm.
            return self._final

        recorder_middleware = middleware[0] if middleware else None
        workflow = MagenticBuilder(
            participants=_build_participants(recorder_middleware),
            manager_agent=_build_manager_agent(),
        ).build()
        result = await workflow.run(self._query)
        if _SHOW_LEDGER:
            _print_ledger_trace(result)
        outputs = result.get_outputs()
        if not outputs:
            raise RuntimeError(
                "Dynamic-graph workflow produced no output. The manager's own final "
                "answer is the workflow's only yielded output — check the ledger "
                "reached completion rather than stalling out."
            )
        ledger_text = getattr(outputs[0], "text", "") or ""

        # Ground truth for R46: the manager's own final note (above) is free text with
        # no check against what actually ran, and was observed writing a full
        # recommendation_summary-worthy paragraph on a run that never consulted
        # Recommendation at all. recorder.tool_calls only gains a row per participant
        # when agent_completion_middleware actually fires for it (R45), so this reads
        # real execution, not the manager's account of it.
        recorder = get_active_recorder()
        ran_canonical = {canonical(tc.name) for tc in recorder.tool_calls} if recorder else set()
        ran_labels = [label for label, name in _CANONICAL_TOOL_NAMES.items() if name in ran_canonical]
        not_ran_labels = [label for label in _CANONICAL_TOOL_NAMES if label not in ran_labels]

        aggregator = Agent(
            name="Dynamic-Graph Result Aggregator",
            instructions=get_instruction("aggregator", topology=TOPOLOGY),
            client=chat_client,
            default_options={"temperature": 0, "response_format": MasterOutput},
        )
        self._final = await aggregator.run(
            f"Original request:\n{self._query}\n\n"
            f"The orchestrator's own ledger has finished, after however many rounds "
            f"it needed, and wrote this final note:\n\n{ledger_text}\n\n"
            f"Capabilities actually consulted this run: {', '.join(ran_labels) or 'none'}.\n"
            f"Capabilities NOT consulted this run: {', '.join(not_ran_labels) or 'none'} -- their "
            f"summary fields must stay empty regardless of what the note above says."
        )
        return self._final


def build_master() -> DynamicGraphCoordinator:
    """Entry point used by topologies/registry.py. A factory, not a module-level object,
    so prompts are read at call time rather than frozen at import."""
    return DynamicGraphCoordinator()
