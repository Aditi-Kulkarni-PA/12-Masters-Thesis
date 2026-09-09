# Email Alerts Agent — Workflow & Design

## Email Alerts Agent

## Flow Diagram

```perl
┌──────────────────────────────────────────────────────────┐
│              Master Orchestrator Agent                   │
│                                                          │
│  Pre-requisite check (3-tier):                           │
│  1. predict already called in this conversation → skip   │
│  2. [SYSTEM: FRESH] → skip predict, call email directly  │
│  3. [SYSTEM: NOT FRESH] + no prior predict → run predict │
└──────────────────────┬───────────────────────────────────┘
                       │ calls email_alert_tool
                       ▼
┌──────────────────────────────────────────────────────────┐
│           Email Alert Agent (sub-agent)                  │
│  Prompt: config/prompts/agents/email_alert.md            │
│  Output: EmailsList                                      │
│    └─ EmailAlert: { email_content, email_id }            │
│  tools=[fetch_delayed_orders_for_email]                  │
└──────────────────────┬───────────────────────────────────┘
                       │ calls fetch_delayed_orders_for_email()
                       ▼
┌──────────────────────────────────────────────────────────┐
│    fetch_delayed_orders_for_email() — @tool     │
│    tools/email_customers.py                              │
│                                                          │
│  1. Read output/daily_delivery_delay_prediction.csv      │
│  2. Filter delayed rows (or use all if delayed-only CSV) │
│  3. For EACH delayed order:                              │
│     ├─ _severity_key() → Long/Medium/Short               │
│     └─ _generate_email() → fill template with:           │
│        {order_id, weather, delivery_mode,                │
│         region, distance_km}                             │
│  4. 3 severity-based templates:                          │
│     ├─ Long (6+h): escalation tone, priority routing     │
│     ├─ Medium (3-5h): monitoring tone, proactive update  │
│     └─ Short (1-2h): informational, minor delay notice   │
│  5. Write back to CSV:                                   │
│     ├─ email_template_name column                        │
│     └─ email_content column                              │
│  6. Return summary string:                               │
│     ├─ template counts by severity                       │
│     ├─ template definitions                              │
│     └─ sample emails (1 per template type)               │
└──────────────────────┬───────────────────────────────────┘
                       │ returns summary string
                       ▼
┌──────────────────────────────────────────────────────────┐
│  Email Agent creates single EmailAlert with              │
│  email_content = entire tool summary                     │
│  → EmailsList returned to Master                         │
└──────────────────────┬───────────────────────────────────┘
                       │ returns to Master
                       ▼
┌──────────────────────────────────────────────────────────┐
│  Master:                                                 │
│  1. App renders emails deterministically (post_process.) │
│     → MasterOutput.email_alert_summary                   │
│  2. Sets MasterOutput.email_alerts = EmailsList          │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│  delivery_app.py (Gradio UI):                            │
│  • Renders email_alert_text from email_alerts.content    │
│    with --- separator between templates                  │
│  • Shows download link for updated CSV                   │
│    (output/daily_delivery_delay_prediction.csv)          │
└──────────────────────────────────────────────────────────┘
```

### Email Alerts — Key Aspects to Remember

| Aspect | Detail |
|---|---|
| Tool function | `fetch_delayed_orders_for_email()` in `tools/email_customers.py` — returns `str`, not a list |
| Input CSV | `output/daily_delivery_delay_prediction.csv` — the delayed-orders CSV written by the predict pipeline |
| 3 severity templates | **Long (6+h):** escalation tone, priority routing; **Medium (3–5h):** monitoring, proactive update; **Short (1–2h):** informational, minor delay |
| Template placeholders | `{order_id}`, `{weather}`, `{delivery_mode}`, `{region}`, `{distance_km}` — filled per row |
| CSV write-back | Adds 2 columns: `email_template_name` and `email_content` to the same CSV |
| Delayed-only CSV handling | If CSV has no `predict_delay` column (already filtered to delayed-only), all rows are treated as delayed |
| delivery_id float fix | `.replace(".0", "")` strips trailing `.0` from float-cast delivery IDs |
| Tool returns summary string | Template counts + template definitions + sample emails (1 per type) — **NOT individual emails** |
| Agent creates single EmailAlert | The email agent wraps the entire tool summary into **ONE `EmailAlert` object** (prompt instructs this) |
| Pydantic model | `EmailAlert: {email_content: str, email_id: str}`; `EmailsList: {content: list[EmailAlert]}` with `min_length=1` |
| UI rendering | `gr.Markdown` — templates joined with `\n\n---\n\n` separator and **To:** bolded |
| CSV download | `gr.File` widget shows the updated CSV with email columns for download |
| Pre-requisite (master prompt) | **3-tier check:** (1) predict already called → skip, (2) `[SYSTEM: FRESH]` → skip, (3) `NOT FRESH` → run predict first |
| format_summary_tool | No longer called — replaced by deterministic Python formatting in `helpers/post_processing.py` (agent remains available for future use) |
| allowed_paths | `delivery_app.py` includes `output/` in `allowed_paths` for Gradio file serving |
