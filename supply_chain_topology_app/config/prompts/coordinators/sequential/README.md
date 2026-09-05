# Sequential — condition manifest

**How a run works — plan once, then execute rigidly.**

1. **Turn 1 — plan.** A tool-less sequence planner (`sequence_planner.md`) reads the query and
   returns the capabilities needed, in the order they must run, plus the action plan
   text. Its response IS turn 1, so the plan is genuinely presented and
   `run.plan_presented` is a real measured value for this condition.
2. **Turn 2 — execute.** A master agent is built carrying **only** the tools the plan
   names, and is driven one turn per planned step with `tool_choice` forced to that
   step's tool by name. It cannot substitute, add, skip, or reorder. A closing turn with
   tools off writes the structured output.

**What this condition isolates.** It shares every held-constant asset with
Planner-Executor — same wrapped agent-as-tools, same specialists, same
`@input_handling` contract — and differs on two axes:

1. **When ordering is decided.** Here, once, before any tool runs, by an agent that
   sees no results and cannot revise afterwards. Planner-Executor's master decides while
   executing and can react to what it just learned, add a step, or revisit one.
2. **Whether execution can overlap.** MAF runs multiple same-turn tool calls
   concurrently (verified under T28). Planner-Executor dispatches from one turn and can
   overlap everything the dependency graph permits; this condition forces one tool per
   turn and structurally cannot overlap anything — not even diagnose and simulate, which
   share only their dependence on predict.

Axis 2 is the primary latency trade-off. Expect this condition pinned at the fully-serial
floor, Static-Graph DAG at ~1.0 exploited, Planner-Executor in between depending on how
well it batches; `missed_concurrency()` will name the specific pairs left un-overlapped
here.

Planning quality is a measured outcome, not a guarantee: a plan that puts a capability
before its inputs produces `upstream_missing` and a recorded dependency violation, as it
would anywhere else. The dependency rules the planner works from live in its prompt, the
same place every other condition's do — no code re-derives or corrects the order.

**Reporting requirement.** Wall-clock cost here has two components that must be reported
separately: serialized tool execution, and the extra coordinator round-trips (one
planning completion plus one per planned step, against Planner-Executor's single
dispatching turn). Both are real properties of the topology, but conflating them hides
how much of the gap is serialization versus model turns. `tool_wall_span_s` and
`coordinator_idle_s` already separate them.

**Messages come from the harness.** Every forced turn re-sends the harness's own query
text; `tool_choice`, not message wording, selects the tool, so what each specialist is
asked for is the user's wording, exactly as in every other condition. The one exception
is the closing turn, which sends a fixed completion message rather than the harness's
"Yes, proceed." — with tools off the model read that as a request it could not fulfil
and reported being unable to proceed, leaving narrative fields empty (run 6cae417b).

**Why not a framework orchestration builder:** `agent_framework_orchestrations.
SequentialBuilder` and `GroupChatBuilder` exist in the installed environment (see T28's
tracker note) but are not used here. `GroupChatBuilder`/AutoGen's `RoundRobinGroupChat`
broadcast every response to every participant — a peer-to-peer shared-context shape,
closer to Mesh than to this condition's fixed chain. `SequentialBuilder` chains
participants by passing the growing conversation forward, which would feed each
specialist context its prompt was never tuned for; reusing Planner-Executor's wrapped
tools keeps every capability invoked exactly as it is in that condition.

**Files in this folder:** `sequence_planner.md` (turn 1's sequence planner) and
`coordinator.md` (turn 2's executor), plus `chatbot_behavior_sequential.md`, the local
behaviour partial that `coordinator.md` includes.

**Assembled `@include` chains:**

- `sequence_planner` → `chatbot_behavior_basic` → `tool_capabilities` →
  `scope_selection` → `dependency_basics` → `plan_confirmation`. It interprets the query,
  decides scope, derives the order and presents the plan, so it receives the same text
  Planner-Executor's master receives to make those decisions. It also carries all five
  tools in its function-calling schema with `tool_choice: "none"`, so those descriptions
  reach it through the same channel they reach PE's master; it can see them and cannot
  call them.

  Both halves were needed, and each was found only by running the other. Built tool-less,
  a weaker model omitted `predict` entirely; given the schema but no `scope_selection`, it
  took all five capabilities for a three-capability query. `scope_selection` was
  previously written inline in `planner_executor/master.md` and `swarm/master.md`, so
  those two conditions had it and this one did not.
- `coordinator` → `security_guardrails` → `chatbot_behavior_sequential`
  (→ `chatbot_behavior_basic`) → `tool_capabilities` → `dependency_basics` →
  `upstream_report_only` → `input_handling` → `self_check` → `exception_handling` →
  `output_contract`. It executes a confirmed plan, so `chatbot_behavior_sequential` adds
  only the turn-shape rules — no plan is re-presented.

**Why `dependency_discovery` is split rather than included whole.** That file bundles
three things: what each capability consumes and produces (`dependency_basics`), how to
recover from `upstream_missing` (`upstream_recovery`), and when work may overlap
(`concurrency_policy`). `dependency_discovery` is now just those three includes, so
conditions using it are unaffected. Neither prompt here can act on the last two — the
executor holds one forced tool per turn, so it can neither obtain a missing prerequisite
nor overlap anything, and the planner acts on nothing at all. Including them whole would
instruct both to do things they structurally cannot. `upstream_report_only` replaces the
recovery half for exactly that reason, and Static-Graph DAG will need the same partial.

This division is the rule, not an exception to it: **the same text for the same
decision; different text only where the affordance genuinely differs.** Deriving order
is a decision both conditions make, so both get `dependency_basics`. Recovering and
overlapping are affordances only one has, so only it gets those.

`input_handling` is essential in the coordinator chain and must stay — it is what
carries the orders path verbatim into each specialist's task string.

**Token accounting note:** the forced tool-calling turns sit between the harness's two
turns, so their usage is recorded through `record_sub_agent_usage` under
`sequential_coordinator_turn` to reach the run's grand total. Analysis separating
coordinator cost from specialist cost must filter that name out.

**Code-level exception handling equivalent:** since no model sits in the execution loop,
`self_check.md`'s "one retry, then stop and report" principle is enforced in the driving
code on a transient (non-`upstream_missing`) failure — see
`Topology_Prompt_Design_Spec.md` §implementation checklist.
