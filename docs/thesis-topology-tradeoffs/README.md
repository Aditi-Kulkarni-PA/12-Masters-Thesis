# Thesis Documentation — Orchestration Topology Trade-offs

Documentation for the **thesis work**: the harness, the instrumentation, the experimental
design and the measures. It covers the system built *on top of* the supply-chain
application, not the application itself.

For the application that provides the substrate — the five capabilities, tools, MCP
server, database and prompts held constant across all conditions — see
[`../supply-chain-app/`](../supply-chain-app/).

---

## Where to start

| If you want to know… | Read |
|---|---|
| how a run becomes a number in the thesis | [architecture/execution-flow.md](architecture/execution-flow.md) |
| what the nine conditions are and how they differ | [topologies/topology-reference.md](topologies/topology-reference.md) |
| how a condition really behaves, and why a measure reads as it does | [topologies/how-they-actually-work.md](topologies/how-they-actually-work.md) |
| what a measure means and where it is computed | [instrumentation/measure-definitions.md](instrumentation/measure-definitions.md) |
| what is stored, and what each column means | [instrumentation/run-schema.md](instrumentation/run-schema.md) |
| how a batch is designed and run | [experiments/experimental-design.md](experiments/experimental-design.md) |
| what every script flag does | [experiments/harness-scripts.md](experiments/harness-scripts.md) |
| why a choice was made, and what was rejected | [decisions/decision-log.md](decisions/decision-log.md) |
| how to read a figure that looks wrong | [validation/known-limitations.md](validation/known-limitations.md) |
| which tables and figures the results chapter produces | [reporting/analysis-report-design-spec.md](reporting/analysis-report-design-spec.md) |

---

## Contents

### Architecture
| Document | Covers |
|---|---|
| [execution-flow.md](architecture/execution-flow.md) | the six-stage chain from planned run to reported figure; what each stage writes; where each value is derived; run identity across logs, traces and the database |

### Topologies
| Document | Covers |
|---|---|
| [topology-reference.md](topologies/topology-reference.md) | the nine conditions, what is held constant, the five capabilities and their true dependencies, the two controlled contrasts, per-condition limits |
| [how-they-actually-work.md](topologies/how-they-actually-work.md) | implementation mechanics learned from building and running: what each condition passes a specialist, framework behaviours confirmed from source, turn handling, and what each means for reading the measures |

### Instrumentation
| Document | Covers |
|---|---|
| [measure-definitions.md](instrumentation/measure-definitions.md) | every reported measure traced to its function; the status vocabulary; the three quality levels; aggregation and scope rules |
| [run-schema.md](instrumentation/run-schema.md) | the run store tables, the 55 `run` columns grouped by what they answer, lifecycle rules, integrity checks |

### Experiments
| Document | Covers |
|---|---|
| [experimental-design.md](experiments/experimental-design.md) | the two-stage design, the frozen query set, how to run a batch, validity rules |
| [harness-scripts.md](experiments/harness-scripts.md) | the four shell scripts flag by flag, and the two behaviours that have caused mistakes |

### Decisions
| Document | Covers |
|---|---|
| [decision-log.md](decisions/decision-log.md) | 22 recorded decisions with the alternative rejected, abandoned designs, and questions still open |

### Validation
| Document | Covers |
|---|---|
| [known-limitations.md](validation/known-limitations.md) | finding vs build defect vs interpretive; correct measures that read as wrong; design limitations; open measurement gaps |

### Reporting
| Document | Covers |
|---|---|
| [analysis-report-design-spec.md](reporting/analysis-report-design-spec.md) | the tables and figures the results chapter produces: what question each answers, how each is encoded, and the minimum data state at which each is honest to draw. Chart form decided once, in writing, rather than ad hoc per task |
| [sample-charts/](reporting/sample-charts/) | working reference implementations — `F17_composite_score.py` and `F18_3d_scatter_widget.html`, both running against the real run store |

---

## Conventions used throughout

| Convention | Meaning |
|---|---|
| **Finding** | the condition behaved this way; the measure is correct |
| **Build defect** | conditions were given something different, or a measure was computed wrongly |
| **Interpretive** | the measure is correct but needs context to read |
| n stated everywhere | a single run is a signal, not a result |
| Tier named on every claim | the topology ranking does not transfer across the full tier span |

---

## Related material outside this folder

| What | Where |
|---|---|
| Proposal, pilot outcome report, tier-freeze justification | `thesis-proposal/` |
| Task tracker, risk log, measure worksheets | `plan/Thesis_Project_Tracker_v5.xlsx` |
| Substrate design documentation | [`../supply-chain-app/`](../supply-chain-app/) |
| Run store | `supply_chain_topology_app/data/run_store.db` |
