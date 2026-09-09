# Prompt Engineering Evolution Log

**Project:** AI-Powered Supply Chain Last-Mile Delivery Optimization  
**Author:** Aditi Kulkarni  
**Purpose:** Documents the iterative evolution of prompt design across development, documenting engineering decisions and lessons learned.

---

## Overview

The prompting strategy for this system evolved through three distinct phases. Each phase was driven by specific failure modes observed during development — from overly verbose, tangled instructions to a clean layered architecture with precise, few-shot-grounded per-record enrichment.

---

## Summary Table

| Phase | Change | Problem Solved | Files Affected |
|---|---|---|---|
| 1 → 2 | Extracted security, behaviour, formatting into shared files | Maintenance burden, security deprioritisation, formatting drift | All prompt files, `load_config.py` |
| 2 → 3.1 | Added `llm_insights` enrichment instruction | Per-row explanations missing entirely | `predict_delivery_delays.md` |
| 3.1 → 3.2 | Added derived feature glossary | Generic, non-specific insights | `predict_delivery_delays.md` |
| 3.2 → 3.3 | Narrowed output to 2-field schema | Parsing errors, missed rows, extra fields | `predict_delivery_delays.md` |
| 3.3 → 3.4 | Added 2 few-shot examples | Near-identical insights across rows | `predict_delivery_delays.md` |
| 3.4 → 3.5 | Enforced ≥2 feature rule + `predict_summary` template | Single-dimension reasoning, weak summaries | `predict_delivery_delays.md` |
| 4.1 | Pydantic split: deterministic structure + LLM prose only | Short, unstructured diagnosis summaries | `diagnose_delay_patterns.md` |
| 4.2 | Field glossary for derived columns | Root cause missing; surface-level observations only | `diagnose_delay_patterns.md` |
| 4.3 | Mandatory section ordering + formatting rules | Inconsistent summary layout between runs | `diagnose_delay_patterns.md` |
| 5.1 | Tool returns dimension-level + combo-level data | Generic recommendations with no specifics | `recommendation.md`, tool |
| 5.2 | RAG-retrieved SLA context appended to tool output | Recommendations ungrounded in SLA/OLA | `recommendation.md`, tool |
| 5.3 | Three-category structure (quick-win/short/long-term) | No temporal prioritisation | `recommendation.md` |
| 6.1 | Email generation moved to tool (deterministic Python) | Format inconsistency, template invention, coverage gaps | `email_alert.md`, tool |
| 6.2 | Negative constraints: no individual generation | Agent re-generating emails on top of tool output | `email_alert.md` |
| 7.1 | Valid values list for filter/change parameters | Tool errors from invalid filter values | `delay_simulation.md` |
| 7.2 | Row parity rule ("same number of rows as tool") | Partial enrichment (5-10 rows out of 50+) | `delay_simulation.md` |
| 7.3 | Pydantic fields enforce concise comparative reason format | Free-form verbosity, inconsistent reason length | `delay_simulation.md` |
| 8.1 | WYSIWYG loader — prompt files passed to agents verbatim; master's duplicated interaction rules replaced with a pointer to the shared behaviour layer | Sections outside Role/Goal/Backstory/Task silently dropped (predict agent saw 28% of its prompt); same rules fed to the master twice | All prompt files, `load_config.py` |
| 8.2 | Testing-driven fixes: capped sample transcription (full results read from CSV on disk); synonym mapping to valid enums + tool-error passthrough; `chat_response` conversational contract + PLAN CONFIRMED tag; bold-values + plain-language summary rules | 241-row transcription collapsed to empty output; "severe weather" → invalid value reported as "ran, no changes"; questions forced into action plans + double confirmation; key numbers didn't stand out | `delay_simulation.md`, `master_expert.md`, `chatbot_behavior.md`, `predict_delivery_delays.md`, `diagnose_delay_patterns.md` |

---

## Key Lessons

1. **Separate concerns explicitly.** Mixing security, formatting, and domain reasoning in one prompt makes all three harder to control.

2. **Schema contracts beat natural-language output instructions.** Saying "output only these 2 fields" with a JSON template eliminated an entire class of parsing errors that verbose instructions did not.

3. **Few-shot examples must span extremes.** One example teaches a template; two opposing examples teach the reasoning pattern.

4. **Name your engineered features explicitly.** LLMs will not spontaneously reference `schedule_risk` if it isn't named. Once named and explained, the model uses it consistently.

5. **Iteration on a single agent's behaviour is faster when that agent's prompt is isolated.** The modular architecture (Iteration 2) made the Phase 3–7 iteration loop significantly faster.

6. **Move deterministic work to code, not prompts.** Email template selection and generation, database reads, and structured data extraction belong in Python tools — not in prompt instructions. The LLM adds value on interpretation, synthesis, and prose; it is unreliable as a templating engine at scale.

7. **Coverage requirements need concrete, checkable language.** *"Enrich every row"* is weaker than *"return the same number of rows as the tool does"* — the latter gives the model a verifiable condition rather than a vague directive. Iteration 8 added the counterpart lesson: the condition must also be *achievable* — at 241 rows, structured transcription silently collapsed to nothing. Large row sets now flow through files on disk, with the LLM enriching only a capped sample.

8. **RAG-grounded recommendations outperform prompt-grounded recommendations.** Injecting retrieved SLA sections into the tool output at call time produces more specific, citeable recommendations than pre-loading SLA content statically into the prompt — because the retrieval is query-specific to what's actually failing today.

---

## Iteration 1 — Monolithic Prompts (Initial Prototype)

### What Was Built

Each agent had a single, large system prompt containing all rules in one block:
- Behavioural rules (tone, refusals, scope)
- Security constraints (what to reject, injection defence)
- Input/output format specs
- Output formatting rules (headings, bullet styles)
- Domain context

### Problems Encountered

**Maintenance:** Changing a shared rule (e.g., output style) required editing every agent file independently. Rules diverged across agents over time.

**Debuggability:** When an agent misbehaved, it was hard to isolate whether the cause was a security rule conflict, a formatting instruction, or a domain reasoning error — they were all mixed together.

**Context bloat:** Repetitive boilerplate (security, tone, response style) consumed context budget on every call, leaving less room for domain-specific reasoning.

**Prompt injection risk:** Security guardrails were buried mid-prompt with no prioritisation signal, making them easy for the model to deprioritise when later instructions created conflicts.

---

## Iteration 2 — Layered Modular Architecture

### What Changed

Prompts were broken into three layers, assembled programmatically in `supply_chain_delivery_app/config/load_config.py` via the `build_instruction()` function:

```
Layer 0 (security_guardrails.md)    ← HIGHEST PRIORITY, prepended first
Layer 1 (chatbot_behavior.md)       ← Interaction rules, confirmation flow
Layer 2 (agent-specific file)       ← Domain reasoning for each agent
```

Additionally, `shared/format_summary.md` was extracted as a standalone prompt for the dedicated formatting agent, separating rendering logic from reasoning logic entirely.

### Shared Prompts Created

| File | Purpose |
|---|---|
| `shared/security_guardrails.md` | Scope restriction, prompt injection defence, PII rules, output safety — applied first, highest priority |
| `shared/chatbot_behavior.md` | Query-to-tool mapping table, action plan confirmation protocol, clarification rules, multi-intent handling |
| `shared/format_summary.md` | All Markdown formatting rules per summary type (predict, diagnosis, simulate, recommendation, email_alert) |

### Agent-Specific Prompts Created

| File | Responsibility |
|---|---|
| `agents/master_expert.md` | Orchestration: sequential tool execution, freshness detection, data-sharing between agents |
| `agents/predict_delivery_delays.md` | ML pipeline invocation + per-row LLM enrichment |
| `agents/diagnose_delay_patterns.md` | Pattern analysis, worsening/improving dimensions, root cause narration |
| `agents/delay_simulation.md` | What-if scenario enrichment, valid input validation |
| `agents/recommendation.md` | SLA-grounded action generation in 3 categories (quick-win / short-term / long-term) |
| `agents/email_alert.md` | Severity-templated customer email generation |
| `agents/fallback_advisor.md` | Out-of-scope query handling |

### Key Design Decisions

**Security first, explicitly stated:** `security_guardrails.md` opens with the heading *"HIGHEST PRIORITY — apply before all other instructions"* to signal priority ordering to the model.

**Separation of rendering from reasoning:** `format_summary.md` handles all Markdown structure (headings, bullet format, percentage denominators, separator characters). Individual agents do not contain formatting rules — they produce structured data; the formatter agent renders it. This eliminated formatting inconsistencies that occurred when agents tried to both reason and render simultaneously.

**Action plan confirmation:** Extracted into `chatbot_behavior.md` with a lookup table (trigger keywords → tool name) and explicit rules for when to skip confirmation (single unambiguous intent, Quick Action button click). Previously each agent had its own ad-hoc confirmation language.

### Results

- Shared rule changes now require editing one file.
- Security rules can never be deprioritised — they are prepended before domain instructions.
- Formatting bugs became isolated to `format_summary.md` and were fixed once for all agents.

---

## Iteration 3 — Per-Row LLM Enrichment (`llm_insights`) Evolution

This was the most iterative phase. The `predict_delivery_delays` agent needed to generate a natural-language insight for every delayed order row, stored in an `llm_insights` column in the output CSV.

### Phase 3.1 — Initial Attempt: Generic Instructions

**Prompt (paraphrased):** *"For each delayed order, generate a brief explanation of why the delay occurred."*

**Problem:** The model generated generic, pattern-repeated text. Examples of what it produced:
- "Order delayed due to weather conditions."
- "Delay caused by distance and traffic."

These added no value over the structured columns already in the data. The model was not using the derived engineered features — it was hallucinating plausible-sounding but content-free explanations.

### Phase 3.2 — Explain the Derived Features

**Change:** Added a field glossary to the prompt explicitly naming the engineered features the model should reference:

```
Derived features available per row:
- schedule_risk: km_per_expected_hr × mode_urgency (higher = tighter deadline) *(definition later corrected — see note below)*
- vehicle_load_strain: load_capacity × mode_urgency (higher = overloaded vehicle on urgent run)
- km_per_expected_hr: distance_km / expected_delivery_time_hrs (planned speed pressure)
- vehicle_type: Bike/Truck/Van — affects speed ceiling and weather sensitivity
```

**Problem:** Insights improved in specificity; but some rows were still being skipped or ramdonly not generating inferences at all for few runs. This was due to the smaller version model GPT-4.1-mini context window limits getting crossed and lack for structured outputs.

### Phase 3.3 — Narrow the Output Contract

**Change:** Explicit output schema constraint added to the prompt:

```
YOUR OUTPUT HAS ONLY 2 FIELDS:
{
  "predict_summary": "...",
  "delayed_orders": [
    { "delivery_id": "...", "llm_insights": "..." },
    ...
  ]
}
DO NOT output formatted_stats, raw rows, or summary tables.
MUST fill llm_insights for EVERY row in the delayed_orders list.
```

This enforced a strict Pydantic-compatible output structure (matching the `PredictOutput` model) and prevented the model from omitting fields.

**Result:** Parsing errors eliminated. Coverage improved to near 100% of rows.

### Phase 3.4 — Few-Shot Examples for Insight Quality

**Change:** Two concrete examples added directly in the prompt (paraphrased below — actual text in `agents/predict_delivery_delays.md`):

**Example 1 — Bike, same-day, stormy weather, 285 km:**
```
llm_insights: "Bike on a same-day 285 km run in stormy weather is the core risk.
schedule_risk is extreme (high km_per_expected_hr × same-day urgency=3).
vehicle_load_strain is moderate (bikes have low load capacity).
Stormy weather further reduces safe speed. The model assigns Long delay (6+h)."
```

**Example 2 — Truck, standard delivery, clear weather, 92 km:**
```
llm_insights: "Truck on a 92 km standard run in clear weather.
schedule_risk is low (standard urgency=0, comfortable km_per_expected_hr).
vehicle_load_strain is high (trucks carry heavier loads) but standard urgency absorbs it.
Short delay (1-2h) driven by mild load strain; weather is not a factor."
```

**Design rationale for the examples:** The two examples were chosen to span extremes — high-risk multi-factor scenario vs. low-risk single-factor scenario — forcing the model to learn the *cross-dimensional* reasoning pattern rather than templating a single style.

### Phase 3.5 — Enforce Cross-Dimensional Reasoning

**Change:** Added an explicit rule:

```
RULE: Each llm_insights entry MUST reference at least 2 derived features
(schedule_risk, vehicle_load_strain, km_per_expected_hr, vehicle_type).
Do NOT produce a single-sentence explanation.
```

**Also changed:** `predict_summary` format was tightened to a specific template with a `### Cross-Dimensional Delay Insights` section listing the top 3 contributing feature interactions across the batch — giving the summary a diagnostic dimension beyond counts and percentages.

### Final State of `llm_insights` Prompt

The final prompt produces insights that:
- Reference ≥2 derived features per row
- State the model's assigned severity and why it follows from the feature values
- Cross-validate: if a bike has low schedule_risk despite long distance, the model explains why (e.g., standard urgency absorbs it)
- Cover 100% of rows in the output (enforced by schema contract)

---

---

## Iteration 4 — Diagnosis Agent: From Short Summaries to Deterministic + LLM Hybrid

### Initial Problem

The first version of `diagnose_delay_patterns.md` gave the agent a free-form instruction to "summarise the diagnosis". Results:
- Summaries were 2-3 lines long, e.g. *"Today had higher delays in East region and stormy weather conditions."*
- Root causes were missing entirely — the agent reported what happened but not why.
- Numbers were inconsistently stated (sometimes percentages, sometimes fractions, sometimes missing).
- Ordering of sections changed between runs, making the output unpredictable for downstream rendering.

### Phase 4.1 — Pydantic Output Schema (Deterministic Structure)

The key insight was that the structural parts of the diagnosis — comparison tables, high-risk pattern lists, KPI numbers — should not be left to the LLM to format. These are direct reads from the database.

**Change:** Defined `DelayDiagnosisResult` as a Pydantic model with explicit fields:
```python
class DelayDiagnosisResult(BaseModel):
    high_risk_patterns: list[...]   # direct copy from tool output
    comparison: list[...]           # daily vs hist dimension rows
    diagnosis_summary: str          # LLM generates only this field
```

The prompt now instructs the agent to *copy* `daily_high_risk_patterns` and `comparison` directly from tool output without re-narrating them. The LLM's job narrowed to generating only the `diagnosis_summary` string — the prose interpretation layer.

**Result:** Structured data became reproducible; only the narrative was LLM-generated.

### Phase 4.2 — Field Glossary for Root Cause Quality

The `diagnosis_summary` was still thin on root cause reasoning because the agent didn't understand what the derived columns meant.

**Change:** Added a full field glossary to the prompt:
```
- avg_schedule_risk: km_per_expected_hr × mode_urgency — higher = tighter deadline + more urgent mode
- pattern_type: High-risk combination type (e.g. mode_weather, weather_vehicle)
- risk_level: medium (30-40% delay rate), high (40-50%), critical (50%+)
- weather_severity: clear=0, hot/cold=1, rainy/foggy=2, stormy=3
- mode_urgency: standard=0, two_day=1, next_day=2, same_day=3

_(Note: several of these glossary definitions were later found to be inconsistent with `feature_engineering_4.py` — e.g. `schedule_risk` is actually `weather_severity × mode_urgency`. Corrected across all prompts and design docs in Iteration 8; see `docs/23` Section 12.)_
```

With these definitions in context, the Root Cause Analysis section began citing compound explanations (e.g. *"Same-day mode combined with stormy weather hits both schedule_risk and weather_severity simultaneously — the pattern_type 'mode_weather' at critical risk level confirms this as the primary driver"*) rather than surface-level observations.

### Phase 4.3 — Mandatory Section Ordering

Even with good content, the summary ordering varied between runs, which made the Gradio UI tab look inconsistent.

**Change:** Added explicit section ordering rules to the prompt:

```
Sections in order:
1. Overall (daily vs hist KPIs)
2. Worsening Patterns (top 5, sorted by rate_change_pct descending)
3. Improving Patterns (top 5, sorted ascending)
4. High-Risk Combinations (critical first, then high)
5. Root Cause Analysis
```

Also standardised formatting rules: `--` as separator (never em dash), bold labels, rounded percentages, bulleted lists with concrete numbers.

### Final State

The diagnosis agent now follows a clean separation: Pydantic handles structure/data fidelity; the LLM handles only prose interpretation within a fixed section template. This is the hybrid deterministic+LLM pattern applied to diagnosis.

---

## Iteration 5 — Recommendation Agent: Generic Actions → SLA-Grounded Specificity

### Initial Problem

Early recommendations were generic strategy advice that could apply to any logistics company:
- *"Improve delivery partner performance in high-delay regions."*
- *"Consider weather-proofing for stormy conditions."*
- *"Reduce delays by optimising routes."*

No numbers, no thresholds, no connection to the SLA/OLA document, no prioritisation rationale. The agent was reasoning from the summary text it received rather than the underlying data fields.

### Phase 5.1 — Data Granularity: Pass the Right Fields

**Problem:** The `recommend_actions` tool was only returning high-level aggregate statistics. The agent had no access to dimension-level breakdowns (delay rate by region + mode + weather combo, hotspot patterns, severity distributions per category).

**Change:** Extended the tool output to return:
- Overall daily vs historical comparison
- Per-dimension worsening/improving breakdown
- Historical and daily high-risk pattern combinations (mode+weather, weather+vehicle, etc.)
- Today's long-severity hotspots (mode + region + weather combos)
- Worst dimensions historically and today

With richer input data, the agent began producing recommendations targeting specific combinations (e.g. *"Bike deliveries in East region under stormy weather: 67% delay rate today vs 41% historical"*) rather than generic dimensions.

### Phase 5.2 — SLA Context via RAG Integration

**Problem:** Even with specific data, recommendations lacked grounding — no penalty amounts, no escalation thresholds, no improvement priorities. The SLA/OLA document existed in ChromaDB but the agent had no access to it.

**Change:** Modified the `recommend_actions` tool to retrieve relevant SLA sections from ChromaDB using the worsening dimensions as the query. The retrieved context is appended to the tool output between delimiter markers:
```
--- SLA Knowledge Context (retrieved via RAG) ---
[relevant SLA sections]
--- End SLA Context ---
```

The prompt was updated with explicit instructions on how to use this context:

```
For each recommendation:
- Fill sla_reference with a direct quote or specific citation from the SLA context
  (e.g. "SLA Section 3.2: Express delivery target 95% on-time, penalty: ₹500/order above 5% delay rate")
- Weave SLA references into action_desc — explain how the recommendation addresses or mitigates an SLA violation
- If a retrieved SLA section mentions a penalty amount, escalation tier, or improvement priority, you MUST include it
```

### Phase 5.3 — Three-Category Structure with Temporal Reasoning

**Problem:** All recommendations were lumped together with no time horizon distinction, making it impossible for the user to know what to act on immediately vs. plan for next quarter.

**Change:** Defined three mandatory categories with distinct data sources:

```
Quick-wins       → Today's hotspots + long-severity orders (act NOW)
Short-term       → Dimensions worse than historical today (1-4 weeks)
Long-term        → Historical high-risk patterns vs SLA benchmarks (1-3 months+)
```

Each category was given specific data fields to reference, avoiding overlap and ensuring temporal logic. Rules added: *"Do NOT provide generic or cookie-cutter recommendations. EVERY recommendation MUST cite specific numbers in supporting_data and a non-empty sla_reference quoting a specific SLA clause."*

### Final State

Each recommendation now has four fields: `action` (what to do), `dimension` (which operational dimension), `supporting_data` (specific numbers), and `sla_reference` (direct SLA quote). Generic outputs are structurally impossible — the prompt enforces specificity through field contracts.

---

## Iteration 6 — Email Alert Agent: Formatting Chaos → Deterministic Template Passthrough

### Initial Problem

The first approach had the LLM generating email content directly from delayed order data. Multiple issues emerged:

- **Formatting inconsistency:** Subject line format, greeting style, closing phrase, and severity escalation language changed between runs.
- **Template invention:** The model invented different escalation thresholds for Short/Medium/Long severity emails rather than applying consistent company-standard language.
- **Individual generation at scale:** When asked to generate emails for 50+ orders, the model would generate 5-10 samples and claim completion, silently skipping the rest.
- **Output size explosion:** Full email content for every order in the chat response made the output unreadable.

### Phase 6.1 — Move Email Generation to the Tool (Deterministic)

**Key decision:** Email content generation was moved entirely out of the LLM and into the `fetch_delayed_orders_for_email` Python tool. The tool:
1. Reads all delayed orders from the prediction CSV
2. Selects the appropriate template based on `delay_severity` (Long / Medium / Short) — Python `if/else`, not LLM
3. Fills in order-specific fields (customer ID, delivery partner, delay hours)
4. Writes `email_template_name` and `email_content` columns directly back to the CSV
5. Returns only a **summary** (template counts, template definitions, sample emails)

The LLM's role was reduced to: call the tool, then pass the tool's summary output through as-is into the `EmailsList` Pydantic structure.

### Phase 6.2 — Prompt Constraint: No Individual Generation

Even after the tool change, the agent would sometimes try to re-generate individual emails from the summary, producing a second inconsistent set on top of what the tool already wrote.

**Change:** Added explicit negative constraints to the prompt:
```
Do NOT attempt to generate individual emails per customer — the tool already did that and wrote them to CSV.
Do NOT call any prediction tools.
Create ONE EmailAlert whose email_content field contains the ENTIRE tool output.
Set email_id to "system@delivery-alerts.com" (this is a summary, not an actual customer email).
```

### Final State

Email generation is now fully deterministic. The LLM handles only routing (call tool → pass output through). Template consistency, coverage (every order), and formatting are guaranteed by Python, not by prompt instructions. The Gradio UI tab shows the summary with sample emails; the CSV has the full per-order output.

---

## Iteration 7 — Simulation Agent: Free-Form Enrichment → Consistent Row-Level Template

### Initial Problem

The simulation agent was asked to: (1) translate a natural-language what-if scenario into tool filters and column changes, (2) call `simulate_order_delays`, (3) enrich each returned row with a `simulate_delay_reason`.

Early problems:
- **Short summaries:** The agent would generate a single paragraph summary of the scenario instead of per-row enrichment (e.g. *"Stormy weather in East region is expected to increase delays"*).
- **Row coverage:** The agent would enrich 5-10 sample rows and stop, leaving `simulate_delay_reason` blank for most orders.
- **Inconsistent reason format:** Reasons ranged from one-word (*"weather"*) to multi-paragraph analyses, with no consistent structure.
- **Invalid filter values:** The agent would pass filter values in the wrong format (e.g. `"Weather": "Stormy"` instead of `"weather_condition": "stormy"`) causing tool errors.

### Phase 7.1 — Valid Values List

**Change:** Added an explicit valid values section to the prompt:
```
Valid values (lowercase):
- weather_condition: clear, cold, foggy, hot, rainy, stormy
- region: central, east, north, south, west
- delivery_mode: express, same day, standard, two day
- vehicle_type: bike, ev bike, ev van, scooter, truck
```

This eliminated filter validation errors.

### Phase 7.2 — Enforce Per-Row Enrichment

**Change:** Added explicit row coverage rules:
```
You MUST call simulate_order_delays exactly once.
After calling the tool, ENRICH EVERY simulation row with simulate_delay_reason
inferred from the scenario, the original_severity, simulated_severity, and other columns.
You MUST return the same number of rows as the tool does.
```

The key phrase *"same number of rows as the tool does"* was the critical fix — it made the completeness requirement concrete and checkable rather than vague.

### Phase 7.3 — Structured Reason Format via Output Pydantic Model

The `simulate_delay_reason` field was still producing inconsistent verbosity even with per-row coverage. The fix was to keep the reason concise and comparative: reference the `original_severity`, `simulated_severity`, and at least one causal factor (weather change, distance, vehicle type) in a standardised sentence pattern.

This was enforced via the `SimulationRow` Pydantic model fields rather than free-form prompt instructions — the model is constrained to populate defined fields rather than write free text.

### Final State

The simulation agent now: validates filter/change values against known enums, enriches every returned row (row count parity enforced), and produces compact comparative reasons (`original_severity → simulated_severity + causal factor`) rather than free-form prose.

---

## Iteration 8 — WYSIWYG Prompt Loading & Conversational Master (2026-07-04)

### What Changed

A code review revealed that the loader was silently dropping every prompt section outside Role / Goal / Backstory / Task / Expected Output — agents never saw their Rules, few-shot examples, glossaries, or valid-value lists. The Role/Goal/Backstory recombination was a CrewAI-era pattern with no purpose under the Microsoft Agent Framework, so it was removed entirely: `get_instruction()` now passes each agent's `.md` file **verbatim** (WYSIWYG — what is written in the file is exactly what the model receives). The master agent keeps its three-layer assembly (security → behaviour → expert); the duplicated interaction rules inside `master_expert.md` were replaced with a pointer to the shared behaviour layer.

### Follow-on Prompt Fixes (from end-to-end testing)

| Prompt | Change |
|---|---|
| `delay_simulation.md` | Transcribe only the tool's capped row sample (full results read from CSV by the app); map informal wording to valid enums ("severe" → stormy); changed values go in `changes`, never `filters`; on tool error return an empty list instead of claiming success. |
| `master_expert.md` | New `chat_response` contract — informational questions answered directly from fresh results without re-running tools; quote the simulation tool's error message instead of "no changes"; `[SYSTEM: PLAN CONFIRMED]` tag suppresses re-confirmation after the UI plan is accepted. |
| `chatbot_behavior.md` | Distinguishes informational questions (direct answer) from action requests (plan → confirm → execute); PLAN CONFIRMED override rule. |
| `predict_delivery_delays.md` / `diagnose_delay_patterns.md` | Plain-language intro sentences; bold every number, percentage, severity label, and category name so key figures stand out when skimming. |

### Final State

Prompt files are the single source of truth for agent behaviour — no hidden filtering between file and model. Confirmation happens exactly once (deterministic gate), the master answers questions conversationally via `chat_response`, and structured row data flows through files on disk rather than LLM transcription wherever volumes exceed what an LLM can copy reliably.

