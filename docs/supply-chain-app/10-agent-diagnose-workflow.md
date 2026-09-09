# Diagnose Delay Patterns Agent — Workflow & Design

## Diagnosys of Delay Patterns

## Flow Diagram

```perl
┌─────────────────────────────────────────────────────────────┐
│  User submits "Provide delay pattern diagnosis"             │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  delivery_app.py                                            │
│  Check sidecar file mtime:                                  │
│  ├── exists && < 1h old → [SYSTEM: FRESH]                   │
│  └── missing or > 1h   → [SYSTEM: NOT FRESH]                │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Master Agent reads system note                             │
│                                                             │
│  ┌─ FRESH ──────────────────────────────────────────┐       │
│  │  Skip predict, call diagnose directly            │       │
│  └──────────────────────────────────────────────────┘       │
│                                                             │
│  ┌─ NOT FRESH ──────────────────────────────────────┐       │
│  │  1. Call predict_delivery_delays_tool (prereq)   │       │
│  │  2. Wait for output                              │       │
│  │  3. Then call diagnose_delay_patterns_tool       │       │
│  └──────────────────────────────────────────────────┘       │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Diagnosis Agent (diagnose_delay_patterns.md)               │
│  Calls get_delay_diagnosis MCP tool (no arguments)          │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  prediction_server.py (MCP stdio)                           │
│  → DatabaseOperations.get_diagnosis_data()                  │
│    ├── Reads daily_summary_* tables (written by predict)    │
│    ├── Reads hist_summary_* tables (historical baseline)    │
│    └── Returns: {overall_daily, overall_hist, comparison,   │
│                  daily_high_risk_patterns,                  │
│                  hist_high_risk_patterns}                   │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Diagnosis Agent processes tool output                      │
│  ├── Copies high_risk_patterns + comparison                 │
│  └── Writes diagnosis_summary (Markdown: overall, worsening,│
│      improving, high-risk combos, root cause analysis)      │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Master Agent receives diagnosis tool result                │
│  ├── diagnosis_summary     → MasterOutput.diagnosis_summary │
│  ├── high_risk_patterns    → MasterOutput.diagnosis_high_risk_rows│
│  └── comparison            → MasterOutput.diagnosis_comparison_rows│
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  delivery_app.py renders Diagnosis tab in Gradio            │
│  ├── diagnosis_text (Markdown summary)                      │
│  ├── diagnosis_high_risk_df (table)                         │
│  └── diagnosis_comparison_df (table)                        │
└─────────────────────────────────────────────────────────────┘
```

### Diagnosis Fixes

| # | Fix | File | Why |
|---|---|---|---|
| 11 | Freshness check for predict prerequisite | `delivery_app.py` | Checks sidecar `mtime < 1h`; appends `[SYSTEM: FRESH]` or `[SYSTEM: NOT FRESH]` to query |
| 12 | Conditional predict-before-diagnose | `master_expert.md` | If `FRESH` → skip predict, diagnose directly. If `NOT FRESH` → run predict first as prerequisite (not error) |
