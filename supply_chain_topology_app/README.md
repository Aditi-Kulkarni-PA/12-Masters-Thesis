# Supply Chain Last-Mile Delivery App

OpenAI Agents SDK multi-agent system for order delay prediction, root-cause
diagnosis, simulation, recommendations, and customer email alerts. A Gradio
UI orchestrates the agents and streams results in real time.

## Architecture

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
║  AGENT RUNTIME  (delivery_agents.py — OpenAI Agents SDK)             ║
║                                                                      ║
║  Master Expert Agent  (orchestrator)                                 ║
║  ├── Predict Agent      → output: PredictOutput (Pydantic)           ║
║  ├── Diagnose Agent     → output: DelayDiagnosisResult (Pydantic)    ║
║  ├── Simulate Agent     → output: SimulationOutput (Pydantic)        ║
║  ├── Recommend Agent    → output: RecommendationOutput (Pydantic)    ║
║  ├── Email Alert Agent  → output: EmailsList (Pydantic)              ║
║  ├── Format Summary Agent (replaced by python; kept for future)      ║
║  └── Fallback Advisor   → WebSearchTool                              ║
║                                                                      ║
║  All outputs validated via Pydantic v2 structured output contracts   ║
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

## Project Structure

```
supply_chain_delivery_app/
├── delivery_chat_app.py           # Gradio UI — entry point
├── delivery_agents.py             # Pydantic output models + agent definitions
├── config/
│   ├── load_config.py             # get_instruction() — reads prompt .md files verbatim
│   └── prompts/
│       ├── agents/                # 7 agent-specific markdown prompts
│       │   ├── master_expert.md
│       │   ├── predict_delivery_delays.md
│       │   ├── diagnose_delay_patterns.md
│       │   ├── delay_simulation.md
│       │   ├── recommendation.md
│       │   ├── email_alert.md
│       │   └── fallback_advisor.md
│       └── shared/                      # 4 cross-cutting prompts
│           ├── security_guardrails.md   # master layer 1
│           ├── chatbot_behavior.md      # master layer 2
│           ├── field_glossary.md        # @include'd into predict/diagnose/format prompts
│           └── format_summary.md        # format agent (currently unused)
├── tools/
│   ├── rag_knowledge.py           # ChromaDB + hybrid retrieval (cosine + keyword)
│   ├── recommend_actions.py       # SQLite reads + RAG retrieval for recommendations
│   └── email_customers.py         # Severity-based email template generation
├── helpers/
│   ├── app_utils.py               # Gradio UI helpers and state management
│   ├── post_processing.py         # Agent output post-processing
│   └── logging_utils.py           # Runtime logging setup
├── knowledge/
│   └── delivery_sla_github_ready.md  # 36-section SLA/OLA policy document (RAG source)
├── vectorstore/                   # ChromaDB persistent store (gitignored)
├── input/                         # Daily order CSVs for prediction
├── output/                        # Generated prediction, simulation, and email CSVs (gitignored)
└── log/                           # Runtime application logs
```

### Github Repository
`https://github.com/Aditi-Kulkarni-PA/supply-chain-capstone`

## Prerequisites

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
| `SC_DELIVERY_OUTPUT_DIR` | Path for generated outputs | `supply_chain_delivery_app/output` |
| `SC_MCP_ENRICH_ROWS` | Max rows for LLM enrichment | `50` |
| `SC_MCP_DISPLAY_ROWS` | Max rows to display in UI | `50` |

## Setup

```bash
# From the project root
uv sync                  # installs all dependencies from pyproject.toml lockfile

# Verify .env is configured
cat .env | grep SC_
```

## Running the App

1. Start the MCP prediction server in a separate terminal:

```bash
# From the project root
python prediction_pipeline/prediction_server.py
```

2. Launch the Gradio app:

```bash
cd supply_chain_delivery_app
python delivery_chat_app.py
```

Open `http://localhost:7860`.

The app has 5 tabs:
- **Predict** — Upload a daily CSV or use the default; runs the two-stage ML pipeline and enriches delayed orders with per-row LLM insights.
- **Diagnosis** — Root-cause analysis comparing today's delay patterns against historical summaries across 12 dimensions.
- **Simulation** — What-if delay scenarios (e.g. *"what if weather turns stormy in the East region?"*).
- **Recommendation** — SLA-grounded optimization actions in three categories (quick-win / short-term / long-term).
- **Email** — Severity-templated customer email alerts for all delayed orders.

Six quick-action buttons trigger common workflows without requiring a typed query.

## MCP Integration (Model Context Protocol)

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

## Agent Configuration (Markdown Prompts)

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

## Agents

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

## RAG Knowledge Base

The `rag_knowledge.py` tool indexes `knowledge/delivery_sla_github_ready.md`
into ChromaDB using:
- **Embeddings**: OpenAI `text-embedding-3-small` (1536 dimensions)
- **Chunking**: `MarkdownHeaderTextSplitter` (semantic boundary detection) followed by `RecursiveCharacterTextSplitter` (200-character overlap)
- **Retrieval**: Hybrid — 70% cosine similarity + 30% BM25 keyword matching
- **Cache invalidation**: Hash-based — re-indexes automatically when the source document changes

The vector store persists in `vectorstore/` (gitignored) and is rebuilt on first run or when the SLA document changes.

## Pydantic Output Models

All agent outputs are validated against Pydantic v2 models defined in
`delivery_agents.py`. Key models:

| Model | Agent | Key fields |
|---|---|---|
| `PredictOutput` | Predict | `predict_summary`, `delayed_orders` (list with `llm_insights` per row) |
| `DelayDiagnosisResult` | Diagnose | `high_risk_patterns`, `comparison`, `diagnosis_summary` |
| `SimulationOutput` | Simulate | list of `SimulationRow` with `simulate_delay_reason` |
| `RecommendationOutput` | Recommend | list of `RecommendedAction` with `category`, `sla_reference`, `supporting_data` |
| `EmailsList` | Email | list of `EmailAlert` |
| `MasterOutput` | Master | aggregates all sub-agent outputs |

## Topology Comparison Harness (`scripts/`)

A second workflow lives alongside the Gradio delivery app documented above: the
orchestration-topology comparison for the thesis. Each of the topologies under
`topologies/` (see `topologies/registry.py` — the single source of truth for which
topologies exist and how each is built) answers the same frozen query set
(`measurement/query_set_v1.xlsx` / `query_metadata` table) and every run is scored and
persisted to `data/run_store.db` via `measurement/run_store_writer.py`. Three scripts in
the repo-root `scripts/` folder drive this workflow; all three `cd` to the repo root and
pin `UV_PROJECT_ENVIRONMENT` so they always use the repo's own `.venv`, regardless of
which directory they were invoked from.

### `execute_topology.sh` — run one topology once

Executes a single topology against a single query and writes one row to the run store
(via `execute_topology.py`). This is the unit every other script builds on.

```bash
./scripts/execute_topology.sh                          # defaults: planner_executor, run_n=1
./scripts/execute_topology.sh swarm                    # named topology, run_n=1
./scripts/execute_topology.sh planner_executor 3        # topology + run_n
./scripts/execute_topology.sh --list                    # show enabled topologies + full registry
./scripts/execute_topology.sh --cache planner_executor  # allow freshness/cache reuse (NOT for measurement)
SC_QUERY_ID=Q4 ./scripts/execute_topology.sh sequential  # pick a query from the frozen set (default Q11)
```

Key behavior:
- **Measurement mode is on by default** (`NO_CACHE=1`, `SC_DEV_PATH_FALLBACK=0`) — every
  run executes independently with no cache or freshness-skip shortcuts, which is required
  for latency/cost figures to be comparable across runs (Risk Log R16, R10). `--cache`
  turns this off for exploratory use only; any run intended for the thesis must not use it.
- `TOPOLOGY` (first positional arg, default `planner_executor`) must be uncommented in the
  script's own `TOPOLOGIES` array, which is kept in sync with `topologies/registry.py` —
  this rejects a topology whose orchestration code doesn't exist yet before any API call
  is made.
- `RUN_N` (second positional arg, default `1`) is the repetition number recorded on the
  run. It must be passed positionally, not via an `SC_RUN_N` environment variable — the
  script always computes and exports `SC_RUN_N` from this argument itself, so an
  ambient/pre-set env var of the same name is silently overridden (this is why
  `run_experiment.sh` below invokes it positionally too).
- `SC_QUERY_ID` (env var, optional) selects a query from `query_metadata`; unset defaults
  to Q11 (the full workflow).

### `run_experiment.sh` — plan and run a batch

Thin wrapper around `run_experiment.py`, which plans the cross-product of
topologies × queries × repetitions and executes each combination as its own
`execute_topology.sh` subprocess (one per run — `execute_topology.py` resolves its
topology at import time, so it cannot be reused for a second topology in the same
process). Every run in the batch is stamped with a shared `batch_id` and a per-run
`execution_order`.

```bash
./scripts/run_experiment.sh                          # all topologies, all queries, N=3
./scripts/run_experiment.sh -q all -r all -n 1        # pilot: N=1 rep, everything else default
./scripts/run_experiment.sh -q 1,2,3 -r 1,2           # specific queries, specific repetitions
./scripts/run_experiment.sh -t swarm,mesh -q 6        # specific topologies + one query
./scripts/run_experiment.sh --dry-run                 # preview the plan, no API calls
./scripts/run_experiment.sh --list                    # show built topologies and frozen queries
./scripts/run_experiment.sh --no-resume               # re-run combos even if already done
```

| Option | Meaning |
|---|---|
| `-t`, `--topology` | `all` (every **built** topology per the registry) or a comma-list of names. Default `all`. |
| `-q`, `--query` | `all` (every frozen query) or a comma-list of numbers/ids, e.g. `1,2,3` or `Q1,Q2`. Brackets optional (`[1,2,3]` also accepted). Default `all`. |
| `-r`, `--reps` | `all` (expands to `run_n` 1..`--total-reps`) or a comma-list of specific `run_n` values, e.g. `1,2`. Default `all`. |
| `-n`, `--total-reps` | What `-r all` expands to. Default `3`; pass `1` for a single-pass pilot. |
| `-b`, `--batch` | Batch label recorded on every run in this invocation. Default: auto-generated UTC timestamp. |
| `--run-phase` | `run_phase` value recorded on every run. Default `pilot`. |
| `--no-resume` | Re-run combinations that already have a `success`/`partial` run (resume is ON by default). |
| `--no-shuffle` | Execute in plan order instead of a randomized execution order (randomized is the default). |
| `--dry-run` | Print the planned runs and exit; no API calls. |
| `--list` | List built topologies and frozen query ids, then exit. |

Key behavior:
- **Resume is on by default.** A `(topology, query_id, run_n)` combination that already
  has a `success` or `partial` row in `run` is skipped, so stopping the harness partway
  (Ctrl-C) and re-invoking the same command later continues rather than duplicates work.
  `--no-resume` disables this.
- **Execution order is randomized by default** (`--no-shuffle` to disable), and every run
  is stamped with the batch's `batch_id` plus its own `execution_order` — this is what
  lets a single batch be distinguished from ordinary timestamp ordering, and lets an
  interrupted batch's partial results be identified afterward.
- Each planned run is dispatched as `bash execute_topology.sh <topology> <run_n>` with
  `run_n` passed positionally (per the note above) and `SC_QUERY_ID`, `SC_BATCH_ID`,
  `SC_EXECUTION_ORDER`, `SC_RUN_PHASE` set as env vars for that subprocess only.
- **Console output stays high-level; detail goes to a file.** `execute_topology.sh`/`.py`
  print a lot per run (banners, turn text, tool/cost tables, run-validity checks) — fine
  for one manual run, unreadable across a batch of dozens. Each run's full stdout/stderr
  is captured to its own file under `log/batches/<batch_id>/`, named
  `<execution_order (3-digit)>_<topology>_<query_id>_n<run_n>.log`
  (e.g. `007_static_graph_dag_Q6_n2.log`); this process's own console output stays to one
  line per run (`[order/total] topology query run_n ... OK/FAILED (log filename)`).
  Runs with `PYTHONUNBUFFERED=1` so `tail -f` on a run's log shows it filling in live,
  not only once that run finishes.
- Writes `log/batches/<batch_id>/summary.json` when the batch finishes OR is
  interrupted — planned/succeeded/failed/remaining counts plus each run's log filename —
  useful for inspecting a stopped batch without querying the database directly.

### `delete_topology_run.sh` — remove runs from the store

Deletes rows from the run store (and their child `tool_call`/`quality_scores` rows),
sparing locked runs by default.

```bash
./scripts/delete_topology_run.sh                    # --unlocked (default)
./scripts/delete_topology_run.sh --unlocked         # every run with lock_rows = 0
./scripts/delete_topology_run.sh --all              # EVERYTHING, locked runs included
./scripts/delete_topology_run.sh --dry-run          # show what would go, delete nothing
./scripts/delete_topology_run.sh --orphan-logs      # also sweep log files no run claims
./scripts/delete_topology_run.sh --unlocked <run_id> [run_id ...]   # specific run ids only
```

Key behavior:
- **Default scope is `--unlocked`, not `--all`**, on purpose: a locked run is one whose
  numbers the thesis cites, and deleting one is an irreversible, real-API-spend mistake.
  `--all` additionally requires typing `delete <N>` (the exact count about to be deleted)
  before anything is removed.
- Always previews the damage first (run count, affected child-row counts, log-file count,
  and — for `--unlocked` — how many locked runs are being spared) before asking for any
  confirmation.
- `--dry-run` shows the same preview and exits without deleting anything.
- `--orphan-logs` additionally sweeps log files that no remaining run row references.
