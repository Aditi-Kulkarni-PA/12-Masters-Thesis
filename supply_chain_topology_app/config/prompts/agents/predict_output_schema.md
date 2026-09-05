**Prediction result — required output structure**

- `predict_summary` (str) — *Cross-dimensional insight paragraph written by the agent — Markdown bullets with quantitative derived-feature stats*
- `delayed_orders` (list) — *One {delivery_id, llm_insights} entry per delayed row. Must have exactly enrich_rows_cap entries, each with non-empty llm_insights.*
  - `delivery_id` (str) — *Delivery ID — must match the value from the tool's delayed_orders*
  - `llm_insights` (str, min 10 chars) — *REQUIRED — 1-2 sentence cross-functional explanation referencing at least two derived features (e.g. schedule_risk, vehicle_load_strain, km_per_expected_hr, vehicle_type). Must not be empty.*
