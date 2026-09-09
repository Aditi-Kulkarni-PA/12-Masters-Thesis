# Master & Fallback Agent — Logic & Workflow

## Master Agent — Logic Flowchart

### 1. App Layer — Plan Confirmation Gate (before agent runs)

```perl
┌─────────────────────────────────────────────────────────────────────┐
│                        User Message                                 │
└─────────────────────────────────────────────────────────────────────┘
                               │
                  ┌────────────▼────────────┐
                  │  Pending query in       │
                  │  gr.State?              │
                  └────────────┬────────────┘
              Yes ─────────────┤─────────────── No
              │                                 │
  ┌───────────▼────────────┐       ┌────────────▼──────────────┐
  │  Confirm word?         │       │  Greeting?                │
  │  yes/proceed/sure/ok   │       │  hi/thanks/help/bye       │
  └───────────┬────────────┘       └─────────────┬─────────────┘
   Yes ───────┤──── No              Yes ─────────┤──── No
   │          │                     │            │
   │    ┌─────▼──────────┐          │     ┌──────▼──────────────────┐
   │    │ Discard pending│          │     │  _build_action_plan()   │
   │    │ treat as fresh │          │     │  regex match on query   │
   │    └─────┬──────────┘          │     └──────┬──────────────────┘
   │          │                     │       0 matches     1+ matches
   │          │                     │         │               │
   │          │                     │   ┌─────▼──────┐  ┌────▼───────────────────┐
   │          │                     │   │Clarification│  │Expand composite intents│
   │          │                     │   │   message  │  │e.g. SLA→predict+diag+  │
   │          │                     │   │  (end turn)│  │     recommend          │
   │          │                     │   └────────────┘  └────┬───────────────────┘
   │          │                     │                        │
   │          └──────────────────►  │         ┌──────────────▼──────────────────┐
   │                                │         │  Add prerequisite steps if      │
   │                                │         │  data NOT fresh:                │
   │                                │         │  - predict not fresh → prepend  │
   │                                │         │    "Run predictions first"      │
   │                                │         │  - diagnose not fresh → prepend │
   │                                │         │    "Run diagnosis first"        │
   │                                │         └─────────────┬───────────────────┘
   │                                │                       │
   │                                │         ┌─────────────▼───────────────────┐
   │                                │         │  Show numbered plan +           │
   │                                │         │  "Shall I proceed?"             │
   │                                │         │  Stash query → gr.State pending │
   │                                │         │  (end turn, wait for confirm)   │
   │                                │         └─────────────────────────────────┘
   │                                │
   └────────────────────────────────┘
                  │  (confirmed or greeting or quick-action button)
                  ▼
```

---

### 2. Agent Execution — Master Expert Orchestrator

```perl
┌─────────────────────────────────────────────────────────────────────┐
│              Master Expert Agent                                    │
│  Prompt: security_guardrails + chatbot_behavior + master_expert.md  │
│  model_settings: tool_choice="auto", output_type=MasterOutput       │
│                                                                     │
│  Build full_query:                                                  │
│  - Append file path if orders CSV uploaded                          │
│  - Append [SYSTEM: freshness tag] (FRESH / NOT FRESH)               │
└─────────────────────┬───────────────────────────────────────────────┘
                      │  calls tools SEQUENTIALLY (one at a time)
    ┌─────────────────┼─────────────────────────────────┐
    │                 │                                 │
    ▼                 ▼                                 ▼
┌──────────────────────────┐   ┌──────────────────────────────────────┐
│  predict_delivery_       │   │  diagnose_delay_patterns_tool        │
│  delays_tool             │   │  (sub-agent)                         │
│  (sub-agent)             │   │  Prompt: diagnose_delay_patterns.md  │
│  Prompt: predict_        │   │  Output: DelayDiagnosisResult        │
│  delivery_delays.md      │   │  - high_risk_patterns: list          │
│  Output:                 │   │  - comparison: list (today vs hist)  │
│  DeliveryDelayPrediction │   │  tools=[get_delay_diagnosis via MCP] │
│  - predict_summary: str  │   └──────────────────────────────────────┘
│  - delayed_orders: list  │
│  tools=[predict via MCP] │   ┌──────────────────────────────────────┐
└──────────────────────────┘   │  delay_simulations_tool              │
                               │  (sub-agent)                         │
                               │  Prompt: delay_simulation.md         │
                               │  Output: SimulationsList             │
                               │  tools=[simulate_order_delays]       │
                               └──────────────────────────────────────┘

                               ┌──────────────────────────────────────┐
                               │  recommendation_tool                 │
                               │  (sub-agent)                         │
                               │  Prompt: recommendation.md           │
                               │  Output: RecommendedActionsList      │
                               │  - min 9 actions (3 per category)    │
                               │  - quick-win / short-term /          │
                               │    long-term                         │
                               │  tools=[recommend_actions + RAG]     │
                               └──────────────────────────────────────┘

                               ┌──────────────────────────────────────┐
                               │  email_alert_tool                    │
                               │  (sub-agent)                         │
                               │  Prompt: email_alert.md              │
                               │  Output: EmailsList                  │
                               │  - EmailAlert: {email_content,       │
                               │    email_id}                         │
                               │  tools=[fetch_delayed_orders_        │
                               │         for_email]                   │
                               └──────────────────────────────────────┘
                                               │
                               ┌───────────────▼──────────────────────┐
                               │  Assemble MasterOutput (Pydantic)    │
                               │  - predict_summary (rows via stream) │
                               │  - diagnosis_summary + high_risk_    │
                               │    rows + comparison_rows            │
                               │  - simulate_summary + simulate_rows  │
                               │  - recommendation_summary            │
                               │  - email_alert_summary + email_alerts│
                               └──────────────────────────────────────┘
```

---

### 3. App Layer — Post-Processing & Output

```perl
┌─────────────────────────────────────────────────────────────────────┐
│                  App Layer (delivery_chat_app.py)                   │
│                                                                     │
│  1. Read predict CSV + sidecar JSON                                 │
│     └─ merge llm_insights per delivery_id (normalised float→int)    │
│     └─ trim to _MCP_DISPLAY_ROWS for table display                  │
│                                                                     │
│  2. Build simulate_df → save simulate_delays_latest.csv             │
│                                                                     │
│  3. Build diagnosis tables                                          │
│     └─ diagnosis_high_risk_df (todays patterns)                     │
│     └─ diagnosis_comparison_df (today vs historical)                │
│     └─ Save diagnosis_meta.json sidecar for next freshness check    │
│                                                                     │
│  4. Format email alerts                                             │
│     └─ Join with \n\n---\n\n separator                              │
│     └─ If no delayed orders → "No email alerts generated"           │
│                                                                     │
│  5. Build "Analysis complete" summary message                       │
│     └─ List which tabs have results                                 │
│     └─ Re-append _WELCOME_MSG for next interaction                  │
│                                                                     │
│  6. Yield 15-tuple to Gradio                                        │
│     (history, textbox, pending_state,                               │
│      predict_md, predict_df, predict_csv,                           │
│      simulate_md, simulate_df, simulate_csv,                        │
│      diagnosis_md, diag_hr_df, diag_comp_df,                        │
│      recommend_md, email_md, email_csv)                             │
└─────────────────────────────────────────────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
   ┌─────────────┐    ┌────────────────┐   ┌────────────────┐
   │  Chat (left)│    │  Output Tabs   │   │  File Downloads│
   │  history +  │    │  Predict       │   │  predict CSV   │
   │  status log │    │  Diagnosis     │   │  simulate CSV  │
   └─────────────┘    │  Simulation    │   │  email CSV     │
                      │  Recommend     │   └────────────────┘
                      │  Email/Alerts  │
                      └────────────────┘
```

---

## Fallback Advisor Agent — Workflow

### Purpose

The Fallback Advisor handles queries that fall outside the system's five operational domains (predict / diagnose / simulate / recommend / email). It is invoked by the Master Orchestrator when intent detection returns zero tool matches and the clarification prompt fails to resolve the query.

### When It Triggers

```
User query → _build_action_plan() → 0 tool matches
        │
        ▼
Clarification message shown ("I'm not sure which analysis to run...")
        │
        ▼
User rephrases → still 0 tool matches → Master routes to Fallback Advisor
```

### Agent Configuration

| Property | Value |
|---|---|
| Prompt file | `supply_chain_delivery_app/config/prompts/agents/fallback_advisor.md` |
| Tool | None. Under the OpenAI Agents SDK the Fallback Advisor carried the built-in `WebSearchTool`; Microsoft Agent Framework has no equivalent built-in, so the agent is constructed with no tools and answers from model knowledge alone (`core/agents.py`, `build_fallback_agent`). |
| Model | Same as Master (GPT-5.4) |
| Output type | Plain text (no Pydantic schema) |

### Responsibilities

- Accept queries about logistics, supply chain, SLA best practices, or general business questions that are adjacent to but outside the app's scope
- Answer from model knowledge. There is no retrieval tool attached under Microsoft Agent Framework
- Respond in the same chat panel — no tab output is populated
- Decline gracefully for truly out-of-scope requests (non-logistics topics) with a brief redirect

### Design Rationale

**Why a separate agent rather than a fallback branch in the Master prompt?** Keeping the Fallback Advisor as a distinct agent-as-tool means:
- The Master Orchestrator's prompt stays focused on the five operational tools without fallback logic cluttering it
- The Fallback Advisor can be given its own system prompt tuned for open-ended Q&A and web search, independent of the structured output contracts the other agents use
- No agent can reach the web. Under the OpenAI Agents SDK this was enforced by attaching `WebSearchTool` to the Fallback Advisor alone; under Microsoft Agent Framework it holds trivially, because no web tool exists in the app at all

---

## Update (2026-07-04) — Conversational Path & Single Confirmation

Two additions to the flow above:

- **`MasterOutput.chat_response`** — informational questions (detected by the app gate via interrogative opener or trailing "?") skip the action-plan builder and go straight to the master, which answers directly in `chat_response` from fresh prior results; the app displays it as the chat reply when no analysis output was produced. Fallback Advisor handoff text is displayed the same way.
- **Single confirmation** — once the user confirms the gate's plan (or clicks a quick action), the query carries a `[SYSTEM: PLAN CONFIRMED]` tag and the master executes immediately instead of presenting a second plan.

