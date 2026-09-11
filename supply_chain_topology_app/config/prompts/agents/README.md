# agents/ — the frozen domain layer

The five capability prompts, plus `fallback_advisor.md` and their output-schema files.
Every topology's specialists receive these, byte-identical. That is why no coordinator
ever needs the domain instructions restated into its own prompt — the one exception being
Monolith, which has no sub-agents to receive them and so `@include`s the same files
directly.

| Kind | Files |
|---|---|
| Capability instructions | `predict_delivery_delays.md`, `diagnose_delay_patterns.md`, `delay_simulation.md`, `recommendation.md`, `email_alert.md` |
| Other agents | `fallback_advisor.md`, `master_expert.md` (retired — not loaded by any live topology) |
| Output schemas | `predict_output_schema.md`, `diagnose_output_schema.md`, `simulate_output_schema.md`, `recommendation_output_schema.md`, `email_output_schema.md` |

## The rule for editing anything here

**Never edit a domain prompt per topology.** These are the frozen substrate the whole
comparison rests on: topology is the manipulated variable, and the domain layer is what is
being held constant. A `diff` between two conditions' view of any file in this folder
showing a difference is a build defect, not a design choice.

The schema files mirror the `Field(description=...)` strings in `core/schemas.py`. They
were originally generated from it; the generator (`gen_schema_files.py`) is no longer in
the repository, so they are currently hand-maintained and can drift from `core/schemas.py`
without anything detecting it.

## Where the rest is documented

How these reach each condition, and what else each condition's prompt carries:

[`docs/thesis-topology-tradeoffs/topologies/prompt-modularization.md`](../../../../docs/thesis-topology-tradeoffs/topologies/prompt-modularization.md)
