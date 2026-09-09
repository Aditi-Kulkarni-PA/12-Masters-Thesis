# How the Topologies Actually Work

[← Documentation index](../README.md) · [Topology reference](topology-reference.md)

The mechanics that only became visible from building and running these conditions. Each
entry is something the design documents do not say and the framework does not advertise,
and each was confirmed from source or from a live run rather than assumed.

This matters for the thesis because several of these determine what a measure *means*.
A reader who does not know that Dynamic Graph rebuilds its ledger every turn cannot
interpret its token counts.

---

## 1. Context passing — what each condition actually hands a specialist

The single most consequential difference between the conditions, and the least visible
from a topology diagram.

| Condition | What a specialist receives | Consequence |
|---|---|---|
| **Monolith** | nothing separate — one agent holds the whole task in one context | no attribution is possible; coordinator time *is* capability work |
| **Sequential** | one forced tool per turn, coordinator drives between turns | context accumulates in the coordinator, not the specialists |
| **Planner-Executor** | a task string built by the planner, passed through a wrapper tool | per-parameter guidance must live in `Annotated[...]`, not the docstring — see §3 |
| **Static Graph DAG** | a self-contained task string per node | no node sees another's raw output unless the graph passes it |
| **Static Graph Routed** | as DAG, for the subset the router opened | identical to DAG apart from which nodes run |
| **Dynamic Graph** | the manager's **ledger**, rebuilt every turn | prompt size grows with turn count — see §2 |
| **Mesh** | `_task_for(cap, query, results)` — the query **plus** accumulated peer results | a peer with a required upstream argument gets it only if that peer already ran |
| **Swarm** | `spec.task`, written by the seed planner in turn 1 | the task was authored *before* any prerequisite ran, so it cannot carry that prerequisite's content |
| **Swarm-CA** | as Swarm, plus a mandated `read_blackboard` call for true prerequisites | the read mandate exists because the task string alone cannot carry the data |

### The finding underneath this

Swarm and Swarm-CA both showed a specialist calling its domain tool with a **required
argument left empty**, because the seed planner's task string was written before the
prerequisite existed. `recommend_actions()` takes `diagnosis_summary` as a required
argument and rejected it via its own minimum-length guard — fast, valid JSON, no error
surfaced. Mesh hit the same class of failure for the same reason and was fixed by
threading accumulated `results` into `_task_for()`.

**"Posted to the blackboard" is not "in this specialist's context."** That distinction
cost two live runs before it was understood.

---

## 2. Per-condition mechanics

| Condition | Mechanic discovered | Why it matters |
|---|---|---|
| **Monolith** | Attaches raw tools rather than wrapping them as sub-agents | No per-capability duration or token attribution exists. Its `0.000` token shares are correct, not missing |
| **Sequential** | `max_function_calls=1` is **required, not tuning**. MAF re-applies `tool_choice` on every iteration of its internal loop and exits early only when the model returns no function call — impossible while a tool is forced. Without the cap, one forced turn repeats that tool up to `max_iterations` (40) | This single parameter *is* the condition. Remove it and Sequential stops being sequential |
| **Planner-Executor** | `agent_framework`'s `@tool` decorator reads only the **whole-function docstring** as the tool description. Per-argument text in the docstring never reaches the model | Guidance must be in `Annotated[...]`. A "pass through verbatim, do not summarise" instruction placed in the docstring was silently discarded |
| **Static Graph DAG** | Runs every level regardless of need | Complete coverage with low precision — by design, not a failure |
| **Static Graph Routed** | Same graph behind an intent gate | The only difference from DAG is which nodes open, which makes the pair a clean controlled contrast |
| **Dynamic Graph** | `MagenticBuilder` gives the manager a **progress ledger rebuilt every turn**, and the final answer bypasses the response-format hook — it returns `response.text`, so a separate turn is needed to produce real `MasterOutput` JSON. The finaliser sees the manager's summary, **not** each participant's raw output | Prompt size scales with turn count, and turn count scales with capability count. This is why it exhausts the context window on complex queries at lower tiers |
| **Mesh** | Each capability routes to its own peers with no coordinator. MAF's workflow builder rejects cyclic graphs, so peer messaging is implemented outside it | Requires its own hop budget and `MAX_RUNS_PER_CAPABILITY`, since nothing else bounds re-addressing |
| **Swarm** | The seed plan names what can start now; every later capability depends on a specialist **choosing** to request it via `request_specialist` | If a specialist omits the request, that capability simply never runs. Observed twice: `diagnose` never requested `recommend` |
| **Swarm-CA** | Identical to Swarm except the wave loop gates on `TRUE_DEPENDENCIES` — the same table used to *judge* every other condition | Removes free-text propagation of the routine dependency chain while leaving genuinely emergent additions agent-decided |

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
| The harness sends **two turns** | turn 1 plans or gates, turn 2 confirms |
| A condition that declined on turn 1 originally discarded turn 2 | the early return meant "cannot act on a clarification" and "was never given one" were indistinguishable in the data |
| Fixed by re-deciding in place | a declined turn 1 re-enters once with the original query and the turn-2 message combined, then falls through to execution. Capped at one retry |
| Two shapes of turn handling exist | *triage-then-execute* (Dynamic Graph, Static Graph Routed) route on turn 1 and execute on turn 2; *gate-and-execute* (Swarm, Swarm-CA) do both in one pass |

That second distinction is why one retry fix did not work for all four conditions and had
to be written twice.

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
