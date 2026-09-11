# Masters Thesis - Orchestration Topology Trade-offs in Multi-Agent Systems 
## A Controlled Study in Last-Mile Delivery Operations

>A controlled comparison of **nine multi-agent orchestration topologies** over one fixed
>application substrate. Topology is the manipulated variable; the model, prompts, tools,
>data and evaluation rubric are held constant.
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

### Aim
>To empirically characterize how orchestration topology/configuration affects the cost, latency, and output quality of a multi-agent LLM system performing last-mile delivery decision support, using a shared, frozen application substrate across all conditions.

### Objectives
A controlled comparison of **nine multi-agent orchestration topologies** over one fixed
application substrate. Topology is the manipulated variable; the model, prompts, tools,
data and evaluation rubric are held constant.

- **Build nine orchestration conditions** — Monolith, Sequential, Static-Graph DAG, Static-Graph Routed, Planner-Executor, Dynamic-Graph (Magentic-style manager), Mesh, Swarm (pure emergent), and Constrained Adaptive Swarm — using the same five domain capabilities, domain prompts, underlying model, data substrate, and evaluation query set across all conditions.
- **Implement uniform, topology-agnostic instrumentation** to capture per-run and per-tool-call token cost, wall-clock timing, concurrency and scheduling behaviour, dependency-order correctness, and capability completeness. Derived measures will include critical-path latency, orchestration tax, and execution order/batch metadata.
- **Implement an LLM-as-judge quality scoring pipeline** covering relevance, faithfulness, and safety for each capability, with a scope-adjusted score that penalizes a topology when it silently skips a capability required by the query.
- **Run each condition repeatedly (N≥3)** over the frozen, complexity-tagged query set under controlled measurement settings, with caching disabled and model/configuration pinned, to characterize run-to-run variability and support paired comparison across topologies.
- **Produce a master results table and Pareto-frontier analysis** of quality vs. cost and quality vs. latency per topology, stratified by query complexity, with paired significance testing (Wilcoxon signed-rank across the shared query set). Statistical comparisons will use the shared query set, with effect sizes and confidence intervals reported alongside p-values. Topology execution order will be randomized across repetition batches to reduce time-of-run confounding.
- **Apply and document a consistent classification protocol** for distinguishing genuine, reproducible topology behaviour from build defects before either is reported as a finding.

### In scope
- **Nine orchestration conditions**, built on Microsoft Agent Framework (MAF). MAF was selected as the common framework rather than mixing frameworks across conditions, which would confound framework-specific overhead with the orchestration comparison. MAF is also the current Microsoft-supported successor to AutoGen and incorporates its multi-agent orchestration concepts.
- **The literature review** also identifies Star/Hub-and-Spoke, Hierarchical Tree, Maker-Evaluator Loop, Blackboard/Shared Workspace, and Pub-Sub/Event-Driven as coordination patterns [19]. These were considered during scoping but excluded from the empirical comparison because of scope, structural redundancy with an already represented category, or lack of a clean fit with the supply-chain capabilities in scope. The nine conditions studied here are the selected experimental set, not a claim of exhaustiveness.
- **One domain (last-mile delivery operations) and five fixed capabilities (predict, diagnose, simulate, recommend, email)**, reusing the capstone's Kaggle-sourced delivery dataset and synthetic SLA policy document as the frozen data substrate.
- **Cost, latency, and LLM-judged output quality as the primary measured outcomes**, defining the quality-cost-latency trade-off frontier used to address RQ1. Dependency-order correctness, run completion/success rate, capability coverage, and fabrication rate are secondary explanatory reliability metrics used to interpret differences observed on the frontier, rather than additional frontier dimensions. A secondary model-tiering interaction axis and an optional payload-policy ablation are also included.

### Out of scope
- **Multi-turn conversational memory and semantic/response caching as comparison axes** — the current system has no genuine multi-turn interaction to study, and existing caching is disabled during measurement runs rather than studied as a variable. 
- **Cross-vendor model comparison** (e.g., GPT vs. GLM) as a full experimental axis — a bounded cross-vendor cost spot-check may be retained for context, but it is not part of the topology comparison or RQ1 analysis because differing tokenization makes raw token counts non-comparable across vendors. 
- **Cross-domain or cross-dataset validation** — the empirical study is deliberately limited to the last-mile delivery testbed, using the fixed dataset, policy document, capability set, and query set. Generalization to other operational domains or datasets is not tested. 
- **Topology-specific prompt optimisation or capability re-engineering** — the same shared domain instructions, capability definitions, output contract, model configuration, and data substrate are used across conditions. Topology-specific instructions are limited to what is required to implement the corresponding orchestration behaviour; performance tuning of individual conditions is outside the study. 
- **Automated topology search or learned/adaptive topology selection** — this study measures a fixed, named set of orchestration configurations. Building a selector that chooses among them is explicitly reserved as future work. 
- **Contingency-theory or dynamic-capability theoretical framing, and any organizational or strategic-management claim** — these are reserved for the future doctoral extension noted in Section 5 and are not tested in this study.
- **Scaling thresholds in larger meshes**: The capability set is fixed at five throughout. Claims in the practitioner literature about scaling thresholds in larger meshes, commonly placed near eight agents, are outside what this design can test.
- **Queueing effects**: Each query is executed as a single request. Queueing effects that require concurrent requests, including priority inversion at a shared coordinator, are outside scope.

### Conditions 

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

>**Research position.** The pilot found that under a capable LLM model, output quality is broadly comparable across topologies; while cost, tokens, latency and dependency behaviour remain strongly topology-dependent. The defensible claim is therefore narrower in one respect and stronger in another: topology primarily determines the *operational* characteristics of a multi-agent system.

[↑ Contents](#table-of-contents)

---

## 2. Relationship to the capstone

This work extends a prior capstone project. The boundary is kept explicit:

| | Capstone | Thesis |
|---|---|---|
| **What** | The supply chain delivery AI-control plane application: 5 capabilities, tools, MCP server, database, RAG store, prompts | The harness, instrumentation, 9 topologies and orchestration conditions, and evaluation built on top |
| **Role here** | The Agentic AI MAS based on planner-executor framework, fixed prompts for building the application, eval and RAGAS harness | The orchestration topology-configuration is the experimental factor, while the application substrate is held fixed across conditions. The shared substrate includes the domain prompts, tools, underlying model, data, and evaluation queries. |
| **Framework** | Open AI SDK | The substrate was ported from the OpenAI Agents SDK to Microsoft Agent Framework (MAF). The five capabilities' domain logic is unchanged; the orchestration layer is not — a single hardcoded master agent became a pluggable registry of nine independent topology modules over shared, factory-built agents and client. |
| **Documentation** | [`docs/supply-chain-app/`](docs/supply-chain-app/) | [`docs/thesis-topology-tradeoffs/`](docs/thesis-topology-tradeoffs/) |
| **README** | [`README-supply-chain-app.md`](README-supply-chain-app.md) | this file |

> **@include** [`docs/thesis-topology-tradeoffs/architecture/topology-modularization.md`](docs/thesis-topology-tradeoffs/architecture/topology-modularization.md)
> — what `delivery_agents.py` looked like before, what replaced it, and why the split landed where it did (decision D23)

[↑ Contents](#table-of-contents)

---

## 3. The nine conditions

The nine experimental conditions vary the mechanism used to **decide which capabilities execute and the order in which they execute**. The underlying capabilities, workload, model, and evaluation criteria are held constant.

|  # | Condition                      | Who/what decides execution?                                    | Scope of execution                                                                   | Execution order                                 | Context handling                                                                                                                                                 |
| -: | ------------------------------ | -------------------------------------------------------------- | ------------------------------------------------------------------------------------ | ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|  1 | **Monolith**                   | Single agent                                                   | Agent implicitly determines required capabilities (tools)                            | Single agent context                            | All reasoning, tool calls, and results remain in one continuous agent context                                                                                    |
|  2 | **Sequential**                 | Planner                                                        | Planner determines the complete task sequence                                        | Fixed sequence — one at a time                  | Each agent receives the relevant output/context from the preceding agent                                                                                         |
|  3 | **Planner–Executor**           | Planner                                                        | Planner produces a task list for the executor, handling concurrency and dependencies | Executor follows planned order                  | Planner context is converted into executor tasks; execution results are accumulated and returned through the execution flow                                      |
|  4 | **Static Graph DAG**           | Code-defined graph                                             | All nodes in the fixed graph, each time                                              | Dependency levels and graph-defined parallelism | Context is passed explicitly along graph edges; each node receives its upstream inputs rather than a continuously shared conversation                            |
|  5 | **Static Graph Routed**        | Intent router                                                  | Router selects relevant paths in the fixed graph                                     | Graph-defined order for selected paths          | Context follows the selected graph paths; each node receives the inputs/results supplied by its upstream nodes                                                   |
|  6 | **Dynamic Graph**              | Manager                                                        | Manager selects the next capability at runtime, one at each step                     | Re-decided after each step                      | Manager reconstructs a ledger/context for each turn; the accumulated ledger is included in subsequent manager prompts, increasing prompt context with turn count |
|  7 | **Mesh**                       | Peer agents                                                    | Each agent decides whether and where to hand off                                     | Peer-to-peer, on receipt                        | Accumulated peer results are threaded into the context passed to subsequent peer tasks                                                                           |
|  8 | **Swarm**                      | Seed plan + participating specialists                          | Specialists dynamically execute assigned capabilities                                | Decided by participating specialists            | Each specialist receives its assigned task/context; task context is established when the task is created rather than through a continuously shared conversation  |
|  9 | **Swarm Constrained Adaptive** | Seed plan + participating specialists + dependency rule checks | Seed plan identifies required capabilities; code enforces dependencies               | Adaptive within dependency constraints          | Each specialist receives its assigned task/context; context is passed through the shared task state as execution progresses                                      |


>Because only one design variable changes within each pair (Static Graph DAG vs Static Graph Routed, and Swarm vs Swarm Constrained Adaptive), differences in outcomes can be attributed more directly to that variable, whereas comparisons across all nine conditions involve multiple mechanisms changing simultaneously.

>Context handling differs more between these conditions than any diagram suggests, and it determines what several measures mean. Dynamic Graph rebuilds its ledger every turn, so its prompt grows with turn count; Swarm creates specialist task context from the seed plan rather than from a continuously shared conversation; Mesh threads accumulated peer results into subsequent task context. These differences are important when interpreting token consumption and latency because the same logical workload can generate substantially different amounts of contextual input.

>**@include** [`docs/thesis-topology-tradeoffs/topologies/topology-reference.md`](docs/thesis-topology-tradeoffs/topologies/topology-reference.md)
>— what is held constant, the five capabilities and their true dependencies, per-condition limits

>**@include** [`docs/thesis-topology-tradeoffs/topologies/how-they-actually-work.md`](docs/thesis-topology-tradeoffs/topologies/how-they-actually-work.md)
>— context passing per condition, framework behaviours confirmed from source, turn handling, and what each means for reading a measure

>**@include** [`docs/thesis-topology-tradeoffs/topologies/prompt-modularization.md`](docs/thesis-topology-tradeoffs/topologies/prompt-modularization.md)
>— how the same prompt text reaches all nine conditions: the `@include` mechanism, the full partial diff per topology, and the two controlled contrasts confirmed at the prompt level

[↑ Contents](#table-of-contents)

---

## 4. Execution flow

Six stages turn a planned run into a reported figure:

```
execute_experiment.py  →  execute_topology.py  →  run_store_writer.write_run()
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
| **Reliability & operational validity** | full capability completion, capability coverage, coverage @optimal, capability precision, precision @optimal, dependency violations, scheduling deviation, infeasible overlap, orchestration tax | 7.2.2 |
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

Every command is run from the repository root. Each script pins the venv to the repo and
loads `.env`, so none of them depends on the directory it was invoked from.

**Every measurement run disables caching by default.** `execute_topology.sh` hardcodes
`SC_NO_CACHE=1` — the response cache and the freshness-skip marker are both off, so every
run executes all five capabilities independently rather than reusing a prior result. This
is not a flag to remember: it is set in the script, `.env` is not allowed
to carry `SC_NO_CACHE` at all (the harness aborts if it disagrees with the script's own
value — something silently overrode it), and `execute_experiment.sh` inherits it because
every batch run goes through this same script. Cost, latency and token figures are only
comparable across topologies because none of them can shortcut work the others can't.
The only place caching is ever on is the delivery app (`execute_chat_app.sh`), which writes
nothing to the run store and is not part of any measurement.

**Before a batch — check the tier and the parity gate:**

```bash
# the tier .env supplies; --model is checked against it and the batch aborts on a mismatch
grep -E '^OPENAI_MODEL=' .env

# assert every condition uses identical generation parameters
uv run python supply_chain_topology_app/cli/check_model_parity.py

# which experiments exist, and how many runs each holds
uv run python supply_chain_topology_app/measurement/run_store_schema.py --list-experiments

# preview the plan, spend nothing. --new-experiment is needed the first time a
# (run_phase, model) pair is used; --dry-run never allocates a number.
./scripts/execute_experiment.sh --run-phase main --model gpt-5.4 --run-n 1 --new-experiment --dry-run
```

**Running:**

```bash
# one topology, one query, once — the smoke test before any batch
# ad-hoc runs: no experiment, excluded from every reported figure
./scripts/execute_topology.sh                       # planner_executor, run_n=1
./scripts/execute_topology.sh -t swarm              # a named topology
./scripts/execute_topology.sh -t mesh -q Q4         # a chosen query
./scripts/execute_topology.sh --list                # built topologies + full registry

# which experiments exist
uv run python supply_chain_topology_app/measurement/run_store_schema.py --list-experiments

# open the main experiment at the frozen tier, first repetition = 99 runs
caffeinate -i ./scripts/execute_experiment.sh --run-phase main --model gpt-5.4 --run-n 1 --new-experiment

# its later repetitions
caffeinate -i ./scripts/execute_experiment.sh -e 4 --run-n 2
caffeinate -i ./scripts/execute_experiment.sh -e 4 --run-n 3

# one query across all topologies, into a named batch, same experiment and repetition
caffeinate -i ./scripts/execute_experiment.sh -e 4 --run-n 1 -q Q8 -b fix-q8
```

Every batch names an **experiment** — one `run_phase` at one model — and a **repetition**.
The experiment number is looked up from `(run_phase, model)`, never typed, and `--model` is
checked against `OPENAI_MODEL` in `.env` before anything is spent. An experiment may take
several invocations to finish; a re-run or a resumed batch carries the same `-e` and
`--run-n`, so it stays one filterable set (`WHERE experiment_no = 4`). Pilot experiments
are 1 = nano, 2 = mini, 3 = gpt-5.4, all at `run_n = 1`.

**After a batch:**

```bash
# backfill timing, rebuild aggregates, print the report (no API calls, idempotent)
./scripts/generate_metrics_report.sh

# score runs that have no quality row yet
uv run python supply_chain_topology_app/cli/score_topology_run.py

# remove runs, sparing locked ones — always preview first
./scripts/delete_topology_run.sh --dry-run
./scripts/delete_topology_run.sh --unlocked <run_id> [run_id ...]
```

**The delivery app** (demonstration UI, writes nothing to the run store):

```bash
./scripts/execute_chat_app.sh    # http://127.0.0.1:7860
```

First start takes a while — the cross-encoder, the ChromaDB vector store and the MCP
stdio handshake all load before Gradio binds its port. Wait for the
`Running on local URL:` line rather than opening the browser early.

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
| Measure worksheets, task tracker, risk log | `../plan/Thesis_Project_Tracker_v5.xlsx` (alongside the repo root, not inside it) |
| Proposal, pilot outcome, tier-freeze justification | `thesis-proposal/` |

**Before quoting any figure:**

| Check | Why |
|---|---|
| State n | a single run is a signal, not a result |
| Name the tier | the topology quality ranking does not transfer across the full tier span |
| Name the axis | spread *across topologies* and spread *across complexity bins* are different collapses of the same scores |
| Take cost from billing | the pipeline estimate misprices cached tokens |
| Confirm the run is `SC_NO_CACHE=1` | a cached or freshness-skipped run understates cost, tokens and latency — `execute_topology.sh` sets this by default; a hand-run process might not |

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

Each is the authoritative source for its topic.

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
> — 24 decisions, abandoned designs, open questions
>
> **@include** [`docs/thesis-topology-tradeoffs/validation/known-limitations.md`](docs/thesis-topology-tradeoffs/validation/known-limitations.md)
> — interpretive cases, design limitations, open measurement gaps, provenance boundaries

[↑ Contents](#table-of-contents)
