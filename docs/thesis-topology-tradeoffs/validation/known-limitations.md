# Known Limitations and How to Read Them

[← Documentation index](../README.md)

Every limitation that affects how a figure should be read. Each states what is affected,
what is not, and whether it is a defect or a property of the design.

---

## 1. Classification

Three categories, kept separate throughout:

| Category | Meaning | Treatment |
|---|---|---|
| **Finding** | the condition behaved this way; the measure is correct | reported as a result |
| **Build defect** | conditions were given something different, or a measure was computed wrongly | fixed, and the fix logged |
| **Interpretive** | the measure is correct but reads as wrong without context | documented, not changed |

> The distinguishing question: *were the conditions told something different?* If yes, it
> is a build defect. If they were told the same thing and behaved differently, it is a
> finding.

---

## 2. Interpretive — correct measures that read as wrong

### Monolith's token shares are 0.000 and its critical path is near zero

Monolith is one agent holding the whole task, attaching raw tools rather than wrapping
them as sub-agents. Its tool calls are genuinely fast and all planning and synthesis
happen inside the coordinator.

**These values are correct.** They report that Monolith's work is *not separable* into
coordination and execution, because in this condition it is not. Its coordinator time
**is** its capability work.

Instrumenting it to attribute time per capability would require wrapping its tools as
sub-agents — which would convert it into something close to Planner-Executor and destroy
the single-agent baseline the comparison depends on.

*The one figure warranting caution:* its dependency violation rate, since violation
detection reads tool offsets that carry little resolution here.

### Critical path can exceed wall time

The critical path is the **floor the dependency graph implies**, not an observed
duration. A run that executes a dependent pair concurrently — that is, breaks dependency
order — finishes below that floor.

So `critical_path > wall_time` is the arithmetic signature of a dependency violation, and
is already reported by `infeasible_overlap` and by a scheduling ratio above 1.0. It is
not a clock defect.

### Medians can invert without any run being inconsistent

Two medians may be taken over different numbers of runs — a condition may have a wall
time for 11 runs and a critical path for 10. A median-level inversion does not imply that
any individual run is internally impossible. Check per-run before concluding.

### Bounded measures look identical across conditions

Coverage, precision and scheduling deviation are bounded, and most conditions sit at the
optimum. The median therefore returns the optimum for nearly everything and discriminates
poorly. Report **mean plus share-at-optimum** instead.

---

## 3. Design limitations

### Cost is unreliable from the pipeline

Stored cost is token count × list price. Cached prompt tokens were priced at the fresh
input rate, so the estimate is wrong in both directions and by different amounts per
tier. Instrumentation to capture cached-token counts has been added, but historical runs
cannot be corrected because the counts were never recorded.

**Take cost from provider billing.** Use the stored figure only for within-tier ratios
between conditions, where the same method applies to every condition.

### Equivalence is not established

Quality differences between conditions at the frozen tier are small. Showing that they
represent *equivalence* rather than unmeasured variation requires repetitions and a test
against a margin specified in advance. The operational differences — factors of two to
six — are large enough to survive that scrutiny; the quality claim is not.

### Complexity results are hypotheses

The low bin holds one query and the others three, with one run per cell in the pilot.
Apparent dips are individual queries, not distributions. Recorded as hypotheses for the
main experiment to test.

### Two counts that are not comparable

`MAX_WAVES` (Swarm) and Mesh's message-hop budget are different units. They bound
different things and must never be compared directly.

---

## 4. Measurement gaps still open

| Gap | Effect | Status |
|---|---|---|
| `config_hash` covers model and prompts only | cannot detect generation-parameter or topology-source drift | mitigated by `check_model_parity.py` as a pre-flight gate |
| A batch abort writes placeholder rows | a run that never reached the model is recorded as a failure | `started_at` now NULL for those; the harness still writes the row |
| `behaviour_class` unpopulated | the wrongful-decline vs clarification-request distinction rests on a reviewed classification, not stored data | classifier exists; labels not yet written |
| Extra-capability errors treated asymmetrically | an extra capability that *errors* downgrades a run, while one returning *empty* does not | no run currently affected; revisit at N ≥ 3 |

---

## 5. Provenance boundaries

| Boundary | Rule |
|---|---|
| Pilot vs main experiment | different N and, for some queries, different turn-2 text. Never pool them |
| Pre- and post-harness-change runs | a change to what a condition receives invalidates comparison with earlier runs |
| Tier | every claim names the tier it was measured at. The topology quality ranking does **not** transfer across the full tier span |

---

[← Documentation index](../README.md)
