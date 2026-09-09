# End-to-End Data Flow

## Full Analysis Workflow

The most complex user action — requesting a full analysis — triggers a five-stage pipeline execution across 16 steps, from user input through every agent, MCP tool, SQLite write, and Gradio render.

| Step | Actor | Action | Output |
|---|---|---|---|
| 1 | User | Types "Run Full Analysis" or clicks quick-action button | Intent: FULL PIPELINE |
| 2 | App (intent detection) | Maps query to `[predict, diagnose, simulate, recommend, email]` | Action plan displayed to user for confirmation; informational questions bypass the plan and go straight to the master agent |
| 3 | Master Orchestrator | Calls `predict_delivery_delays_tool` via MCP | Prediction JSON |
| 4 | Prediction Pipeline (MCP) | Extract → Clean → Engineer → Encode → Stage 1 → Stage 2 → Write DB + CSV | prediction CSV + DB updated (27 tables) |
| 5 | Predict Agent | Enriches each delayed row with LLM-generated `llm_insights` | `predict_summary`; enrichment rows captured by the app from the tool-output stream (master leaves `predict_rows` empty) |
| 6 | Master Orchestrator | Calls `get_delay_diagnosis` via MCP | Diagnosis JSON |
| 7 | Diagnosis MCP tool | Reads 24 summary tables from SQLite; computes daily vs historical deltas | `high_risk_patterns` + `comparison_data` |
| 8 | Diagnosis Agent | Narrates patterns; identifies critical risk combinations | `diagnosis_summary` + diagnosis rows |
| 9 | Master Orchestrator | Calls `simulate_order_delays` with user-specified scenario | Simulation JSON |
| 10 | Simulation MCP tool | Applies condition changes; reassigns severity proportionally from historical distributions | `simulations` list |
| 11 | Master Orchestrator | Calls `recommend_actions` (local tool + RAG) | Recommendation JSON |
| 12 | Recommendation Agent | Queries SQLite + ChromaDB; generates 9+ SLA-grounded actions | `recommendation_rows` |
| 13 | Master Orchestrator | Calls `fetch_delayed_orders_for_email` | Email drafts |
| 14 | Email Agent | Generates personalised severity templates for each delayed order | `email_alerts` list |
| 15 | App (post-processing) | Parses `MasterOutput`; builds DataFrames; saves CSVs; updates sidecar files | Tab state updated |
| 16 | Gradio UI | Renders all 5 tabs simultaneously | User sees structured results |

---

## Freshness Gate (between steps 2 and 3)

Before step 3, the app checks sidecar file mtimes against a 1-hour TTL:

```
predict sidecar fresh?
  YES → skip steps 3-5, proceed to step 6
  NO  → run steps 3-5

diagnose sidecar fresh?
  YES → skip steps 6-8, proceed to step 9
  NO  → run steps 6-8
```

The action plan shown in step 2 reflects this — steps the system will skip are marked explicitly so the user sees the actual execution plan before confirming.

---

## Output Files Generated

All output files are written to `supply_chain_delivery_app/output/` (gitignored) after a full pipeline run:

| File | Written by | Contents | Consumed by |
|---|---|---|---|
| `daily_delivery_delay_prediction.csv` | Prediction pipeline + Predict Agent | All orders with `predict_delay`, `predict_severity`, `llm_insights` columns | Predict tab · Simulation tool |
| `daily_delivery_delay_prediction_meta.json` | Prediction pipeline | Summary stats, severity counts, top regions/weather/partners, formatted text | App freshness check · Predict tab render |
| `simulate_delays_latest.csv` | Simulation post-processing | Simulated delayed orders with `simulate_delay_reason` and before/after conditions | Simulation tab |
| `diagnosis_meta.json` | `save_diagnosis_sidecar()` | Truncated diagnosis summary (500 chars) for freshness detection | App freshness check |
| `email_alerts.csv` | Email post-processing | Delayed orders with `email_template_name` and `email_content` | Email tab · external dispatch system |
| `delivery_predictions.db` | `database_operations_10.py` | 27 SQLite tables — predictions + 12 daily summaries + 12 historical summaries + metadata dict | All MCP tools (diagnose, simulate, recommend) |

---

## Data Dependency Graph

```
predict_delivery_delays
        │
        ├──→ [writes] daily_delivery_delay_prediction.csv
        ├──→ [writes] daily_delivery_delay_prediction_meta.json  (freshness sidecar)
        └──→ [writes] SQLite daily_* tables (27 tables)
                │
                ├──→ get_delay_diagnosis (reads daily_* + hist_* tables)
                │           └──→ [app writes] diagnosis_meta.json  (freshness sidecar)
                │
                ├──→ simulate_order_delays (reads daily_delivery_delay_prediction.csv + hist_summary tables)
                │           └──→ [writes] simulation_delivery_delays.csv (app trims/copies → simulate_delays_latest.csv)
                │
                └──→ recommend_actions (reads daily_summary tables → RAG → Recommend Agent)
                            └──→ ChromaDB (SLA policy chunks)

fetch_delayed_orders_for_email
        └──→ reads daily_delivery_delay_prediction.csv
        └──→ [writes] email_alerts.csv
```

Any step that tries to run without its upstream output will receive an explicit error from the MCP tool (`predict artifacts missing`) rather than silently returning empty data.
