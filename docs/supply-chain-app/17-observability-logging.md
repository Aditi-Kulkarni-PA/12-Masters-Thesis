# Observability & Logging

## Overview

The system has four observability surfaces: a **structured runtime log** per session, **audit sidecar files** written after each pipeline run, **RAG timing and retrieval events** logged inline, and **OpenAI agent tracing** (configurable).

```
supply_chain_delivery_app/
├── log/
│   └── delivery_chat_run_{YYYYMMDD_HHMMSS}.log   ← one file per app start
└── output/
    ├── daily_delivery_delay_prediction_meta.json  ← prediction audit sidecar
    └── diagnosis_meta.json                        ← diagnosis audit sidecar
```

---

## 1. Runtime Logger

### Setup

`setup_run_logger()` in `helpers/logging_utils.py` creates one timestamped log file per app process start:

```python
logger = logging.getLogger("supply_chain_delivery_app")
logger.setLevel(logging.INFO)

# One file per run: delivery_chat_run_20260621_143022.log
log_path = app_dir / "log" / f"delivery_chat_run_{ts}.log"
```

Format:
```
2026-06-21 14:30:22 | INFO | supply_chain_delivery_app | <message>
```

All modules in the app share the same logger (`logging.getLogger("supply_chain_delivery_app")`), so every subsystem's events appear in a single chronological file.

### What Gets Logged

#### RAG Events (structured key=value)

| Event | Key fields | When |
|---|---|---|
| `rag.collection.load.started` | — | On first `retrieve_sla_context` call |
| `rag.collection.rebuild.started` | `source=<path>` | SLA file hash changed |
| `rag.collection.rebuild.completed` | `chunks=<n>` | After indexing all chunks |
| `rag.collection.load.completed` | `duration_ms`, `count` | After collection ready |
| `rag.retrieve.started` | `input_len`, `rerank_top_n` | Each retrieval call |
| `rag.retrieve.cache_hit` | `duration_ms` | Cache hit, no embedding needed |
| `rag.retrieve.hybrid_prefilter` | `candidates=<n>` | After stage 2 scoring |
| `rag.retrieve.cross_encoder_rerank` | `returned=<n>` | After stage 3 rerank |
| `rag.retrieve.completed` | `duration_ms`, `retrieved`, `returned` | Final retrieval result |
| `rag.retrieve.cache_evicted` | — | Cache cleared at 200-entry ceiling |
| `rag.cross_encoder.load` | `model=<name>` | Cross-encoder first load |
| `rag.cross_encoder.ready` | — | Model loaded and ready |
| `rag.retrieve.no_source_file` | `path=<path>` | SLA file missing |

Example log lines:
```
2026-06-21 14:31:05 | INFO | supply_chain_delivery_app | rag.retrieve.started input_len=1842 rerank_top_n=8
2026-06-21 14:31:06 | INFO | supply_chain_delivery_app | rag.retrieve.hybrid_prefilter candidates=12
2026-06-21 14:31:06 | INFO | supply_chain_delivery_app | rag.retrieve.cross_encoder_rerank returned=8
2026-06-21 14:31:06 | INFO | supply_chain_delivery_app | rag.retrieve.completed duration_ms=743 retrieved=15 returned=8
```

#### MCP Debug Events (stderr)

The `get_delay_diagnosis` MCP tool writes debug lines to stderr (not to the app log file, since it runs in a separate child process):

```
[MCP DEBUG] get_delay_diagnosis called, db_path=.../delivery_predictions.db
[MCP DEBUG] get_delay_diagnosis result keys: ['overall_daily', 'overall_hist', 'comparison', ...]
```

These appear in the terminal where the app is launched, not in the log file.

---

## 2. Audit Sidecar Files

### Prediction Sidecar — `daily_delivery_delay_prediction_meta.json`

Written by `DailyPredictionPipeline` after every successful prediction run. Contains the full `summary` dict returned by the MCP tool:

```json
{
  "summary": {
    "total_orders": 5000,
    "total_delayed": 1823,
    "pct_delayed": 36.46,
    "severity_short": 612,
    "severity_medium": 741,
    "severity_long": 470,
    "csv_path": "...daily_delivery_delay_prediction.csv",
    "delayed_csv_path": "...daily_delivery_delay_prediction.csv",
    "top_regions": [...],
    "top_weather": [...],
    "top_partners": [...]
  },
  "formatted_stats": "### Today's Delivery Summary\n..."
}
```

**Dual purpose:**
1. Read by `post_processing.process_predict()` to render `formatted_stats` in the Predict tab
2. Used as the freshness sidecar — file mtime checked against 1-hour TTL to decide whether to re-run prediction (see `16-caching-design.md`)

### Diagnosis Sidecar — `diagnosis_meta.json`

Written by `save_diagnosis_sidecar()` in `app_utils.py` after a successful diagnosis run. Stores only the first 500 characters of the diagnosis summary:

```json
{
  "diagnosis_summary": "Today's delay rate is 36.5% vs historical 31.2%..."
}
```

**Purpose:** Freshness detection only — the truncated summary is not rendered in the UI.

---

## 3. Output Files (per-run audit trail)

All output files are written to `supply_chain_delivery_app/output/` (gitignored):

| File | Written by | Contents |
|---|---|---|
| `daily_delivery_delay_prediction.csv` | Prediction pipeline + post-processing | All orders with `predict_delay`, `predict_severity`, `llm_insights` columns |
| `daily_delivery_delay_prediction_meta.json` | Prediction pipeline | Summary stats + formatted text (prediction sidecar) |
| `diagnosis_meta.json` | `save_diagnosis_sidecar()` | Truncated diagnosis summary (diagnosis sidecar) |
| `simulate_delays_latest.csv` | Simulation post-processing | Simulated delayed orders with `simulate_delay_reason` |
| `email_alerts.csv` | Email post-processing | Delayed orders with `email_template_name`, `email_content` columns |

---

## 4. Agent Tracing

The OpenAI Agents SDK emitted trace payloads to the OpenAI platform by default, and this project disabled that via `.env`. Microsoft Agent Framework does not read this variable, so the line below is inert and retained only so an older run's `.env` still validates. Observability under MAF comes from the per-run logs and the run store, described in the sections above:

```
OPENAI_AGENTS_DISABLE_TRACING=1
```

**Why disabled:** The SDK trace payload limit is 10 KB. Prediction runs with 5,000 rows and full Pydantic output objects routinely exceed this, causing `RequestTooLarge` errors that blocked tool execution during development.

To re-enable tracing (e.g. for debugging a specific agent): remove or set `OPENAI_AGENTS_DISABLE_TRACING=0` in `.env`.

---

## 5. Log Retention

Log files accumulate in `supply_chain_delivery_app/log/` — one per app start. There is no automatic rotation or deletion. For long-running deployments, periodic manual cleanup or a log rotation policy (e.g. `logrotate`) should be applied.

The log directory is not gitignored so that log files can be inspected after a deployment; add `supply_chain_delivery_app/log/*.log` to `.gitignore` if log files should be excluded from commits.
