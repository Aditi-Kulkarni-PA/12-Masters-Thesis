# Supply Chain Topology App

The application package. It contains **two things that share one substrate**:

| | What | Entry point |
|---|---|---|
| **The delivery app** | a Gradio conversational UI over five delivery capabilities | `delivery_chat_app.py` |
| **The topology harness** | nine orchestration conditions, instrumented, run headlessly for the thesis | `cli/execute_experiment.py` → `cli/execute_topology.py` |

Both use the same capabilities, prompts, tools, MCP server and data. That is deliberate:
the app *is* the substrate the thesis holds constant while varying topology.

This README covers **the application and how to run it**. Harness operation, measurement
and experimental design are documented separately and referenced below rather than
repeated.

| You want… | Go to |
|---|---|
| the application: architecture, setup, agents, RAG, MCP | this file |
| running an experiment, script flags, the measures | [`../docs/thesis-topology-tradeoffs/`](../docs/thesis-topology-tradeoffs/) |
| the substrate's design documents | [`../docs/supply-chain-app/`](../docs/supply-chain-app/) |
| project-level orientation | [`../README-thesis-topology-tradeoffs.md`](../README-thesis-topology-tradeoffs.md) · [`../README-supply-chain-app.md`](../README-supply-chain-app.md) |

---

## Contents

| # | Section |
|---|---|
| 1 | [Architecture](#1-architecture) |
| 2 | [Project structure](#2-project-structure) |
| 3 | [Prerequisites](#3-prerequisites) |
| 4 | [Setup](#4-setup) |
| 5 | [Running the app](#5-running-the-app) |
| 6 | [MCP integration](#6-mcp-integration) |
| 7 | [Agent configuration](#7-agent-configuration-markdown-prompts) |
| 8 | [Agents](#8-agents) |
| 9 | [RAG knowledge base](#9-rag-knowledge-base) |
| 10 | [Pydantic output models](#10-pydantic-output-models) |
| 11 | [Running the topology harness](#11-running-the-topology-harness) |

---

## 1. Architecture

```
╔══════════════════════════════════════════════════════════════════════╗
║  PROMPT ASSEMBLY  (config/load_config.py — get_instruction())        ║
║                                                                      ║
║  agents/<agent>.md              ← full file = agent prompt (WYSIWYG) ║
║  @name lines                    ← include shared/<name>.md content   ║
║                                                                      ║
║  master_expert.md begins with two includes (highest priority first): ║
║  @security_guardrails           ← security constraints               ║
║  @chatbot_behavior              ← query routing, plan confirmation   ║
║           │                                                          ║
║           ▼  instruction string                                      ║
╚══════════════════════════════════════════════════════════════════════╝
                            │
╔══════════════════════════════════════════════════════════════════════╗
║  AGENT RUNTIME  (core/agents.py — Microsoft Agent Framework)         ║
║                                                                      ║
║  Coordinator  (the delivery app uses planner-executor)               ║
║  ├── Predict Agent      → output: PredictOutput (Pydantic)           ║
║  ├── Diagnose Agent     → output: DelayDiagnosisResult (Pydantic)    ║
║  ├── Simulate Agent     → output: SimulationOutput (Pydantic)        ║
║  ├── Recommend Agent    → output: RecommendationOutput (Pydantic)    ║
║  ├── Email Alert Agent  → output: EmailsList (Pydantic)              ║
║  ├── Format Summary Agent (replaced by python; kept for future)      ║
║  └── Fallback Advisor   → out-of-scope / generic advisory replies    ║
║                                                                      ║
║  All outputs validated via Pydantic v2 structured output contracts   ║
║  The same five agents are the substrate for all nine topologies      ║
╚══════════════════════════════════════════════════════════════════════╝
                            │
╔══════════════════════════════════════════════════════════════════════╗
║  TOOLS LAYER                                                         ║
║                                                                      ║
║  ┌───────────────────┐ ┌──────────────────────┐ ┌─────────────────┐  ║
║  │ FastMCP Client    │ │ RAG Tool             │ │ Email Tool      │  ║
║  │ (MCP protocol,    │ │ rag_knowledge.py     │ │ email_customers │  ║
║  │  stdio transport) │ │ • Cross-enc rerank   │ │ • Severity-     │  ║
║  │                   │ │ • Hybrid retrieval   │ │   based Python  │  ║
║  │ → predict         │ │   (cosine + BM25)    │ │   templates     │  ║
║  │ → diagnose        │ │ • Hash-based cache   │ │ • Writes to CSV │  ║
║  │ → simulate        │ │   invalidation       │ │                 │  ║
║  └─────────┬─────────┘ └──────────┬───────────┘ └─────────────────┘  ║
║            │                      │                                  ║
║            │            recommend_actions.py                         ║
║            │            (SQLite reads + RAG retrieval)               ║
╚════════════╪══════════════════════╪══════════════════════════════════╝
             │                      │
╔════════════╪══════════════════════╪══════════════════════════════════╗
║  KNOWLEDGE & PERSISTENCE          │                                  ║
║            │                      │                                  ║
║   prediction_server.py     ChromaDB (vectorstore/)                   ║
║   SQLite DB (27 tables)    SLA doc → text-embedding-3-small          ║
║   output/ CSVs             (1536-dim, MarkdownHeader chunking)       ║
╚══════════════════════════════════════════════════════════════════════╝
                            │
╔══════════════════════════════════════════════════════════════════════╗
║  POST-PROCESSING & UI  (helpers/ + delivery_chat_app.py)             ║
║                                                                      ║
║  post_processing.py  → formats agent outputs for Gradio tabs         ║
║  logging_utils.py    → writes timestamped logs to log/               ║
║  app_utils.py        → state management, tab routing                 ║
║                                                                      ║
║  Gradio UI  →  5 tabs + 6 quick-action buttons  →  user              ║
╚══════════════════════════════════════════════════════════════════════╝
```


[↑ Contents](#contents)

---

## 2. Project structure

**Entry points live in `cli/`; importable modules live in the package folders.** The one
exception is `delivery_chat_app.py`, which stays at the package root because it is the
application itself rather than part of the harness.

| Folder | Holds |
|---|---|
| `cli/` | the seven command-line entry points, invoked through `scripts/*.sh` |
| `topologies/` | the nine orchestration conditions and the registry |
| `measurement/` | instrumentation, the run store schema and writer, the dependency table |
| `analysis/` | aggregation and derived measures |
| `core/` · `tools/` · `helpers/` · `config/` | the shared substrate |


```
supply_chain_topology_app/
├── delivery_chat_app.py            # Gradio UI — the delivery app's entry point
│
├── cli/                            # harness entry points, one concern each
│   ├── execute_experiment.py           # plans a batch, dispatches one child per run
│   ├── execute_topology.py         # runs one topology x query x repetition
│   ├── score_topology_run.py       # LLM-judge quality scoring
│   ├── backfill_scheduling.py      # repairs timing measures on existing rows
│   ├── check_model_parity.py       # pre-flight generation-parameter gate
│   ├── report_topology_run.py      # per-run report
│   └── classify_behaviour.py       # behaviour labelling
├── topologies/                     # the nine orchestration conditions + registry.py
├── measurement/                    # instrumentation, dependencies, run-store schema/writer
├── analysis/                       # aggregate.py, measures.py, tracker figure builders
│
├── core/                           # the substrate all nine conditions hold constant
│   ├── agents.py                   # the five capability agents
│   ├── clients.py                  # model client and generation settings
│   ├── schemas.py                  # Pydantic output models incl. MasterOutput
│   ├── mcp_tools.py                # pipeline_mcp — spawns the prediction server
│   ├── tool_descriptions.py        # canonical tool names and descriptions
│   └── paths.py
├── config/
│   ├── load_config.py              # get_instruction() — reads prompt .md files verbatim
│   └── prompts/
│       ├── agents/                 # per-capability domain prompts
│       ├── coordinators/           # per-topology coordinator prompts
│       ├── shared/                 # cross-cutting partials, @include'd
│       ├── helper/ · templates/    # supporting fragments
├── tools/
│   ├── rag_knowledge.py            # ChromaDB + hybrid retrieval (cosine + keyword)
│   ├── recommend_actions.py        # SQLite reads + RAG retrieval for recommendations
│   └── email_customers.py          # severity-based email template generation
├── helpers/
│   ├── app_utils.py                # Gradio UI helpers and state management
│   ├── post_processing.py          # agent output post-processing
│   └── logging_utils.py            # run logging, run_slug()/run_batch() identity
│
├── knowledge/                      # delivery_sla_github_ready.md — RAG source
├── data/                           # run_store.db — every measured run
├── runs/                           # per-run agent artefacts (swarm instruction files)
├── vectorstore/                    # ChromaDB persistent store (gitignored)
├── input/                          # daily order CSVs for prediction
├── output/                         # generated CSVs and sidecars (gitignored)
└── log/                            # batches/<batch_id>/ console logs and traces/
```

### 2.1 GitHub repository
`https://github.com/Aditi-Kulkarni-PA/supply-chain-capstone`


[↑ Contents](#contents)

---

## 3. Prerequisites

- Python 3.11+
- The **prediction_pipeline** module (sibling folder) must have trained models and
  the SQLite database in place — the delivery app calls the FastMCP prediction
  server at runtime.
- Project-root `.env` (see `.env.example` at the project root) with:

| Variable | Purpose | Example |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API key | `sk-...` |
| `OPENAI_MODEL` | Primary LLM | `gpt-5.4` |
| `OPENAI_MODEL_MINI` | Lighter model for formatting | `gpt-4.1-mini` |
| `SC_PREDICTION_MODEL_DIR` | Path to trained models | `prediction_pipeline/models` |
| `SC_PREDICTION_DB_PATH` | Path to SQLite DB | `prediction_pipeline/db/delivery_predictions.db` |
| `SC_PREDICTION_SRC_DIR` | Path to prediction_pipeline root | `prediction_pipeline` |
| `SC_DELIVERY_OUTPUT_DIR` | Path for generated outputs | `supply_chain_topology_app/output` |
| `SC_MCP_ENRICH_ROWS` | Max rows for LLM enrichment | `50` |
| `SC_MCP_DISPLAY_ROWS` | Max rows to display in UI | `50` |


[↑ Contents](#contents)

---

## 4. Setup

```bash
# From the project root
uv sync                  # installs all dependencies from pyproject.toml lockfile

# Verify .env is configured
cat .env | grep SC_
```


[↑ Contents](#contents)

---

## 5. Running the app

One command from the repository root. The prediction server does not need to be started
separately — `pipeline_mcp` is an `MCPStdioTool`, so the app spawns it as a subprocess
and closes it on exit (see [section 6](#6-mcp-integration)).

```bash
./scripts/execute_chat_app.sh                 # start the UI
SC_NO_CACHE=1 ./scripts/execute_chat_app.sh   # no response cache, no freshness reuse
```

Open `http://localhost:7860`.

**First start is slow** — typically well over a minute. The RAG cross-encoder, the
ChromaDB vector store and the MCP `initialize` handshake (`request_timeout=120`) all load
before Gradio binds its port. Wait for Gradio's `Running on local URL:` line; opening the
browser before it appears gives a "site can't be reached" error that looks like a crash
but is not one.

**The app runs planner-executor, and only planner-executor.** That is a structural fit,
not a preference: it is a plain MAF `Agent`, so one chat message returns a finished
`MasterOutput` and `stream=True` fills the tabs as each specialist lands. The other eight
conditions cannot stand in for it —

| Conditions | Why not, in a one-message UI |
|---|---|
| sequential, static_graph_dag, static_graph_routed, dynamic_graph | plan or triage on turn 1 and execute only on turn 2, so a single message returns a plan and no analysis. Their second turn is a harness measurement contract, not a UI one |
| mesh, swarm, swarm_constrained_adaptive | finish in one call, but are custom coordinators whose `run()` has no `stream` parameter — no progress could be shown |
| monolith | streams, but attaches raw MCP tools instead of the five capability wrappers, so the tab dispatch matches nothing and every tab stays empty |

Comparing topologies is the harness's job. The app demonstrates the substrate those nine
conditions hold constant, and writes nothing to `data/run_store.db` — use
`./scripts/execute_topology.sh` for a recorded run.

The app has 5 tabs:
- **Predict** — Upload a daily CSV or use the default; runs the two-stage ML pipeline and enriches delayed orders with per-row LLM insights.
- **Diagnosis** — Root-cause analysis comparing today's delay patterns against historical summaries across 12 dimensions.
- **Simulation** — What-if delay scenarios (e.g. *"what if weather turns stormy in the East region?"*).
- **Recommendation** — SLA-grounded optimization actions in three categories (quick-win / short-term / long-term).
- **Email** — Severity-templated customer email alerts for all delayed orders.

Six quick-action buttons trigger common workflows without requiring a typed query.


[↑ Contents](#contents)

---

## 6. MCP integration

The delivery app and the ML prediction pipeline communicate via **FastMCP** over stdio transport. This decouples the agent layer from the ML code — the agents call tools without needing to import or directly execute Python ML code.

**How it works:**

```
delivery_chat_app.py (MCP client)
        │
        │  stdio transport (subprocess)
        ▼
prediction_server.py (FastMCP server)
        │
        ├── predict   → DailyPredictionPipeline.run()
        ├── diagnose  → DatabaseOperations (reads 27 SQLite tables)
        └── simulate  → simulate_delays.py
```

The OpenAI Agents SDK registers the MCP server as a tool provider. When an agent calls `predict`, `diagnose`, or `simulate`, the SDK routes the call through the MCP protocol to `prediction_server.py`, which executes the ML code and returns structured results.

**Why MCP?**
- The ML pipeline and the agent app can run in separate processes (or even separate machines).
- The prediction server can be replaced or upgraded without changing agent code.
- Tools are discoverable at runtime — the agent SDK queries the server for available tools at startup.

**Starting the server:**

```bash
# From the project root — must be running before launching the app
python prediction_pipeline/prediction_server.py
```

The server runs persistently over stdio; the delivery app spawns it as a subprocess and communicates over stdin/stdout.


[↑ Contents](#contents)

---

## 7. Agent configuration (markdown prompts)

Each agent's instructions live in a dedicated `.md` file under
`config/prompts/agents/`. Loading is WYSIWYG: `load_config.get_instruction()`
passes the full file content verbatim to the OpenAI Agents SDK `Agent()`, so
every section written in the file (Purpose, Objective, Context, Rules, few-shot
examples, Task, Expected Output, …) reaches the model. A line containing only
`@name` is an include directive, replaced with that prompt file's content —
shared reference material like `shared/field_glossary.md` is written once and
included wherever needed (predict, diagnose, format_summary).

**Master agent layering** is expressed with the same include mechanism —
`master_expert.md` begins with `@security_guardrails` and `@chatbot_behavior`
(highest priority first), so the precedence order is visible in the prompt
file itself; the loader has no special cases:

```
@security_guardrails   ← security constraints, scope restriction
@chatbot_behavior      ← query routing, action plan confirmation
(master_expert.md own content — orchestration rules)
```

`shared/format_summary.md` defines the Format Summary agent (no longer called in the main flow — display formatting is deterministic in `helpers/post_processing.py`); it previously served
(called as a sub-agent tool) to separate rendering logic from reasoning logic.

Edit any `.md` file and restart the app to change agent behaviour — no Python
changes required.


[↑ Contents](#contents)

---

## 8. Agents

| Agent | Prompt file | Tool | Purpose |
|---|---|---|---|
| Master Expert | `master_expert.md` | all sub-agents | Orchestration, freshness detection, sequential tool execution |
| Predict | `predict_delivery_delays.md` | MCP `predict` | Two-stage RF prediction + per-row LLM enrichment (`llm_insights`) |
| Diagnose | `diagnose_delay_patterns.md` | MCP `diagnose` | Reads 24 DB summary tables; daily vs historical pattern analysis |
| Simulate | `delay_simulation.md` | MCP `simulate` | What-if scenario translation and row-level enrichment |
| Recommend | `recommendation.md` | `recommend_actions` (RAG) | SLA-grounded 3-category optimization recommendations |
| Email Alert | `email_alert.md` | `fetch_delayed_orders_for_email` | Severity-templated customer email generation (Python-deterministic) |
| Format Summary | `format_summary.md` | agent-as-tool (defined; not currently called) | Replaced by deterministic Python formatting in `post_processing.py`; available for future use |
| Fallback Advisor | `fallback_advisor.md` | WebSearchTool | Handles out-of-scope queries |


[↑ Contents](#contents)

---

## 9. RAG knowledge base

The `rag_knowledge.py` tool indexes `knowledge/delivery_sla_github_ready.md`
into ChromaDB using:
- **Embeddings**: OpenAI `text-embedding-3-small` (1536 dimensions)
- **Chunking**: `MarkdownHeaderTextSplitter` (semantic boundary detection) followed by `RecursiveCharacterTextSplitter` (200-character overlap)
- **Retrieval**: Hybrid — 70% cosine similarity + 30% BM25 keyword matching
- **Cache invalidation**: Hash-based — re-indexes automatically when the source document changes

The vector store persists in `vectorstore/` (gitignored) and is rebuilt on first run or when the SLA document changes.


[↑ Contents](#contents)

---

## 10. Pydantic output models

All agent outputs are validated against Pydantic v2 models defined in
`core/schemas.py`; the agents that produce them are built in `core/agents.py`. Key models:

| Model | Agent | Key fields |
|---|---|---|
| `PredictOutput` | Predict | `predict_summary`, `delayed_orders` (list with `llm_insights` per row) |
| `DelayDiagnosisResult` | Diagnose | `high_risk_patterns`, `comparison`, `diagnosis_summary` |
| `SimulationOutput` | Simulate | list of `SimulationRow` with `simulate_delay_reason` |
| `RecommendationOutput` | Recommend | list of `RecommendedAction` with `category`, `sla_reference`, `supporting_data` |
| `EmailsList` | Email | list of `EmailAlert` |
| `MasterOutput` | Master | aggregates all sub-agent outputs |

[↑ Contents](#contents)

---

## 11. Running the topology harness

The harness is the thesis workflow, not part of the delivery app. It runs the nine
conditions headlessly against the frozen query set and persists every run to
`data/run_store.db`.

```bash
# from the repository root
grep -E '^OPENAI_MODEL=' .env                              # --model is checked against this
uv run python supply_chain_topology_app/measurement/run_store_schema.py --list-experiments
./scripts/execute_experiment.sh -e 4 --run-n 1 --dry-run   # preview, spend nothing
caffeinate -i ./scripts/execute_experiment.sh -e 4 --run-n 1   # run one repetition
./scripts/generate_metrics_report.sh                       # rebuild every metric
```

| Script | Purpose |
|---|---|
| `execute_topology.sh` | one topology, one query, once |
| `execute_experiment.sh` | plan and run a batch; parity-checked before it spends |
| `delete_topology_run.sh` | remove runs, sparing locked ones |
| `generate_metrics_report.sh` | backfill timing, rebuild aggregates, print the report |
| `execute_chat_app.sh` | launch the Gradio delivery app; records nothing |

> **@include** [`../docs/thesis-topology-tradeoffs/experiments/harness-scripts.md`](../docs/thesis-topology-tradeoffs/experiments/harness-scripts.md)
> — every flag for all four scripts, with the two behaviours that have caused mistakes
>
> **@include** [`../docs/thesis-topology-tradeoffs/experiments/experimental-design.md`](../docs/thesis-topology-tradeoffs/experiments/experimental-design.md)
> — the two-stage design, the frozen query set, validity rules
>
> **@include** [`../docs/thesis-topology-tradeoffs/architecture/execution-flow.md`](../docs/thesis-topology-tradeoffs/architecture/execution-flow.md)
> — what happens between invoking a run and reading a figure
>
> **@include** [`../docs/thesis-topology-tradeoffs/instrumentation/measure-definitions.md`](../docs/thesis-topology-tradeoffs/instrumentation/measure-definitions.md)
> — what each measure means and where it is computed

[↑ Contents](#contents)
