# Decision Log — research and implementation choices

[← Documentation index](../README.md)

Decisions that shape what the study can claim. Each records what was decided, the
alternative, and why the alternative was rejected — so a reader can disagree with the
reasoning rather than guess at it.

---

## 1. Research design

| # | Decision | Alternative rejected | Reasoning |
|---|---|---|---|
| D1 | Topology is the manipulated variable; model is controlled and frozen | vary both | a three-way interaction between model, topology and complexity cannot be interpreted at this sample size |
| D2 | Tier chosen on **measurability**, not absolute performance | choose the best-performing tier | a tier where some conditions cannot execute the harder queries removes conditions from the comparison rather than ranking them |
| D3 | Nine conditions including two controlled pairs | a broader set of loosely related designs | the DAG/Routed and Swarm/Swarm-CA pairs isolate one design variable each, which no nine-way ranking can |
| D4 | Complexity assigned a priori from capability structure | derive bins from observed difficulty | deriving bins from results would make the complexity finding circular |
| D5 | One out-of-scope probe, reported in its own scope | fold it into the workload | declining is the *correct* outcome there; pooling it would penalise correct restraint |
| D6 | Q8 deliberately ambiguous | make every query unambiguous | ambiguity handling is an operational capability worth measuring |

---

## 2. Measurement

| # | Decision | Alternative rejected | Reasoning |
|---|---|---|---|
| D7 | A required capability returning an empty payload counts as **incomplete** | count any finished run as complete | a tool that finds no data returns an empty list rather than raising; without this rule a coordinator narrating results with nothing behind it scored a clean success |
| D8 | Extra capabilities ignored by completion | penalise over-execution in completion | over-execution is a different failure, measured by capability precision |
| D9 | A missing part of an **attempted** capability scores zero | renormalise over what is present | renormalising would score a run 5.0 for work it was told to do and skipped |
| D10 | An artifact a condition **cannot** produce is excluded and the weight renormalised | score it zero | zero is right when a coordinator was told to write something and did not; wrong when no coordinator exists to write it |
| D11 | Scope-adjusted quality inserts zero for an unattempted required capability | average only what ran | otherwise a condition that skipped work would outscore one that attempted it imperfectly |
| D12 | Cost reported from provider billing | use the pipeline estimate | cached prompt tokens were priced at the fresh rate, making the estimate wrong in both directions |
| D13 | Timing derived from tool offsets only, never wall clock | mix the two | they are captured differently; mixing them produced spurious violations |
| D14 | `run_status` and `behaviour_class` kept orthogonal | one combined status | they answer different questions and co-occur; an ungrounded answer can accompany partial execution |

---

## 3. Implementation

| # | Decision | Alternative rejected | Reasoning |
|---|---|---|---|
| D15 | Each topology is an independent module over shared capabilities | one parameterised orchestrator | a parameterised orchestrator would encode the comparison's conclusion in its own structure |
| D16 | Swarm and Swarm-CA recorded as **two topologies**, not one design iterated | patch Swarm and report one condition | plain Swarm's free-text failures are themselves the finding for that condition |
| D17 | Monolith attaches raw tools | wrap its tools as sub-agents for attribution | wrapping would make it a near-Planner-Executor and destroy the single-agent baseline |
| D18 | Dependency gating in Swarm-CA reads the same table used to *judge* every condition | give Swarm-CA its own table | using one table means the gate and the judgement cannot diverge |
| D19 | Scheduling values computed once, in a shared function | compute in both the write path and the backfill | two implementations of the same arithmetic drift |
| D20 | Generation-parameter parity asserted before every batch | trust the literals | `temperature` is written at 23 sites and `config_hash` does not cover it |
| D21 | Invalid designed runs excluded, not deleted | delete them | deletion destroys the audit trail and hides real spend |
| D22 | Per-run artefacts share a filename stem | timestamped names | a timestamp cannot be tied to a run without querying the store, and nearly caused real evidence to be deleted |

---

## 4. Abandoned designs

| Design | Why abandoned |
|---|---|
| LLM-generated topology source (`swarm_codegen.py`) | built, never wired in, deleted unused — the controller assembling agents directly is simpler and inspectable |
| Free-text capability resolution in Swarm | a resolver mapping free text onto a fixed target set is a routing table, which is Mesh's distinguishing feature, not Swarm's |
| Keyword-based behaviour classifier | rewritten three times, relabelled real runs each pass; replaced with an LLM judge and an explicit rationale field |
| Uniform cost-correction factor | the pricing bias differs in direction by tier, so no single factor applies |

---

## 5. Decisions still open

| Question | Blocking |
|---|---|
| Whether an empty-but-valid payload should count against completion at N ≥ 3 | definition is settled and applied consistently; revisit only if it changes a conclusion |
| Whether to re-read billed cost before quoting any final figure | yes — the current billed figure predates the last re-runs |
| Whether `behaviour_class` labels are written to the store before the write-up | the wrongful-decline distinction is load-bearing in the tier argument |

---

[← Documentation index](../README.md)
