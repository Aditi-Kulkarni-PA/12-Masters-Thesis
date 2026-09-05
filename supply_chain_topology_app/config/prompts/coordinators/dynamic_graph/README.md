# Dynamic-Graph (MAF MagenticBuilder) — condition manifest (OPTIONAL, gated at T100)

**Who decides order:** the orchestrator's own Task Ledger / Progress Ledger, deciding
"who acts next" from completed state. `manager.md` is task-framing only — no ordering,
no edges, no plan. This is the one condition where the framework itself (not the prompt)
supplies most of the adaptivity.

**Files in this folder (restructured 29-Aug-26 — see below):**
- `coordinator.md` — tools-off triage: refuse / answer informationally / proceed. States
  no plan (unlike Static-Graph DAG's triage): scope and order are `manager.md`'s own
  job, discovered inside the workflow, not stated up front. `run.plan_presented`
  therefore reads **false for every Dynamic-Graph run** — a genuine, documented property
  of this condition, not a measurement gap. The manager's own internal planning (its
  `plan()` call, inside the workflow) still happens; it is simply never surfaced to the
  user before execution, which is the point of not fixing a plan in advance.
- `manager.md` — task framing only, deliberately thin, given to `manager_agent`. The 5
  "participants" listed are named for the orchestrator's benefit, not wired as a
  fixed-order tool menu.
- `aggregator.md` — tools-off, closes the run by converting the ledger's plain-text
  final answer into real `MasterOutput` JSON (the manager's own final-answer call has
  no schema hook — see `master.md`, kept on disk as a design note, for why).
- `master.md` — retired; explains the 3-way split above. Not read by any topology code.

**Why 3 files, not 1:** `manager_agent`'s `default_options` (including `response_format`)
apply uniformly across every internal call the manager makes — facts, plan,
progress-ledger, final-answer. `prepare_final_answer()` returns plain text with no schema
hook at all, so a single file could never actually produce `MasterOutput` JSON; and
bundling security/chat handling into the same instructions the progress-ledger call
relies on (strict JSON, retry-then-raise) risked corrupting that call. Confirmed by
reading `agent_framework_orchestrations/_magentic.py` directly, not assumed.

**Assembled `@include` chain — `manager.md`:** `participant_capabilities` →
`input_handling` → `scope_selection` → `dependency_discovery` → `self_check` →
`exception_handling`.

**Why `dynamic_graph.py` passes all 5 specialists to `MagenticBuilder(participants=...)`
(29-Aug-26, Aditi's question):** this is deliberate, not an oversight, and not the same
situation as Static-Graph DAG always running all 5. All 5 must be *available* — the same
way Planner-Executor always binds all 5 tools and lets the model choose which to call —
because pre-filtering the participant list would mean an LLM commits to scope *before*
the workflow runs, which is exactly Sequential's mechanism, not Dynamic-Graph's. Doing
that here would collapse this condition's distinguishing property (the ledger discovers
scope AND order live, from completed state) into a Magentic-flavoured Sequential, and
erase the dynamism-axis comparison against Static-Graph DAG for the same reason the
"do not hardcode edges" instruction above exists.

Whether the manager actually behaves selectively is then a prompt question, not a code
one — checked two ways: (1) `ORCHESTRATOR_TASK_LEDGER_PLAN_PROMPT`, MAF's own built-in
plan prompt (confirmed by reading `agent_framework_orchestrations/_magentic.py`), already
tells the manager "there is no requirement to involve all team members. A team member's
particular expertise may not be needed for this task" — the framework's default behaviour
is not biased toward using everyone. (2) `manager.md`'s own "Choosing your team" section
reinforces this locally, the same way Planner-Executor's master.md states "You decide
which specialist tools are needed" next to its own tool roster — added 29-Aug-26 after
the original wording (inherited near-verbatim from the pre-restructure master.md) turned
out to state availability without ever stating selectivity.

Because this constraint is now purely prompt-level with no code-level backstop (unlike
Sequential's reduced tool set), Dynamic-Graph's `unprompted` tool-call rate on a narrow
query (e.g. Q6) is a real thing to check on the first live runs, not just DAG's -- if the
manager involves every participant regardless of what the query actually asked for, that
is a genuine finding about this mechanism, not a prompt bug to silently patch away.

**Important asymmetry to document, not silently normalize away:** MagenticBuilder's
ledger is itself a form of built-in replanning/reflection — this condition may end up
with *more* native adaptivity than the uniform `self_check.md` instruction gives the
other 4 model-driven conditions, simply because the framework provides it structurally.
That's a legitimate property of testing the dynamism axis (§3,
`Multi_Agent_Topology_Reference.md`), not a bug — but it should be named explicitly in
the results/limitations chapter rather than assumed away, alongside the residual
Group A vs Group B asymmetry noted in `self_check.md`.

**Plan turn:** no, deliberately (29-Aug-26) — see `coordinator.md` above and its
`plan_presented` note.

**Marginal integration cost (T103):** gated behind the T100 go/no-go decision alongside
Mesh. Pick up only if time permits post-core-condition completion.

**Blocking prerequisite — resolved 29-Aug-26:** confirmed by reading
`agent_framework_orchestrations/_magentic.py` directly in the installed environment:
`MagenticBuilder(participants=..., manager_agent=...).build()` exists and returns a
`Workflow` (the same type `WorkflowBuilder` produces for Static-Graph DAG). Built against
this confirmed API, not the class name alone — see `topologies/dynamic_graph.py`.

## First live runs (29-Aug-26, Q6/Q5/Q10) — two real bugs, one structural finding

**Bug 1 (blocking, fixed): manager_agent's progress-ledger call failed on every run**
with the OpenAI Responses API error "No tool call found for function call output with
call_id ...", looping identically through Magentic's own reset-and-retry. Root cause:
Magentic folds each participant's own tool-call messages into the shared `chat_history`
it resends in full to the manager (`_process_participant_response()` returns
`response.agent_response.messages` unfiltered), while `StandardMagenticManager`'s own
session is stateful by default (the OpenAI client captures a `conversation_id` from every
response unless `store=False`). The two together meant the manager sent an explicit
history containing a participant's tool-call id alongside a server-side
`previous_response_id` that had never seen it. Fixed by setting `"store": False` on
`manager_agent`'s `default_options` in `topologies/dynamic_graph.py` — see that file's
`_build_manager_agent()` docstring for the full trace. No other topology in this codebase
replays one agent's tool-call messages into a second agent's session, so this is specific
to Dynamic-Graph.

**Bug 2 (fixed): diagnose_delay_patterns was never called, on all 3 first live runs** —
including Q10, which explicitly needs it, and which produced a real dependency violation
(`recommendation_tool ran but diagnose_delay_patterns_tool never did` — recommend requires
diagnose's summary as an argument). Root cause: none of `core/agents.py`'s five
`build_X_agent()` functions set `description=` on the `Agent` objects. Magentic's
participant registry falls back to a literal placeholder when unset — confirmed by
reading `agent_framework_orchestrations/_base_group_chat_orchestrator.py`:
`"<no description, use name to identify the purpose of this participant>"` — and that
placeholder, not any real information about what each specialist does, is what gets
rendered into the `{team}` block on every ledger call. The manager was choosing
`next_speaker` almost blind. Fixed by adding `description=CAPABILITY_DESCRIPTIONS[...]`
to each `build_X_agent()` (the same neutral, ordering-free descriptions
`core/tool_descriptions.py` already provides Planner-Executor's tool schema — one source,
no duplication). Not yet re-verified against a live run.

**Plan visibility gap (fixed, console-only):** with no fixed plan text on turn 1 (by
design, see above), the manager's real facts/plan/progress-ledger reasoning was
happening but surfacing nowhere — not the console, not the run record. Fixed:
`topologies/dynamic_graph.py`'s `_print_ledger_trace()` reads `WorkflowRunResult` (itself
a list of `WorkflowEvent`s) for the `magentic_orchestrator` events MAF already emits per
plan/replan/progress-ledger step, and prints them. Deliberately NOT written to
`run.turn1_plan_text`/`plan_presented` — those columns specifically mean "shown to the
user before any capability ran," which this text is not; that gap is the property under
measurement, not a defect to paper over.

**Structural finding, not a bug: Dynamic-Graph did not exploit concurrency on Q10**, even
though predict/simulate/recommend/email were all independently ready early (span 135.5s
vs a 16.2s critical path; 3 pairs flagged "free to overlap but ran sequentially";
~119s of the run was the coordinator "deciding between waves"). This is not fixable
without abandoning MagenticBuilder's actual mechanism: `ORCHESTRATOR_PROGRESS_LEDGER_PROMPT`
asks for exactly one `next_speaker` per round (`"answer": string`, not a list), so the
manager can only ever dispatch one participant, wait for its full turn, then run another
complete progress-ledger call before dispatching the next — a moderated-conversation
pattern, not a scheduler. Static-Graph DAG's fan-out edges genuinely parallelize
independent work at the framework level; Dynamic-Graph structurally cannot, no matter how
well the manager reasons. This is real evidence for the concurrency axis alongside the
dynamism axis already documented above — Dynamic-Graph is expected to win on adaptivity
and lose on scheduling efficiency, and that trade-off, not either alone, is the finding.

## Why `manager.md` says "do not fix the sequence in advance" (reworded 23-Aug-26, moved from master.md 29-Aug-26)

The prompt used to say hardcoding edges "collapses this condition into Static-Graph DAG
and erases the dynamism-axis comparison the two conditions exist to isolate", citing
`Multi_Agent_Topology_Reference.md` §3. That is the correct *design* reason and it is why
the constraint exists — but stating it in the prompt told the model it was one arm of a
comparison against another named arm, which is a demand characteristic and a topology
leak (Risk Log R27). The operative instruction is unchanged; only the justification moved
here.
