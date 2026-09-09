# Format Agent — Design & Output Specification

## Purpose

> **Status update (2026-07-04):** the Format Summary agent is no longer called in the main flow. All display formatting is deterministic in `helpers/post_processing.py`, and the app captures sub-agent outputs directly from the tool stream (see `docs/23` Section 14). The agent remains defined in `delivery_agents.py` (with `format_summary.md` intact) and can be re-attached to the master's tool list for future use/changes; this document describes its original design.

The Format Summary agent is an internal-only agent invoked by the Master Orchestrator **after all specialist agents have completed**. It formats the text summaries produced by each upstream agent into consistent, structured Markdown before the content is rendered in the Gradio UI chat panel. It is not accessible to end users directly and receives no user input.

The Format Agent is distinct from the other six agents in that:
- It receives **already-structured data** (agent summaries + rows from MasterOutput), not raw user queries
- It does **not call any tools** — it is a pure formatting pass
- It uses a **lightweight LLM** (GPT-4.1-mini) rather than the primary GPT-5.4 model, since formatting requires minimal reasoning
- It applies **deterministic formatting rules** rather than open-ended generation

---

## Invocation in the Pipeline

```
Master Orchestrator
    │
    ├──→ Predict Agent → predict_summary (raw)
    ├──→ Diagnosis Agent → diagnosis_summary (raw)
    ├──→ Simulation Agent → simulate_summary (raw)
    ├──→ Recommendation Agent → recommendation_summary (raw)
    └──→ Email Agent → email_alert_summary (raw)
            │
            ▼
    Format Summary Agent
            │
            ▼
    Formatted summaries → Gradio chat panel
```

The Format Agent processes each summary type in order, applying the output structure defined below. The formatted output is yielded to the Gradio UI as the final content shown in the chat history window.

---

## Output Structure per Summary Type

| Summary Type | Output Heading | Key Sections |
|---|---|---|
| `predict` | `### Delivery Delay Prediction Results` | Overview · Severity Breakdown · Top Regions/Weather/Partners · CSV note |
| `diagnosis` | `### Delay Pattern Diagnosis: Today vs Historical` | Overall KPIs · Worsening/Improving Patterns · High-Risk Combos · Root Cause |
| `simulate` | `### Delivery Delay Simulation Results` | Overview · Delay Distribution · Key Patterns · Comparison to baseline |
| `recommendation` | `### Delivery Optimization Recommendations` | Quick-Win · Short-Term · Long-Term (grouped by `category` field) |
| `email_alert` | `### Customer Email Alert Summary` | Overview · Templates Used · Sample Emails · CSV confirmation |

---

## Strict Formatting Rules

These rules are enforced in `config/prompts/agents/format_summary.md` and applied to all output types:

| Rule | Requirement |
|---|---|
| Section headings | Use `###` (H3) for all output titles — never H1 or H2 |
| Sub-section labels | Use **bold** labels within bullet lists or paragraphs |
| Number values | Use real numbers from agent output only — never invented values |
| Separators | Use `--` (double dash) — never em-dash (`—`) |
| Percentages | Round to 1 decimal place (e.g., `27.1%`, not `27.1234%`) |
| Lists | Use Markdown bullet lists (`-`) for all item enumerations |
| Tables | Do not generate new tables — reference the structured output tabs for tabular data |
| Tone | Operational, concise, factual — no conversational filler or hedging |

---

## Prompt Layer

The Format Summary agent uses a minimal instruction stack:
- **No** `security_guardrails.md` (internal agent, not user-facing)
- **No** `chatbot_behavior.md` (no user interaction)
- Only: `format_summary.md` (formatting rules + output structure per summary type)

This is the only agent where the security and behaviour layers are intentionally omitted, as explained in [`docs/15-security-guardrails-design.md`](15-security-guardrails-design.md).

---

## Model

The Format Agent uses `OPENAI_MODEL_MINI` (GPT-4.1-mini) rather than the primary `OPENAI_MODEL` (GPT-5.4). Rationale: formatting does not require deep reasoning, multi-step planning, or full context recall. Using the mini model for this pass reduces token cost and latency without any quality trade-off.

---

## Relationship to Other Docs

- [`docs/08-master-agent-flowchart.md`](08-master-agent-flowchart.md) — shows where Format Agent fits in the Master Orchestrator flow
- [`docs/14-gradio-ui-design.md`](14-gradio-ui-design.md) — shows how formatted output maps to Gradio chat panel vs. structured output tabs
- [`docs/15-security-guardrails-design.md`](15-security-guardrails-design.md) — explains why this agent has no guardrails layer
