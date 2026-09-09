# Measure Definitions — every reported figure, traced to its code

[← Documentation index](../README.md)

Each measure the study reports, what it means, where it is computed, and the rule that
decides an edge case. A figure in the thesis can be traced from this table to the
function that produced it.

**Proposal cross-reference:** Sections 7.2.1 (raw), 7.2.2 (reliability and operational
validity), 7.2.3 (quality).

---

## 1. Raw measures — proposal 7.2.1

Recorded per run; no interpretation applied.

| Measure | Meaning | Computed in |
|---|---|---|
| `cost_usd` | total generation cost for the run | `run_store_writer` (stored), `measures.derive()` |
| `specialist_cost_usd` | cost of capability calls | `measures.cost_split()` |
| `orchestration_cost_usd` | cost of coordinator turns that are not capability calls | `run_store_writer` |
| `master_cost_usd` | cost of the final assembly turn | `run_store_writer` |
| `total_tokens`, `prompt_tokens`, `generated_tokens` | token counts, split by direction | `measures.token_split()` |
| `specialist_tokens`, `orchestration_tokens`, `master_tokens` | the same three groups, absolute | `measures.token_split()` |
| `wall_time_s` | end-to-end elapsed time | `execute_topology` → `run_store_writer` |
| `critical_path_s` | longest dependency-constrained chain of capability calls | `dependencies.concurrency_report()` |
| `actual_span_s` | first capability start to last capability end | same |
| `fully_serial_s` | total capability duration with no overlap | same |
| `coordinator_idle_s` | time inside the span with no capability running | same |
| `achievable_concurrency` | `fully_serial ÷ critical_path` — overlap the graph permits | `measures.cost_split()` |
| `actual_concurrency` | `fully_serial ÷ actual_span` — overlap realised | same |

> **Cost caveat.** Stored cost is token count × list price and is unreliable in both
> directions, because cached prompt tokens were priced at the fresh input rate. Report
> cost from provider billing; use stored cost only for *within-tier ratios* between
> conditions, where the same method applies to every condition.

---

## 2. Reliability and operational validity — proposal 7.2.2

| Measure | Definition | Edge-case rule |
|---|---|---|
| **(Clean Capability) Completion Rate** | complete when every capability the query required ran **and returned data** | extras ignored; a required capability returning a valid but empty list counts as incomplete |
| **Capability Coverage** | required capabilities executed ÷ required | `None` when the query requires none — not 0 |
| **Capability Precision** | required capabilities executed ÷ total executed | `None` when none executed — not 0 |
| **Dependency Violation Rate** | share of runs where a capability started before its input was produced | compares a dependent's *start* against its prerequisite's *end*, so concurrent dispatch is judged correctly |
| **Scheduling Deviation** | `abs(1 − critical_path ÷ busy)`, where busy is span minus coordinator idle | 0 is optimal; measures scheduling, not thinking time |
| **Infeasible Overlap Rate** | share of runs whose ratio exceeds 1.0 by more than the tolerance | signals a dependent pair ran concurrently — see §5 |
| **Orchestration Tax** | orchestration cost ÷ total cost | `None` where a condition records no separate coordination turns |
| **Declined** | run finished and executed nothing | requires *finished*; a crash that executed nothing is not a decline |

### Status vocabulary

`run_status` is assigned in `run_store_writer.py`, in this order:

| Status | Meaning |
|---|---|
| `no_execution` | the query implied capabilities and none ran |
| `failed` | a tool errored and there was ≤1 tool call |
| `partial` | a tool errored, or a **required** capability returned an empty payload |
| `success` | everything else |

`behaviour_class` is a **separate, orthogonal** field describing *what the run did
instead* — `answer_without_execution`, `wrongful_decline`, `clarification_request`,
`empty_output`. A run can carry both; they answer different questions.

---

## 3. Quality — proposal 7.2.3

Three levels, each with its own rule.

| Level | What happens | Rule that matters |
|---|---|---|
| **1. Per artifact** | a fixed LLM judge scores relevance, faithfulness, safety on 1–5 | criteria are identical across conditions |
| **2. Per capability** | artifacts blend into one capability score using fixed weights | a **missing part of an attempted capability scores zero**, not a renormalised skip |
| **3. Per run** | capability scores average equally | `judge_mean` weights relevance 0.45, faithfulness 0.45, safety 0.10 |

**Scope adjustment.** `judge_mean_scope_adj` inserts a zero for any capability the query
implied but the run never attempted, and for any run that did not complete. Without it, a
condition that skipped work would score higher than one that attempted it and did it
imperfectly.

**Structural absence.** Where a condition *cannot* produce an artifact by design — the two
swarm conditions have no coordinator that writes a narrative summary — the artifact is
dropped from the blend and its weight removed from the denominator. This is a third case,
distinct from both "attempted but missing = 0" and "capability never attempted".

---

## 4. Aggregation

| Level | Rule |
|---|---|
| Run | one observation |
| Query | median across repetitions for distributions; pooled for rates |
| Topology | median across queries for distributions; pooled across runs for rates |

**Scopes** are reported separately and must not be mixed: `workload` (the 10 substantive
queries), `out_of_scope` (the probe, where declining is correct), and `bin:<name>` (the
four complexity bins).

> **Axis warning.** "Spread across topologies" and "spread across complexity bins" are two
> different collapses of the same run-level scores and give different numbers. Neither is
> derivable from the other. Always state which axis a spread refers to.

---

## 5. Measures that look wrong and are not

| Observation | Why it is correct |
|---|---|
| Monolith reports token shares of `0.000` and a near-zero critical path | its work is not separable into coordination and execution; the coordinator time *is* the capability work |
| A run's critical path exceeds its wall time | the critical path is the dependency-implied **floor**; a run that executes a dependent pair concurrently finishes below it. This is the arithmetic signature of a violation, already reported by `infeasible_overlap` |
| Two medians invert at topology level but no run is inconsistent | the two statistics can be taken over different numbers of runs |
| Bounded measures show a median at the optimum for every condition | coverage, precision and scheduling deviation are bounded; report **mean plus share-at-optimum**, not the median |

---

## 6. Where each measure is computed

| File | Responsibility |
|---|---|
| `measurement/run_store_writer.py` | `run_status`, dependency violations, scheduling values at write time |
| `measurement/dependencies.py` | the dependency table, `concurrency_report()`, `scheduling_values()` — the single implementation |
| `analysis/measures.py` | per-run derived measures: coverage, precision, tax, token and cost splits |
| `score_topology_run.py` | judge criteria, capability blends, scope adjustment |
| `analysis/aggregate.py` | query- and topology-level aggregation, all scopes |
| `backfill_scheduling.py` | repair path only; calls the same `scheduling_values()` |

---

[← Documentation index](../README.md)
