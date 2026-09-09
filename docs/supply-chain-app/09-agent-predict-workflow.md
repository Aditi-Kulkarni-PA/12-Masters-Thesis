# Predict Delivery Delays Agent — Workflow & Design

## Predict Delayed Deliveries Agent — OpenAI GPT-5.4

## Flow Diagram

```perl

┌─────────────────────────────────────────────────────────────┐
│  User submits query via Gradio                              │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  delivery_chat_app.py                                       │
│  1. Append file path to query (if uploaded)                 │
│  2. Check sidecar mtime → append [SYSTEM: FRESH/NOT FRESH]  │
│  3. master_agent.run(full_query)           │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Master Agent (master_expert.md)                            │
│  Sees "predict" request → calls predict_delivery_delays_tool│
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Predict Agent (predict_delivery_delays.md)                 │
│  Calls predict_delivery_delays MCP tool (exactly once)      │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  prediction_server.py (MCP stdio)                           │
│  → DailyPredictionPipeline.get_prediction()                 │
│    ├── Stage 1: Delay RF (delayed vs on-time)               │
│    ├── Stage 2: Severity RF (Short/Medium/Long)             │
│    ├── Write predictions to SQLite summary tables           │
│    ├── Save delayed CSV → data/processed/                   │
│    ├── Save sidecar JSON (summary + formatted_stats) → disk │
│    └── Return JSON: {summary, formatted_stats,              │
│                      delayed_orders[0:10]}                  │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Predict Agent processes tool output                        │
│  ├── IGNORES summary + formatted_stats (not in schema)      │
│  ├── Fills llm_insights for each of 10 rows                 │
│  │   (cross-functional analysis referencing derived features)│
│  └── Writes predict_summary (Markdown bullets)              │
│                                                             │
│  OUTPUT: {predict_summary, delayed_orders: [{id, insights}]}│
│  (only 2 fields — entire output budget for intelligence)    │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Master Agent receives predict tool result                  │
│  ├── predict_summary → MasterOutput.predict_summary         │
│  └── delayed_orders  → captured by app from tool stream     │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  delivery_app.py processes MasterOutput                     │
│  ├── Guard: isinstance check (skip if str from weak model)  │
│  ├── Heading guard: prepend ### if missing                  │
│  ├── Read sidecar JSON from disk → formatted_stats + csv_path│
│  ├── Append formatted_stats to predict_text                 │
│  ├── Read CSV from disk → predict_df                        │
│  ├── Merge llm_insights by delivery_id into predict_df      │
│  ├── Save updated CSV                                       │
│  └── Display in Gradio (text + table + download)            │
└─────────────────────────────────────────────────────────────┘
```


### Predict Delay Fixes

| # | Fix | File | Why |
|---|---|---|---|
| 1 | Rounding `vehicle_load_strain`, `km_per_expected_hr` to 2dp | `daily_predict.py` | Raw floats had 10+ decimals, wasting tokens and confusing the LLM |
| 2 | Row cap restored to 10 | `daily_predict.py` | Limit cost/tokens during testing; controlled by `SC_MCP_ENRICH_ROWS` env var |
| 3 | Tracing disabled | `.env` | `OPENAI_AGENTS_DISABLE_TRACING=1` — 10KB trace payload limit was blocking requests |
| 4 | Schema slimmed from 15 fields to 2 per row | `delivery_agents.py` | `RowEnrichment(delivery_id, llm_insights)` — LLM doesn't copy CSV data it doesn't need |
| 5 | `llm_insights` made required with `min_length=10` | `delivery_agents.py` | Was `Optional[str]=None` — LLM would skip it silently |
| 6 | Sidecar JSON file (the key architectural fix) | `daily_predict.py`, `delivery_app.py` | Pipeline saves `summary + formatted_stats` to disk; app reads from disk. LLM never touches pass-through data |
| 7 | Removed pass-through fields from agent output models | `delivery_agents.py` | `DeliveryDelayPredictionResult` → only `predict_summary + delayed_orders`. `MasterOutput` stripped of `predict_formatted_stats + predict_csv_path` |
| 8 | Prompt rewrite — "YOUR OUTPUT HAS ONLY 2 FIELDS" | `predict_delivery_delays.md`, `master_expert.md` | Clear instructions, few-shot examples for `llm_insights`, Step 1/2 for `predict_summary` |
| 9 | Heading guard | `delivery_app.py` | Prepends `### Cross-Dimensional Delay Insights` if model omits the heading |
| 10 | String fallback guard | `delivery_app.py` | Ensures output remains valid if the model returns a plain string instead of structured output |
