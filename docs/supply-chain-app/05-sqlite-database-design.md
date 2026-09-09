# SQLite Database Design

## Overview

A single SQLite database (`prediction_pipeline/db/delivery_predictions.db`) is the shared data store between the two sub-systems. The prediction pipeline writes to it; the agent layer reads from it via the MCP tools `get_delay_diagnosis` and `simulate_order_delays`.

```
prediction_pipeline/                    supply_chain_delivery_app/
┌──────────────────────────┐            ┌──────────────────────────┐
│  daily_predict.py        │   writes   │  get_delay_diagnosis()   │
│  database_operations_10  │ ─────────► │  simulate_order_delays() │
│                          │            │  recommend_actions.py    │
│  27 tables               │ ◄───────── │  (via SQLite reads)      │
└──────────────────────────┘   reads    └──────────────────────────┘
```

## Table Inventory — 27 Tables

### Raw Prediction Tables (2)

| Table | Rows | Key Columns |
|---|---|---|
| `hist_delivery_delay_prediction` | ~20,000 (training data) | All features + `actual_delayed`, `actual_delay_hours`, `predict_delay`, `predict_severity`, `predict_severity_label` |
| `daily_delivery_delay_prediction` | ~5,000 (daily inference) | All features + `predict_delay`, `predict_severity`, `predict_severity_label` (no actuals) |

The `hist` table includes ground-truth actuals; the `daily` table has predictions only. This distinction drives which column is used in summary SQL (`actual_delayed` vs `predict_delay`).

### Summary Tables (24 = 12 types × 2 epochs)

Each summary type is created for both `hist_` and `daily_` prefixes, giving 24 summary tables total.

| # | Table suffix | Grouped by | Purpose |
|---|---|---|---|
| 1 | `summary_by_delivery_partner` | `delivery_partner` | Delay rate per logistics partner |
| 2 | `summary_by_package_type` | `package_type` | Delay rate per package category |
| 3 | `summary_by_vehicle_type` | `vehicle_type` | Delay rate per vehicle |
| 4 | `summary_by_delivery_mode` | `delivery_mode` | Delay rate per service tier |
| 5 | `summary_by_region` | `region` | Delay rate per geographic region |
| 6 | `summary_by_weather_condition` | `weather_condition` | Delay rate per weather type |
| 7 | `summary_by_distance_category` | distance bin | short (<50 km) / medium (50-200) / long (>200) |
| 8 | `summary_by_mode_weather` | `delivery_mode` + `weather_condition` | Combined risk patterns |
| 9 | `summary_by_mode_distance` | `delivery_mode` + distance bin | Combined risk patterns |
| 10 | `summary_by_weather_vehicle` | `weather_condition` + `vehicle_type` | Combined risk patterns |
| 11 | `summary_overall` | — | Aggregate KPIs: total, delayed count, delay rate, severity breakdown |
| 12 | `summary_high_risk_patterns` | — | Rows with `delay_rate >= 0.30` from tables 8, 9, 10; risk-classified |

All single-dimension and combined summaries share the same column shape:

| Column | Description |
|---|---|
| `total_deliveries` | Count of deliveries in group |
| `delayed_count` | Number delayed |
| `on_time_count` | Number on time |
| `delay_rate` | `delayed_count / total_deliveries` (4 dp) |
| `severity_short_count` | Count with severity = 1 (1–2h) |
| `severity_medium_count` | Count with severity = 2 (3–5h) |
| `severity_long_count` | Count with severity = 3 (6+h) |
| `avg_distance_km` | Mean distance in group |
| `avg_schedule_risk` | Mean schedule_risk in group |

### Metadata Dictionary (1)

`metadata_dictionary` — auto-generated at the end of every pipeline run. Describes every column in every table (table name, column name, data type, description). Used for prompt grounding and developer reference.

## Key Engineered Feature Columns (in prediction tables)

These are the columns agents and tools read to understand delay drivers:

| Column | Description |
|---|---|
| `km_per_expected_hr` | Distance / expected time — schedule tightness. Top Stage 1 feature (27.1% importance) |
| `mode_urgency` | Ordinal encoding: standard=1, two_day=2, express=3, same_day=4. Second feature (21.5%) |
| `schedule_risk` | `weather_severity × mode_urgency` (0–16) — compounding weather-urgency pressure. Third feature (14.9%) |
| `weather_severity` | Ordinal: clear=0, hot/cold=1, foggy=2, rainy=3, stormy=4 |
| `vehicle_load_strain` | `package_weight_kg / vehicle_capacity` |
| `carrier_avg_schedule` | Mean `km_per_expected_hr` for this partner across training data |

## High-Risk Pattern Classification

`summary_high_risk_patterns` unions delay rates from three combined tables and classifies risk:

| Threshold | `risk_level` |
|---|---|
| `delay_rate >= 0.50` | `critical` |
| `delay_rate >= 0.40` | `high` |
| `delay_rate >= 0.30` | `medium` |

Pattern types captured: `mode_weather`, `mode_distance`, `weather_vehicle`.

## How the Agent Layer Reads the Database

### `get_delay_diagnosis` → `DatabaseOperations.get_diagnosis_data()`

Called by the Diagnose agent via MCP. Reads 7 dimension pairs (daily vs hist) and returns:

```
{
  "overall_daily":  { total_deliveries, delayed_count, delay_rate, severity_* },
  "overall_hist":   { same shape },
  "comparison": [
    {
      "dimension": "region",
      "category": "east",
      "daily_delay_rate_pct": 38.5,
      "hist_delay_rate_pct": 31.2,
      "rate_change_pct": 7.3,   ← positive = worsening today
      ...
    }, ...
  ],
  "daily_high_risk_patterns": [...],
  "hist_high_risk_patterns":  [...]
}
```

The `rate_change_pct` field (daily − historical) is the primary signal the Diagnose agent uses to identify which dimensions are driving today's spike.

### `simulate_order_delays` → `run_simulation()`

Reads `daily_delivery_delay_prediction` to get the current delayed orders, applies `filters` to select a subset, applies `changes` to modify column values, then queries `hist_summary_by_*` tables to look up the historical severity distribution for the new conditions and reassigns severity labels proportionally.

### `recommend_actions.py`

The Recommend tool reads `daily_summary_overall` and `daily_summary_high_risk_patterns` directly via SQLite to ground recommendations in today's data before RAG retrieval.

## Write Lifecycle

```
train_predict_delay_model.ipynb (once)
    └── save_prediction_tables()
            ├── hist_delivery_delay_prediction   (training rows + actuals)
            ├── daily_delivery_delay_prediction  (test rows, predictions only)
            ├── create_summary_tables(prefix="hist_")  → 12 hist_ tables
            ├── create_summary_tables(prefix="daily_") → 12 daily_ tables
            └── create_metadata_dictionary()           → metadata_dictionary

Daily inference (predict_delivery_delays MCP tool)
    └── refresh_daily()
            ├── save_daily_predictions()                → daily_delivery_delay_prediction (replace)
            ├── create_summary_tables(prefix="daily_")  → 12 daily_ tables (rebuild)
            └── create_metadata_dictionary()            → metadata_dictionary (rebuild)
```

`hist_*` tables are written once during training and remain static. `daily_*` tables are replaced on every prediction run.
