# How the Topologies Actually Work

[← Documentation index](../README.md) · [Topology reference](topology-reference.md)

The mechanics that only became visible from building and running these conditions. Each
entry is something the design documents do not say and the framework does not advertise,
and each was confirmed from source or from a live run rather than assumed.

This matters for the thesis because several of these determine what a measure *means*.
A reader who does not know that Dynamic Graph rebuilds its ledger every turn cannot
interpret its token counts.

---

## 0. Orchestration Structure and Control Across the Tested Conditions

![Orchestration autonomy versus centralization. The vertical axis runs from static at the bottom, through deterministic, to autonomous at the top. The horizontal axis runs from fully decentralized on the left to fully centralized on the right. The nine conditions tested in this study are marked in purple; nine untested reference patterns are marked in grey. Orange dotted arrows mark four places where an orchestration decision was taken from the model and placed in code: Swarm to Swarm-CA, Monolith to Planner-executor to Sequential, and Static Graph Routed to Static Graph DAG.](../assets/orchestration_autonomy_vs_centralization_topology_landscape.png)

The vertical axis is this study's own question — who decides what runs and when. **Static**
means the schedule is fixed in code and no model decides orchestration. **Deterministic**
means a decision is made once, then enforced or bounded. **Autonomous** means a model
decides continuously and can re-decide.

Three properties of this layout matter for everything below:

- **Every arrow points the same way.** All four mark the same kind of move — an
  orchestration decision taken from the model and placed in code. Monolith → Planner-executor
  → Sequential is a staged withdrawal (free tool-calling, then a plan fixed before
  execution, then `max_function_calls=1` forcing one tool per turn). Static Graph Routed →
  Static Graph DAG replaces the router with `_FIXED_PLAN`. Swarm → Swarm-CA hands dependency
  enforcement to the wave loop. The conditions are not an unordered set; they are points on
  one gradient.
- **Both controlled pairs are near-vertical moves.** Each pair changes autonomy while
  holding centralization roughly constant, which is what makes them interpretable. A pair
  that drifted sideways here would be varying two things at once.
- **Monolith lands beside Dynamic Graph, by a different route.** Both are centralized and
  autonomous, but Dynamic Graph is a manager coordinating specialists while Monolith has
  no delegation at all. These axes cannot separate those two cases; §2 and
  [topology-reference.md §5](topology-reference.md#5-reading-a-conditions-measures) can.

### The autonomy axis hides a split

The second figure separates **scope control** on vertical axis (which capabilities run) from **dependency control** (when they may run) on x-axis. This distinction is important because the two controlled pairs change different aspects of orchestration.

The untested cells represent design coverage limits, not claims about the broader design space.

Separating them reorders the conditions:

![Who decides scope versus who enforces dependency order. A three by three matrix. Columns are who decides scope: fixed in code, model once, model continuously. Rows are who enforces dependency order: model decides at the top, code gates in the middle, code schedules at the bottom. Static Graph DAG sits in fixed-code scope with code scheduling. Static Graph Routed sits in model-once scope with code scheduling. Sequential and Planner-executor sit in model-once scope with the model deciding order. Monolith, Dynamic Graph, Mesh and Swarm sit in model-continuous scope with the model deciding order. Swarm-CA sits directly below them in the same scope column, with code gating on dependencies. A dotted arrow runs vertically from Swarm to Swarm-CA; a second runs horizontally from Static Graph Routed to Static Graph DAG.](../assets/who_decides_scope_versus_who_decides_timing.png)

**Code never decides scope in Swarm-CA, and does not schedule either — it only defers.**
Two facts from `swarm_constrained_adaptive.py` fix its position. `request_specialist` is
attached to every specialist by `_build_specialist()`, alongside `read_blackboard` and
`write_blackboard`, and the wave loop folds `blackboard.pending_requests` in at the top of
every wave — so scope keeps growing on the model's initiative, exactly as in plain Swarm.
And `_prerequisites_met()` is a pure predicate: it returns a bool, and never adds to or
removes from `still_needed`. Its own docstring is explicit that the dependency table is
reused here "to GATE... not an orchestration structure being imposed on the agents."

So Swarm and Swarm-CA share the scope column, and the pair separates on dependency
enforcement alone — which is what the
[topology reference](topology-reference.md#3-the-nine-conditions) claims for it. The gate
does not pick capabilities and does not order them beyond refusing to start one whose real
inputs have not posted yet.

Two further readings:

- **The two controlled pairs isolate different axes.** Swarm → Swarm-CA is a vertical
  move: same scope authority, dependency enforcement handed to the gate. Static Graph
  Routed → Static Graph DAG is a horizontal move: same code scheduling, scope taken from
  the router and fixed in `_FIXED_PLAN`. Neither pair is a diagonal, which is what makes
  each one readable on its own.
- **Four of nine cells are unsampled**, and not at random: nothing built here lets code fix
  scope while a model still controls dependency order. That is a coverage limit of this
  design, not a property of the design space.

---

## 1. Context passing — what each condition actually hands a specialist

The single most consequential difference between the conditions, and the least visible
from a topology diagram.

| Condition               | What the specialist receives                                                     | What this means                                                                                  |
| ----------------------- | -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| **Monolith**            | Nothing separate — one agent holds the entire task in one context                | No separate specialist attribution; coordinator time is also capability work                     |
| **Sequential**          | One tool call per turn, driven by the coordinator                                | Context accumulates with the coordinator rather than with separate specialists                   |
| **Planner–Executor**    | A task string created by the planner and passed through a wrapper tool           | Parameter-specific guidance must be defined in `Annotated[...]`, not the tool docstring — see §3 |
| **Static Graph DAG**    | A self-contained task string for each node                                       | A node sees another node's output only when the graph explicitly passes it                       |
| **Static Graph Routed** | Same as DAG, but only for nodes selected by the router                           | Context handling is the same as DAG; only the set of executed nodes differs                      |
| **Dynamic Graph**       | The manager's **ledger**, rebuilt on every turn                                  | The manager prompt grows as the execution history grows — see §2                                 |
| **Mesh**                | `_task_for(cap, query, results)` — the query plus accumulated peer results       | A peer receives an upstream result only if that peer has already produced it                     |
| **Swarm**               | `spec.task`, created by the seed planner in turn 1                               | The task is created before prerequisites run, so it cannot initially contain their results       |
| **Swarm-CA**            | Same as Swarm, plus a mandated `read_blackboard` call for required prerequisites | The blackboard read supplies prerequisite data that was not available when the task was created  |


### The context-propagation finding

Two systems can have the same apparent dependency: Diagnosis → Recommendation, but behave differently depending on when task context is created and how results are propagated.

Swarm and Swarm-CA both showed a specialist calling its domain tool with a **required
argument left empty**, because the seed planner's task string was written before the
prerequisite existed. `recommend_actions()` takes `diagnosis_summary` as a required
argument and rejected it via its own minimum-length guard — fast, valid JSON, no error
surfaced. 

Mesh's defining property, the thing that makes it a distinct condition rather than Swarm with a graph on top, is that information moves by one peer directly addressing another — not by any node independently polling a shared board on its own initiative. A read_blackboard mandate would have made Mesh behaviorally converge toward Swarm's decoupled-broadcast model, which is exactly the variable this pair of conditions is supposed to keep separate.

Mesh hit the same class of failure for the same reason and was fixed by
threading accumulated `results` into `_task_for()`. `run.results` is a plain dict on a single shared `_RunContext` every PeerNode holds a reference to — so Mesh does have shared state, in that sense. What it doesn't have is anything resembling Swarm's Blackboard, which is purpose-built machinery — a Pydantic-typed store

**"Posted to the blackboard" is not "in this specialist's context."** That distinction
cost two live runs before it was understood.

>A dependency in the execution design does not guarantee that the dependent agent receives the information it needs. Context must be propagated or retrieved at execution time.

>Swarm demonstrated that assigning a dependent task before its prerequisite has executed can leave the required input empty. Swarm-CA addresses this by requiring the specialist to retrieve prerequisite results from the blackboard. 

>Mesh encountered the same context-propagation problem and was corrected by passing accumulated peer results into _task_for().

---

## 2. Per-condition mechanics

| Condition               | Mechanic discovered                                                                                                                                                                                                   | Why it matters                                                                                                                                                                                        |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Monolith**            | Uses raw tools directly rather than wrapping them as sub-agents                                                                                                                                                       | There is no separate per-capability timing or token attribution. The `0.000` token shares are therefore **correct**, not missing data.                                                                |
| **Sequential**          | `max_function_calls=1` is **required for the condition**. MAF otherwise allows the forced tool call to repeat within its internal loop.                                                                               | This parameter defines the sequential behavior. Without it, the same tool can repeat for multiple iterations and the condition is no longer truly sequential.                                         |
| **Planner–Executor**    | The `@tool` decorator uses the **whole-function docstring** as the tool description. Text describing individual parameters in the docstring is not passed to the model.                                               | Per-argument guidance must be placed in `Annotated[...]`. Instructions such as "pass through verbatim" can otherwise be silently lost.                                                                |
| **Static Graph DAG**    | Every node in the fixed graph runs, whether or not the query needs it.                                                                                                                                                | This gives high coverage but can reduce precision. This is an **intentional property of the condition**, not an execution failure.                                                                    |
| **Static Graph Routed** | Uses the same fixed graph, but an intent router decides which nodes are opened.                                                                                                                                       | The router is the key difference from DAG, making DAG vs Routed a clean controlled comparison of intent gating.                                                                                       |
| **Dynamic Graph**       | `MagenticBuilder` rebuilds a **progress ledger on every turn**. The final response also uses `response.text` rather than the response-format hook, so a separate finalization step is needed for `MasterOutput` JSON. | Prompt size grows with the number of turns, which generally grows with the number of capabilities. This creates a context-window risk for complex queries, particularly with lower-tier LLM models. |
| **Mesh**                | Each capability communicates directly with its peers; there is no central coordinator. Because MAF's workflow builder rejects cyclic graphs, peer messaging is implemented outside the workflow graph.                | A separate hop limit and `MAX_RUNS_PER_CAPABILITY` are needed to prevent repeated peer-to-peer routing.                                                                                               |
| **Swarm**               | The seed plan identifies what can start initially. Later capabilities run only when a specialist **chooses to request them** through `request_specialist`.                                                            | A capability can be skipped if no specialist makes the request. This happened twice in testing: `diagnose` did not request `recommend`.                                                               |
| **Swarm-CA**            | Uses the same Swarm mechanism, but the wave loop checks `TRUE_DEPENDENCIES` before starting a capability.                                                                                                             | Required dependencies are enforced by code, while genuinely new or emergent tasks can still be added by agents. This isolates the effect of dependency-aware execution timing.                        |


---

## 3. Framework behaviours that shaped the implementations

Confirmed from `agent_framework` source, not documentation.

| Behaviour | Where confirmed | Effect on this study |
|---|---|---|
| `tool_choice="required"` forces **a** tool call, not a specific ordered sequence, and MAF resets it to `"auto"` after one iteration | `agent_framework/_tools.py` | A prompt-level mandate to call two tools in order cannot be enforced. Write-compliance had to be *measured*, then force-captured, rather than assumed |
| The `@tool` decorator does not parse per-argument text from a docstring | `agent_framework/_tools.py` | All per-parameter guidance moved to `Annotated[...]` |
| Magentic's `prepare_final_answer()` returns `response.text`, bypassing the response format | `agent_framework_orchestrations/_magentic.py` | Dynamic Graph needs a third turn to produce structured output |
| Middleware attaches at call time, not construction | `agent_framework/_middleware.py` | Instrumentation passes `middleware=` per invocation |
| The workflow builder rejects cyclic graphs | `agent_framework/_workflows/` | Mesh's peer messaging is implemented outside the workflow abstraction |
| A response's parsed value lives behind `_value` / `_value_parsed` | `agent_framework/_types.py` | Code-assembled `MasterOutput` must set both |

---

## 4. Turn handling — a cross-cutting property

Every condition returns `None` from `create_session()`. There is no shared chat history;
state lives in instance flags on the entry point.

| Consequence | Detail |
|---|---|
| The harness sends **two turns** | turn 1 plans or gates; turn 2 confirms, or answers where the query defines a clarification |
| Turn 2's text is frozen per query | `query_metadata.clarification_response`, defined for **Q8 only** (1 of 11); every other query receives `"Yes, proceed."`. Identical across all conditions and all model tiers |
| A condition that declined on turn 1 originally discarded turn 2 | the early return meant "cannot act on a clarification" and "was never given one" were indistinguishable in the data |
| Fixed by re-deciding in place | a declined turn 1 re-enters once with the original query and the turn-2 message combined, then falls through to execution. Capped at one retry |
| The retry restores parity; it does not add budget | turn 2 is sent to every run anyway (the out-of-scope probe excepted). Before the fix a declining condition effectively received one turn where a proceeding one received two |
| Two shapes of turn handling exist | *triage-then-execute* (Dynamic Graph, Static Graph Routed) route on turn 1 and execute on turn 2; *gate-and-execute* (Swarm, Swarm-CA) do both in one pass |

That last distinction is why one retry fix did not work for all four conditions and had
to be written twice.

**Asking for clarification on Q8 is correct behaviour, not a failure.** Q8 is the one
deliberately ambiguous query (decision D6), and its clarifying answer is part of the frozen
query set rather than improvised per run — so a condition that asks receives the same
answer as every other condition at every tier, and is then judged on what it does with it
(decision D24).

What the protocol does not separate is a decline that was *wrong* — an in-scope request
refused on the condition's own turn-1 judgement — because `RoutedPlan.proceed` is a single
boolean covering refusal and informational answer alike. That is a reporting concern rather
than a validity one: both runs received the same two-turn protocol, so no condition is
advantaged, but only `behaviour_class` can say which was which, and it is populated for
**0 of 297 rows** (see
[decision-log.md §5](../decisions/decision-log.md#5-decisions-still-open)).

---

## 5. What this means for reading the measures

| Observation | Explanation |
|---|---|
| Dynamic Graph has the highest token count and lowest generated-token share | it re-reads its ledger rather than producing output; cost is re-reading, not writing |
| Swarm has the highest cost and lowest precision | it over-executes — the cost is redundant work, not coordination |
| Sequential's coordinator dominates its token count | one tool per turn means many coordinator round-trips |
| Monolith's orchestration and specialist shares are `0.000` | its work is not separable, not zero |
| Mesh runs more capabilities than any query requires | each peer decides its own downstream, with no coordinator to stop it |

---

[← Documentation index](../README.md) · [Topology reference](topology-reference.md)
