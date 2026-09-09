# Orchestration Topology Trade-offs — Masters Thesis

A controlled comparison of **nine multi-agent orchestration topologies** over one fixed
application substrate. Topology is the manipulated variable; the model, prompts, tools,
data and evaluation rubric are held constant.

This README is an **index and orientation**. Detail lives in `docs/` and is referenced,
not repeated — each `@include` below points at the authoritative document for that topic.

---

## Table of Contents

| # | Section |
|---|---|
| 1 | [What this project is](#1-what-this-project-is) |
| 2 | [Relationship to the capstone](#2-relationship-to-the-capstone) |
| 3 | [The nine conditions](#3-the-nine-conditions) |
| 4 | [Execution flow](#4-execution-flow) |
| 5 | [What is measured](#5-what-is-measured) |
| 6 | [Experimental design](#6-experimental-design) |
| 7 | [Running an experiment](#7-running-an-experiment) |
| 8 | [Reading the results](#8-reading-the-results) |
| 9 | [Repository layout](#9-repository-layout) |
| 10 | [Documentation map](#10-documentation-map) |
| 11 | [Decisions and limitations](#11-decisions-and-limitations) |

---

## 1. What this project is

The thesis asks what orchestration topology actually determines in a multi-agent system.
Nine coordination designs execute the same eleven queries against the same five
capabilities, and every run is instrumented for cost, latency, tokens, scheduling
behaviour, capability coverage, dependency correctness and output quality.

| Element | Value |
|---|---|
| Conditions | 9 orchestration topologies |
| Workload | 11 frozen queries (10 substantive + 1 out-of-scope probe) |
| Framework | Microsoft Agent Framework, OpenAI backend |
| Substrate | last-mile delivery prediction application, held constant |
| Measures | 37, spanning raw, reliability and quality |

**Research position.** The pilot found that under a capable model, output quality is
broadly comparable across topologies while cost, tokens, latency and dependency
behaviour remain strongly topology-dependent. The defensible claim is therefore narrower
in one respect and stronger in another: topology primarily determines the *operational*
characteristics of a multi-agent system.

[↑ Contents](#table-of-contents)

---

## 2. Relationship to the capstone

This work extends a prior capstone project. The boundary is kept explicit:

| | Capstone | Thesis |
|---|---|---|
| **What** | the delivery-prediction application: capabilities, tools, MCP server, database, RAG store, prompts | the harness, instrumentation, topologies and evaluation built on top |
| **Role here** | the fixed substrate — held constant so topology is the only variable | the object of study |
| **Documentation** | [`docs/supply-chain-app/`](docs/supply-chain-app/) | [`docs/thesis-topology-tradeoffs/`](docs/thesis-topology-tradeoffs/) |
| **README** | [`README-supply-chain-app.md`](README-supply-chain-app.md) | this file |

The substrate was ported from the OpenAI Agents SDK to Microsoft Agent Framework; the
logic is unchanged.

[↑ Contents](#table-of-contents)

---

## 3. The nine conditions

They differ in **who decides what runs and when** — nothing else.

| # | Condition | What runs | When it runs |
|---|---|---|---|
| 1 | Monolith | one agent, implicitly | same agent, one context |
| 2 | Sequential | a planner, once | the plan, rigidly |
| 3 | Planner-Executor | a planner's task list | an executor follows it |
| 4 | Static Graph DAG | fixed in code | graph levels |
| 5 | Static Graph Routed | an intent router | the same fixed graph |
| 6 | Dynamic Graph | a manager, re-planned each turn | its ledger |
| 7 | Mesh | each capability, for its peers | each peer, on receipt |
| 8 | Swarm | seed plan, then each specialist | each specialist |
| 9 | Swarm Constrained Adaptive | seed plan names the full set | **code**, against the dependency table |

Two pairs differ in exactly one variable and carry more weight than the nine-way ranking:
**DAG vs Routed** isolates intent gating, **Swarm vs Swarm-CA** isolates code-enforced
timing.

Context passing differs more between these conditions than any diagram suggests, and it
determines what several measures mean. Dynamic Graph rebuilds its ledger every turn, so
its prompt grows with turn count; Swarm's task strings are written before any prerequisite
has run, so they cannot carry it; Mesh threads accumulated peer results into each task.

> **@include** [`docs/thesis-topology-tradeoffs/topologies/topology-reference.md`](docs/thesis-topology-tradeoffs/topologies/topology-reference.md)
> — what is held constant, the five capabilities and their true dependencies, per-condition limits
>
> **@include** [`docs/thesis-topology-tradeoffs/topologies/how-they-actually-work.md`](docs/thesis-topology-tradeoffs/topologies/how-they-actually-work.md)
> — context passing per condition, framework behaviours confirmed from source, turn handling, and what each means for reading a measure

[↑ Contents](#table-of-contents)

---

## 4. Execution flow

Six stages turn a planned run into a reported figure:

```
run_experiment.py  →  execute_topology.py  →  run_store_writer.write_run()
                                                      │
                              ┌───────────────────────┴───────────────────────┐
                              ▼                                               ▼
                   score_topology_run.py                          backfill_scheduling.py
                              └───────────────────────┬───────────────────────┘
                                                      ▼
                                            analysis/aggregate.py
                                                      ▼
                                     thesis tables · tracker · reports
```

> **@include** [`docs/thesis-topology-tradeoffs/architecture/execution-flow.md`](docs/thesis-topology-tradeoffs/architecture/execution-flow.md)
> — what each stage reads and writes, where each value is derived, how run identity ties logs, traces and rows together

[↑ Contents](#table-of-contents)

---

## 5. What is measured

37 measures across three proposal sections:

| Group | Examples | Proposal |
|---|---|---|
| **Raw** | cost, tokens by group, wall time, critical path, concurrency | 7.2.1 |
| **Reliability & operational validity** | completion, coverage, precision, dependency violations, scheduling deviation, orchestration tax | 7.2.2 |
| **Quality** | judge mean, scope-adjusted quality | 7.2.3 |

**Completion**, the measure most often misread:

> A run is complete when **every capability the query required actually ran and came back
> with data**. Extra capabilities are ignored — running more than required, or an extra
> capability returning nothing, does not make a run incomplete. Over-execution is
> measured separately by capability precision.

> **@include** [`docs/thesis-topology-tradeoffs/instrumentation/measure-definitions.md`](docs/thesis-topology-tradeoffs/instrumentation/measure-definitions.md)
> — every measure traced to its function, status vocabulary, quality blending, aggregation and scope rules
>
> **@include** [`docs/thesis-topology-tradeoffs/instrumentation/run-schema.md`](docs/thesis-topology-tradeoffs/instrumentation/run-schema.md)
> — tables, the 55 `run` columns grouped by what they answer, lifecycle rules, integrity checks

[↑ Contents](#table-of-contents)

---

## 6. Experimental design

Two stages. The model tier is selected and frozen first, because it is a controlled
variable rather than a free one; the topology comparison then runs entirely at that tier.

| Stage | What happens | Status |
|---|---|---|
| **1 — Tier freeze** | 9 × 11 at each candidate tier, N=1; select on whether every topology can be measured across the full complexity range | complete |
| **2 — Topology comparison** | 9 × 11 at the frozen tier, N ≥ 3 | pending |

> **@include** [`docs/thesis-topology-tradeoffs/experiments/experimental-design.md`](docs/thesis-topology-tradeoffs/experiments/experimental-design.md)
> — the frozen query set with complexity bins, batch mechanics, validity rules
>
> **@include** [`docs/thesis-topology-tradeoffs/experiments/harness-scripts.md`](docs/thesis-topology-tradeoffs/experiments/harness-scripts.md)
> — the four shell scripts, flag by flag

[↑ Contents](#table-of-contents)

---

## 7. Running an experiment

```bash
# check the tier before every batch — it comes from .env, not a flag
grep -E '^(MODEL|OPENAI_MODEL)=' .env

# preview the plan, spend nothing
./scripts/run_experiment.sh --dry-run

# run the full design
caffeinate -i ./scripts/run_experiment.sh

# one query across all topologies
caffeinate -i ./scripts/run_experiment.sh -q Q8 -r 3 -b fix-q8

# regenerate every metric (no API calls, idempotent)
./scripts/generate_metrics_report.sh
```

**Two behaviours that have caused mistakes:**

| Behaviour | Consequence |
|---|---|
| Resume is on by default | a combo with an existing success or partial is skipped; delete first if replacing, or the batch silently narrows |
| The tier comes from `.env` | a mis-set `.env` has produced contaminated rows; a parity check now aborts the batch on mismatch |

`caffeinate -i` prevents sleep during a long batch (macOS).

[↑ Contents](#table-of-contents)

---

## 8. Reading the results

| Output | Where |
|---|---|
| Per-run records and all measures | `supply_chain_topology_app/data/run_store.db` |
| Aggregated tables | `agg_query`, `agg_topology` — rebuilt on demand |
| Console + trace logs per run | `supply_chain_topology_app/log/batches/<batch>/` |
| Measure worksheets, task tracker, risk log | `plan/Thesis_Project_Tracker_v5.xlsx` |
| Proposal, pilot outcome, tier-freeze justification | `thesis-proposal/` |

**Before quoting any figure:**

| Check | Why |
|---|---|
| State n | a single run is a signal, not a result |
| Name the tier | the topology quality ranking does not transfer across the full tier span |
| Name the axis | spread *across topologies* and spread *across complexity bins* are different collapses of the same scores |
| Take cost from billing | the pipeline estimate misprices cached tokens |

The form of every results table and figure is decided in advance, in writing, so that
chart form is not chosen ad hoc once the data lands. Each entry states what question it
answers, how it is encoded, and the minimum data state at which it is honest to draw.

> **@include** [`docs/thesis-topology-tradeoffs/reporting/analysis-report-design-spec.md`](docs/thesis-topology-tradeoffs/reporting/analysis-report-design-spec.md)
> — the full specification, with working sample charts in `reporting/sample-charts/`

[↑ Contents](#table-of-contents)

---

## 9. Repository layout

```
0_supply_chain_thesis/
├── README-thesis-topology-tradeoffs.md   ← this file
├── README-supply-chain-app.md            ← the substrate application
│
├── docs/
│   ├── supply-chain-app/                 ← substrate design docs (01–23)
│   └── thesis-topology-tradeoffs/        ← thesis documentation
│       ├── architecture/ · topologies/
│       ├── instrumentation/ · experiments/
│       ├── decisions/ · validation/
│       └── reporting/                    ← results tables, figures, sample charts
│
├── supply_chain_topology_app/
│   ├── cli/                              ← the seven entry points
│   ├── delivery_chat_app.py              ← the Gradio app (not the harness)
│   ├── topologies/                       ← the nine conditions
│   ├── measurement/                      ← instrumentation and the run store
│   ├── analysis/                         ← aggregation and derived measures
│   ├── core/ · tools/ · config/          ← the shared substrate
│   ├── data/run_store.db                 ← the measurement
│   └── log/batches/<batch>/              ← console logs + traces/
│
├── scripts/                              ← run, score, report
├── evals/                                ← judge and parity reference
├── prediction_pipeline/                  ← ML substrate (MCP server)
└── tests/
```

[↑ Contents](#table-of-contents)

---

## 10. Documentation map

Nothing below is repeated in this README; each is the authoritative source for its topic.

| Document | Answers |
|---|---|
| [`docs/thesis-topology-tradeoffs/README.md`](docs/thesis-topology-tradeoffs/README.md) | index for the thesis documentation |
| [architecture/execution-flow.md](docs/thesis-topology-tradeoffs/architecture/execution-flow.md) | how a run becomes a number |
| [topologies/topology-reference.md](docs/thesis-topology-tradeoffs/topologies/topology-reference.md) | what the nine conditions are |
| [topologies/how-they-actually-work.md](docs/thesis-topology-tradeoffs/topologies/how-they-actually-work.md) | how each one really behaves, and why its measures read as they do |
| [instrumentation/measure-definitions.md](docs/thesis-topology-tradeoffs/instrumentation/measure-definitions.md) | what each measure means |
| [instrumentation/run-schema.md](docs/thesis-topology-tradeoffs/instrumentation/run-schema.md) | what is stored |
| [experiments/experimental-design.md](docs/thesis-topology-tradeoffs/experiments/experimental-design.md) | how the study is designed and run |
| [experiments/harness-scripts.md](docs/thesis-topology-tradeoffs/experiments/harness-scripts.md) | every script flag |
| [decisions/decision-log.md](docs/thesis-topology-tradeoffs/decisions/decision-log.md) | why each choice was made |
| [validation/known-limitations.md](docs/thesis-topology-tradeoffs/validation/known-limitations.md) | how to read a figure that looks wrong |
| [reporting/analysis-report-design-spec.md](docs/thesis-topology-tradeoffs/reporting/analysis-report-design-spec.md) | which tables and figures the results chapter produces |
| [`docs/supply-chain-app/`](docs/supply-chain-app/) | the substrate: architecture, MCP server, database, RAG, agent workflows |

[↑ Contents](#table-of-contents)

---

## 11. Decisions and limitations

Every design and measurement choice is recorded with the alternative that was rejected,
and every limitation states what it does and does not affect.

| Classification | Meaning |
|---|---|
| **Finding** | the condition behaved this way; the measure is correct |
| **Build defect** | conditions were given something different, or a measure was computed wrongly |
| **Interpretive** | the measure is correct but needs context to read |

Three measures read as wrong and are not: Monolith's `0.000` token shares, a critical
path exceeding wall time, and bounded measures sitting at the optimum for most
conditions. Each is explained in the limitations document.

> **@include** [`docs/thesis-topology-tradeoffs/decisions/decision-log.md`](docs/thesis-topology-tradeoffs/decisions/decision-log.md)
> — 22 decisions, abandoned designs, open questions
>
> **@include** [`docs/thesis-topology-tradeoffs/validation/known-limitations.md`](docs/thesis-topology-tradeoffs/validation/known-limitations.md)
> — interpretive cases, design limitations, open measurement gaps, provenance boundaries

[↑ Contents](#table-of-contents)
