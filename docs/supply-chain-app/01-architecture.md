# System Architecture

## Layered Architecture Diagram

```
╔══════════════════════════════════════════════════════════════════════╗
║  LAYER 1 — USER EXPERIENCE                                           ║
║                                                                      ║
║   Gradio Web UI  (delivery_chat_app.py)                              ║
║   ┌──────────┬───────────┬──────────┬─────────────┬───────────┐      ║
║   │ Predict  │ Diagnose  │ Simulate │ Recommend   │   Email   │      ║
║   └──────────┴───────────┴──────────┴─────────────┴───────────┘      ║
║   6 Quick-Action Buttons  │  Natural Language Chat Input             ║
╚══════════════════════════════════════════════════════════════════════╝
                            │
╔══════════════════════════════════════════════════════════════════════╗
║  LAYER 2 — ORCHESTRATION  (Microsoft Agent Framework)                        ║
║                                                                      ║
║        ┌────────────────────────────────────┐                        ║
║        │   Master Expert Agent              │                        ║
║        │   • Planning & tool execution      │                        ║
║        │   • Files Freshness detection      │                        ║
║        │   • Prerequisite chain enforcement │                        ║
║        └──┬──────┬──────┬──────┬──────┬─────┘                        ║
║           │      │      │      │      │                              ║
║        Predict Diagnose Sim  Recommend Email  Fallback  Format       ║
║        Agent   Agent   Agent  Agent  Agent   Advisor   Summary       ║
╚══════════════════════════════════════════════════════════════════════╝
                            │
╔══════════════════════════════════════════════════════════════════════╗
║  LAYER 3 — LLM  (OpenAI API)                                         ║
║                                                                      ║
║   GPT-5.4 (primary)              GPT-4.1-mini (formatting)           ║
║   • Reasoning & narration        • Summary rendering                 ║
║   • Structured outputs           • Markdown formatting               ║
║     (Pydantic v2 contracts)      • Lightweight inference             ║
╚══════════════════════════════════════════════════════════════════════╝
                            │
╔══════════════════════════════════════════════════════════════════════╗
║  LAYER 4 — TOOLS                                                     ║
║                                                                      ║
║  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐    ║
║  │ FastMCP Server   │  │  RAG Tool        │  │  Email Tool      │    ║
║  │ (stdio transport)│  │  rag_knowledge   │  │  email_customers │    ║
║  │                  │  │  • cross-enc rerank │                  │    ║
║  │ • predict        │  │  • Hybrid search │  │  • Severity      │    ║
║  │ • diagnose       │  │    (cosine+BM25) │  │    templates     │    ║
║  │ • simulate       │  │  • Chunk & embed │  │  • Deterministic │    ║
║  └────────┬─────────┘  └────────┬─────────┘  └──────────────────┘    ║
║           │                     │                                    ║
║  Two-Stage RF Pipeline    recommend_actions                          ║
║  Stage 1: Delay Y/N       (SQLite + RAG)                             ║
║  Stage 2: Severity                                                   ║
╚══════════════════════════════════════════════════════════════════════╝
                            │
╔══════════════════════════════════════════════════════════════════════╗
║  LAYER 5 — KNOWLEDGE & PERSISTENCE                                   ║
║                                                                      ║
║  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐    ║
║  │ SQLite DB        │  │ ChromaDB         │  │ File System      │    ║
║  │ 27 tables        │  │ (vectorstore/)   │  │ output/ CSVs     │    ║
║  │ • daily preds    │  │ SLA document     │  │ input/ orders    │    ║
║  │ • hist summaries │  │ 1536-dim embeds  │  │ models/ .pkl     │    ║
║  │ • aggregates     │  │ (text-embed-3-sm)│  │ knowledge/ .md   │    ║
║  └──────────────────┘  └──────────────────┘  └──────────────────┘    ║
╚══════════════════════════════════════════════════════════════════════╝
                            │
╔══════════════════════════════════════════════════════════════════════╗
║  LAYER 6 — OBSERVABILITY                                             ║
║                                                                      ║
║  Runtime logs      : supply_chain_delivery_app/log/*.log             ║
║  Prediction audit  : output/daily_delivery_delay_prediction_meta.json║
║  Diagnosis audit   : output/diagnosis_meta.json                      ║
║  Agent tracing     : OPENAI_AGENTS_DISABLE_TRACING=1 (configurable)  ║
╚══════════════════════════════════════════════════════════════════════╝
```

## Agent Inventory

| Agent | File | Tool | Responsibility |
|---|---|---|---|
| Master Expert | `master_expert.md` | all sub-agents | Orchestration, freshness detection, sequential execution |
| Predict | `predict_delivery_delays.md` | FastMCP `predict` | Two-stage RF prediction + per-row LLM enrichment |
| Diagnose | `diagnose_delay_patterns.md` | FastMCP `diagnose` | Daily vs historical pattern analysis, root cause narration |
| Simulate | `delay_simulation.md` | FastMCP `simulate` | What-if scenario translation and row enrichment |
| Recommend | `recommendation.md` | `recommend_actions` (RAG) | SLA-grounded 3-category recommendations |
| Email Alert | `email_alert.md` | `fetch_delayed_orders_for_email` | Severity-templated customer emails |
| Format Summary | `format_summary.md` | agent-as-tool (defined; not currently called) | Replaced by deterministic Python formatting in `helpers/post_processing.py`; available for future use |
| Fallback Advisor | `fallback_advisor.md` | WebSearchTool | Out-of-scope query handling |

## Detailed Agent Workflow Docs

| Agent / Component | Detailed Design Doc |
|---|---|
| ML Prediction Pipeline | [`docs/04-ml-pipeline-architecture.md`](04-ml-pipeline-architecture.md) |
| Master Agent — Logic Flowchart | [`docs/08-master-agent-flowchart.md`](08-master-agent-flowchart.md) |
| Predict Agent | [`docs/09-agent-predict-workflow.md`](09-agent-predict-workflow.md) |
| Diagnose Agent | [`docs/10-agent-diagnose-workflow.md`](10-agent-diagnose-workflow.md) |
| Simulate Agent | [`docs/11-agent-simulate-workflow.md`](11-agent-simulate-workflow.md) |
| Recommend Agent + RAG | [`docs/12-agent-recommend-workflow.md`](12-agent-recommend-workflow.md) |
| Email Alert Agent | [`docs/13-agent-email-workflow.md`](13-agent-email-workflow.md) |
| Gradio UI Design | [`docs/14-gradio-ui-design.md`](14-gradio-ui-design.md) |
| Security Guardrails | [`docs/15-security-guardrails-design.md`](15-security-guardrails-design.md) |
| Prompt Engineering | [`docs/03-prompt-evolution-log.md`](03-prompt-evolution-log.md) |
