# 23 — Code Simplification, Consistency Refactoring & Test-Driven Fixes

**Dates:** 2026-07-04 → 2026-07-12
**Scope:** started as `supply_chain_delivery_app/` only (Section 1–Section 9); test-driven fixes later extended into `prediction_pipeline/` (simulate tool, `daily_predict.py`, DB metadata) and `evals/` (conftest judge-score export, human-baseline source, `eval_config.py`) — each extension documented in its section.
**Constraint honored:** no folder names, file names, agent names, or tool names were changed. One stray duplicate prompt file was deleted (approved).

## Document Map

| Section | Change | Type |
|---|---|---|
| 1–7 | Initial refactor: WYSIWYG loader (Section 2), sub-agent factory (Section 3), handler/helpers cleanup (Section 4–Section 6), verification (Section 7) | Refactor |
| 8 | Prompt overlap dedup (master vs shared layers) | Prompt |
| 9 | Items intentionally not changed | Record |
| 10 | Test-run fixes: simulation bug (10.1), bold formatting (10.2–10.3), conversational chatbot (10.4), double-confirmation bug (10.5), enriched plan labels (10.6) | Bug fix |
| 11 | Human-baseline eval uses latest judge scores (merge + in-session source) | Eval |
| 12 | Field-glossary corrections vs feature_engineering code | Prompt/data |
| 13 | Master no longer re-copies predict rows; composing heartbeat | Latency/UX |
| 14 | MasterOutput slimmed to 4 fields; format_summary_tool unwired (agent kept for future use) | Architecture |
| 15 | Shared `field_glossary.md` + `@include` mechanism; diagnosis heading rename | Prompt |
| 16 | llm_insights sees all importance-ranked features (`_MCP_DISPLAY_COLS`) | ML/prompt |
| 17 | Progressive tab updates; dead MCP-log branch removed | UX/cleanup |
| 18 | Operator-visible run warnings; recommend logging; CrewAI heading renames (Purpose/Objective/Context) | Observability |
| 19 | Loader fully uniform — master layering via `@include` in master_expert.md | Architecture |
| 20 | Helper dead-code audit (zero dead; two trivial helpers folded in) | Cleanup |
| 21 | Eval instructions audit — docs matched to actual behaviour (ragas gating fiction removed, `--agent master` removed, output listings completed) | Eval |
| 22 | Predict judge-score split (summary vs. per-row insights) + RAG eval redesign to per-topic RAGAS sampling | Eval |

Documentation was kept in sync throughout: READMEs, `docs/01/03/05/08/09/11–15/18–21` updated per change; Iteration 8 rows added to the prompt evolution log and README version-history tables; Format-agent status notes placed across all documents.

Note: the Section 1 line-count table reflects the initial refactor (Section 1–Section 9) only; later sections change several of those files again.

---

## 1. Summary

| File | Before | After | Nature of change |
|---|---|---|---|
| `config/load_config.py` | 160 lines | 51 lines | Rewritten — WYSIWYG prompt loading (fixes dropped-sections bug) |
| `config/__init__.py` | 5 | 5 | Removed dead `load_yaml` export |
| `delivery_agents.py` | 342 | 362 | Sub-agent factory; removed unused imports; single tool-name list |
| `delivery_chat_app.py` | 878 | 798 | Moved usage helpers out; deduped tab-tuple logic |
| `helpers/app_utils.py` | 326 | 304 | Clean imports; simpler plan-builder (equivalent output) |
| `helpers/logging_utils.py` | 33 | 106 | Gained token-usage extraction helpers (moved from chat app) |
| `helpers/post_processing.py` | 266 | 283 | Deduped helpers; documented formatting contract |
| `tools/email_customers.py` | 218 | 218 | Fixed mis-indented comments; removed placeholder-less f-strings |
| `tools/rag_knowledge.py` | 425 | 369 | Removed 50-line commented-out dead code; removed unused constant |
| `README.md` | 266 | 267 | Updated prompt-assembly description to match new loader |
| `config/prompts/agents/master_expert.md` | 231 | 199 | Removed section duplicating chatbot_behavior.md (see Section 8) |
| `config/prompts/shared/security_guardrails.md` | 80 | 78 | Removed stray duplicate heading at end of file (see Section 8) |
| `config/prompts/agents/recommendation 2.md` | — | deleted | Stray duplicate, never loaded by code |

A full pre-refactor backup of the app folder is available for diffing (see Section 7).

---

## 2. The significant fix — prompt sections were silently dropped

### 2.1 The problem

The old `build_instruction()` in `config/load_config.py` parsed each agent's
`.md` prompt file into sections, then rebuilt the instruction from **only five
sections**: `## Role`, `## Goal`, `## Backstory`, `## Task`, `## Expected Output`
(a CrewAI-style pattern carried over to the Microsoft Agent Framework). Every other
section was parsed and then **discarded** — it never reached the LLM:

| Prompt file | Sections that never reached the agent |
|---|---|
| `predict_delivery_delays.md` | `## YOUR OUTPUT HAS ONLY 2 FIELDS`, `## Rules` (incl. both few-shot llm_insights examples) |
| `recommendation.md` | `## How to Use the SLA Knowledge Context`, `## Instructions`, `## Rules` |
| `diagnose_delay_patterns.md` | `## Field Glossary`, `## Summary Formatting Rules` |
| `delay_simulation.md` | `## Valid values (lowercase)` |
| `email_alert.md` | `## Instructions` |

Measured impact: the predict agent received ~2,094 of ~7,500 characters
written in its prompt file (28%). After the fix it receives 7,493 (100%).

### 2.2 The fix — WYSIWYG loading

There is no technical reason to reassemble "You are {role}. Your goal is
{goal}…" sentences: the Microsoft Agent Framework takes any string as `instructions`,
and the markdown files are already well-structured prompts. The loader now
passes **the full file content verbatim**:

```python
def get_instruction(agent_key: str) -> str:
    if agent_key == "master_expert":
        layers = [_read_prompt(key) for key in _MASTER_LAYERS]   # security_guardrails, chatbot_behavior
        layers.append(_read_prompt(agent_key))
        return _LAYER_SEPARATOR.join(layers)
    return _read_prompt(agent_key)
```

Consequences:

- **What you write in a prompt file is exactly what the agent gets** — no
  hidden filtering. This is the single consistency rule for how instructions
  are read, replacing three different code paths (sectioned agents /
  `format_summary` raw / `master_expert` layered) with one rule plus one
  explicit master-agent special case.
- The master agent's layering (security_guardrails → chatbot_behavior →
  master_expert, highest priority first) is **unchanged**.
- `format_summary` behavior is **unchanged** (it was already loaded raw; now
  it simply follows the same rule as everyone else).
- Sub-agent prompts now start with `## Role`-style headings instead of the
  synthesized "You are …" sentence. LLMs read markdown headings natively;
  the content is a strict superset of what was sent before.

**Note:** this is the one intentional behavior change in the refactor
(explicitly approved). Agent outputs may improve/change because agents now see
their complete rules, examples, and glossaries. All other changes are
behavior-preserving.

### 2.3 Dead code removed with it

- `load_yaml()` + the `tasks.yaml` override branch in `build_instruction()`:
  no `tasks.yaml` exists anywhere in the project and no caller ever passed
  `task_key`. Removed, along with the now-unneeded `yaml` and `re` imports and
  the four one-line `_*_dir()` path helpers (replaced by two module constants).
- `build_instruction()` itself is gone; `get_instruction()` (the only function
  callers ever used) remains the public API with the same signature minus the
  never-used `task_key` parameter.
- `config/__init__.py` now exports only `get_instruction`.

---

## 3. `delivery_agents.py` — consistent agent construction

### 3.1 Sub-agent factory

All five domain sub-agents (predict, diagnose, simulate, recommend, email)
followed the identical recipe, written out five times with small accidental
formatting drift. They are now built through one factory that makes the recipe
explicit and guarantees consistency:

```python
def _sub_agent_as_tool(*, agent_name, prompt_key, output_type, tool_name,
                       tool_description, use_pipeline_mcp=False, function_tools=None):
    """instructions from prompts/agents/<prompt_key>.md, temperature=0,
    tool_choice="required", typed Pydantic output → (agent, agent-as-tool)."""
```

Each definition shrank from ~14 lines to ~8, e.g.:

```python
predict_delivery_delays_agent, predict_delivery_delays_tool = _sub_agent_as_tool(
    agent_name="Predict Delivery Delays",
    prompt_key="predict_delivery_delays",
    output_type=DeliveryDelayPredictionResult,
    tool_name="predict_delivery_delays_tool",
    tool_description="Run the two-stage ML pipeline to predict delayed orders and classify severity",
    use_pipeline_mcp=True,
)
```

Unchanged on purpose: every agent **name**, tool **name**, tool description,
model settings, output type, and the module-level variable names that
`tests/` and `evals/` import. `fallback_advisor_agent` and
`format_summary_agent` intentionally stay outside the factory (no forced tool
use / different model), and the master agent is unchanged.

### 3.2 Other changes in this file

- Removed unused imports: `Runner`, `trace`, `WebSearchTool` (only the chat
  app runs agents; no agent uses web search).
- New exported constant `PIPELINE_TOOL_NAMES` — the allowed MCP tool names
  were previously written twice (here in `tool_filter`, and hard-coded again
  in `delivery_chat_app.py` for timing logs). Now defined once and imported by
  the chat app.
- Section comments renumbered 1–8 (the old file numbered them 1, 3, 4, …, 9 —
  there was no section 2).

---

## 4. `delivery_chat_app.py` — slimmer handler, no logic changes

- **Token-usage extraction moved to `helpers/logging_utils.py`.** The three
  functions `_usage_from_container`, `_value_from_usage`,
  `_extract_usage_from_event` (~70 lines) are logging concerns, not UI
  concerns. They now live next to `setup_run_logger`; the public name is
  `extract_usage_from_event`. Code is byte-identical apart from the tidied
  docstrings and one micro-simplification (list literal instead of
  `candidates = []` + `extend`).
- **One tab-tuple builder instead of two.** The handler had `_tabs()` (final
  tuple) and a nearly identical 12-line `_running_tabs` block with its own
  `_keep_or_pending()` helper (in-progress tuple with hourglass placeholders).
  Merged into `_tabs(show_pending: bool = False)`; the running variant is now
  `_tabs(show_pending=True)`. The `_PENDING` hourglass HTML moved to module
  level next to `_WELCOME_MSG`. Rendering output is identical.
- `mcp_tool_names = set(PIPELINE_TOOL_NAMES)` replaces the duplicated
  hard-coded set (see Section 3.2).
- Removed `is_file_fresh` from the `helpers.app_utils` import list — it was
  imported but never used in this module.
- Everything else — caching, confirmation flow, streaming loop, Gradio UI
  wiring, CSS — is untouched.

---

## 5. Helpers

### 5.1 `helpers/app_utils.py`

- **Normal imports.** `import json as _json`, `import re as _re`,
  `import time as _time` and the `__import__("os")` inline hack are replaced
  by plain `import json / os / re / time`. No functional difference; much
  easier to read.
- **Plan-builder simplified.** The old design encoded prerequisites as label
  strings with an embedded `" (if not fresh)"` marker, then needed three
  helpers (`_resolve_freshness`, `_slot`, `_STEP_ORDER`) to parse its own
  labels back apart, match on substrings like `"-- skip"`, and re-derive
  ordering. The new design separates data from presentation:

  ```python
  _STEP_LABELS = {"predict": "Predict delivery delays", ...}   # order = execution order
  TOOL_DEPS    = {"predict": [], "diagnose": ["predict"],
                  "simulate": ["predict"], "recommend": ["predict", "diagnose"],
                  "email": ["predict"]}
  _STEP_SIDECARS = {"predict": PREDICT_SIDECAR, "diagnose": DIAG_SIDECAR}
  ```

  `_merge_steps()` is now 15 straightforward lines: union the requested tools
  with their prerequisites, walk the steps in canonical order, and mark a
  prerequisite-only step "-- skip (data is fresh)" when its sidecar is fresh.
  The old "run beats skip" merging rule falls out naturally (a directly
  requested step is never marked skip). Three helpers and the `_STEP_ORDER`
  table are deleted.

  **Verified equivalent:** old and new `build_action_plan()` were run side by
  side over 17 representative queries (single-intent, multi-intent, composite
  SLA/KPI phrasing, full-pipeline triggers, greetings, gibberish, quick-action
  text) × 3 freshness scenarios (none/all/predict-only fresh) — 51 cases,
  zero differences (see Section 7).
- `resolve_confirmation()`: removed the dead `pending_query = ""` assignment
  (a no-op on a local variable) and replaced it with a comment stating the
  intent; all return paths identical. Verified against the old implementation
  on 7 confirmation/pending scenarios — identical results.
- All public names kept: `TOOL_DEPS`, `TOOL_KEYWORDS`, `CONFIRM_RE`,
  `build_action_plan`, `resolve_confirmation`, `clarification_message`,
  `format_plan_text`, `brief_args`, `is_file_fresh`,
  `build_freshness_system_msg`, `save_diagnosis_sidecar`, `list_dir_files`,
  `knowledge_files`, `input_files`, `PREDICT_SIDECAR`, `DIAG_SIDECAR`,
  `FRESHNESS_TTL`, `DISPLAY_ROWS`.
  (Note: `TOOL_DEPS` values changed shape from label-lists to key-lists; it is
  not imported anywhere else in the project.)

### 5.2 `helpers/logging_utils.py`

Now the single home for all logging concerns: `setup_run_logger()` (unchanged)
plus the token-usage extraction helpers moved from the chat app (Section 4).

### 5.3 `helpers/post_processing.py`

- **Formatting contract documented** in the module docstring — this answers
  "where does formatting happen" once, explicitly:
  - predict / simulate / recommendation / email tabs: final markdown is built
    deterministically **here** from structured Pydantic rows; agent narrative
    is appended after duplicate headings are stripped.
  - diagnosis tab: the agent's `diagnosis_summary` markdown is displayed
    as-is; only the two DataFrames are built here.
- Deduplicated two patterns that each appeared 3–4 times:
  - `_ensure_output_dir(app_dir)` — mkdir-and-return `output/`
    (was inline in `process_predict` ×2, `process_simulate`;
    `process_emails` now uses it too — the mkdir there is idempotent).
  - `_strip_leading_heading(text)` — the regex that removes an agent-written
    duplicate heading (was inline in `process_simulate` and
    `process_recommendations`).
- No numeric, ordering, or content changes to any produced markdown/CSV.

---

## 6. Tools

### 6.1 `tools/email_customers.py`

- Fixed six comments that were indented at the wrong level (4 spaces instead
  of 8 inside the `try:` block) — they visually broke the block structure.
- Removed the `f` prefix from four f-strings with no placeholders.
- No logic changes: templates, severity mapping, CSV write-back, row cap, and
  the returned summary are byte-identical.

### 6.2 `tools/rag_knowledge.py`

- Deleted the ~50-line commented-out block of superseded custom chunking
  functions (`_split_md_sections`, `_chunk_section`) — the langchain-based
  pipeline replaced them; the git-style historical record now lives in this
  document instead of the source file.
- Removed unused constant `_EMBED_DIM` (folded into a comment on
  `_EMBED_MODEL`).
- Retrieval pipeline (chunking, hybrid pre-filter, cross-encoder rerank,
  caching) untouched.

### 6.3 `tools/recommend_actions.py`

No changes — already consistent with the tool conventions (start/finally
logging with status + duration, ERROR-string returns for missing
prerequisites).

---

## 7. Verification performed

1. `python -m py_compile` on all 10 changed Python files — clean.
2. **Prompt-loader check:** for each sub-agent, asserted the new instruction
   contains the previously dropped section headings (Rules, few-shot examples,
   Field Glossary, Valid values, Instructions, SLA-context guidance) — all
   present; master layering order and `format_summary` passthrough confirmed.
3. **Plan-builder equivalence test:** old vs new `build_action_plan()` and
   `resolve_confirmation()` executed side by side (17 queries × 3 freshness
   scenarios + 7 confirmation cases) — **0 mismatches**.
4. **AST check:** every module-level name imported by `tests/` and `evals/`
   (all 15 Pydantic models, all 7 agents, all 6 tools, `pipeline_mcp`) is
   still defined in `delivery_agents.py`.
5. **Leftover-reference sweep:** grepped the app for all removed symbols
   (`build_instruction`, `load_yaml`, `_extract_usage_from_event`,
   `_keep_or_pending`, `WebSearchTool`, `_EMBED_DIM`, aliased `_json./_re./_time.`,
   `__import__`, `tasks.yaml`) — no references remain.
6. The full pre-refactor copy of the app folder was saved before any edit and
   used for the equivalence tests and line-count diffs in Section 1.

**Recommended local check** (needs your venv with the Microsoft Agent Framework):

```bash
cd 0_supply_chain_thesis
pytest tests/ -q                 # pydantic models, MCP server, RAG smoke tests
python supply_chain_delivery_app/delivery_chat_app.py   # end-to-end UI run
```

---

## 8. Prompt overlap analysis (agent files vs shared files)

A line-level cross-comparison of all 7 agent prompt files and 3 shared prompt
files was run to find content duplicated **within a single assembled prompt**
(only the master agent assembles multiple files: security_guardrails →
chatbot_behavior → master_expert). Two real duplications were found and fixed:

1. **`master_expert.md` — `## Chatbot Interaction Rules` section (~40 lines)
   was a near-verbatim restatement of `shared/chatbot_behavior.md`** (same
   trigger-keyword table, action-plan format, confirmation rules, clarification
   and error-handling rules). Because chatbot_behavior.md is prepended to the
   master prompt, the master agent received all of it twice. Every rule in the
   duplicated section was verified to exist (same or stricter wording) in
   chatbot_behavior.md before removal. The section is now a 4-line pointer to
   the shared layer. No instruction was lost; the assembled master prompt
   shrank by ~1.3k characters per request.

2. **`shared/security_guardrails.md` ended with a stray
   `# Chatbot Interaction Behavior` heading** — a leftover "bridge" from when
   the files were one document. In the assembled prompt this produced the same
   heading twice in a row. The duplicate heading was removed; the transition
   sentence was kept and reworded to reference "the next layer".

Overlaps that were examined and deliberately **kept**:

- `delay_simulation.md` and `master_expert.md` both list the valid lowercase
  values for weather/region/vehicle/mode. These are two *separate* agents with
  separate prompts (no double-feeding within one context); the master needs
  the list to construct simulate tool arguments, the simulate agent needs it
  to validate them.
- `diagnose_delay_patterns.md` and `shared/format_summary.md` share one
  definition line (`delay_rate`) — again different agents, harmless.

Verification: after the edits, the assembled master instruction contains
exactly one trigger table, one `# Chatbot Interaction Behavior` heading, and
preserves the layer order security → behavior → master.

---

## 9. Items intentionally NOT changed

- `prediction_pipeline/`, `evals/`, `tests/` — out of scope by agreement.
- Agent names, tool names, file/folder names, and all public symbols.
- The slight naming inconsistency of the diagnose tool
  (`tool_name="diagnose_delay_patterns"` — no `_tool` suffix, unlike the other
  four): renaming would change the tool name the master agent's prompt and
  logs refer to, so it was left as-is per the no-renames constraint.
- `print()` statements inside tools alongside logger calls — they serve as
  console feedback when tools run under the MCP server; removing them would
  change observable behavior.
- The notebook `supply_chain_delivery_app/notebooks/logic-workflow-fixes.ipynb`
  was initially left as a historical record — later removed along with the
  empty notebooks/ folder (2026-07-05, user decision): it contained 27
  markdown cells and no code, and its content had been formalised into
  docs/08-15 (the README itself called it "source for agent workflow docs").
  Preserved in the pre-refactor backup. Stale README tree entries removed.

---

## 10. Fixes from user testing (2026-07-04 test run)

Four issues were reported after an end-to-end test run; forensics used
`log/delivery_chat_run_20260704_212609.log` and the output CSVs.

### 10.1 Simulation returned "ran, no changes" (BUG — root cause & fix)

There are two simulation CSVs, and distinguishing them explains the bug:

- `prediction_pipeline/data/processed/simulation_delivery_delays.csv` —
  written by the **Python tool itself** (step 7 of `run_simulation`), before
  any LLM touches the results.
- `supply_chain_delivery_app/output/simulate_delays_latest.csv` — written by
  the **app** from rows transcribed by the LLM agents
  (tool → simulate sub-agent output → master `simulate_rows` → app). This is
  what the UI displayed.

**Test run 1** ("what if the weather gets rainy in east region?"): the tool
call completed in 3.6 s and the pipeline CSV was NOT written — proving the
tool exited on an early-exit path (before step 7) and never simulated
anything. The most likely path given the master's own words ("No affected
rows were returned for this filter") is a filters/changes mix-up by the
sub-agent (e.g. the new weather placed in `filters` leaving `changes` empty →
tool returns an ERROR string). The tool's error text was swallowed and
reported as "no changes". (Exact args are visible in the terminal's
`[Simulation] run_simulation:` stderr lines, which are not in the log file.)

**Test run 2** ("Simulate delays for stormy weather in East region"): the
tool RAN CORRECTLY — the pipeline CSV written at 21:42 contains 241 rows with
a large severity shift (Short 122→19, Medium 97→152, Long 22→70). But the
tool call took 136 s and the master ended with an EMPTY `simulate_summary`
and no simulate rows: the sub-agent was required by its prompt to transcribe
ALL 241 rows through structured LLM output (and the master to copy them
again), which exceeds practical output limits and collapsed to nothing. The
correct results existed on disk and were lost in LLM transcription — the
app's `simulate_delays_latest.csv` still had June 25 contents, proving zero
rows reached the UI in either run.

**Fixes:**
- `helpers/post_processing.process_simulate()` now treats the pipeline CSV as
  the **source of truth** whenever its mtime shows it was written during the
  current run (`run_start_ts` passed by the handler). The agent's per-row
  `simulate_delay_reason` enrichment is merged in by `delivery_id`; agent rows
  are only a fallback. UI table capped to `DISPLAY_ROWS`.
- Column layout regression fix: the pipeline CSV carries all 17 prediction
  columns with the severity pair appended at the end, which pushed
  `simulated_severity` away from `original_severity` in the UI. The loaded
  data is trimmed/reordered to `_SIM_DISPLAY_COLS` (the `SimulateDelays`
  model order), restoring the original 10-column table with the two severity
  columns adjacent — identical whether data comes from the CSV or agent rows.
- `prediction_pipeline/src/simulate_delays.py`: the text report to the agent
  now caps the row table at 40 rows (`SC_SIM_REPORT_ROWS`), noting the full
  CSV path — removing the impossible transcription requirement.
- `delay_simulation.md`: transcribe only the rows shown; copy
  original/simulated severity exactly; map informal wording to valid values
  ("severe/extreme/bad" → stormy, etc.); put changed conditions in `changes`,
  never in `filters`; on tool error return an empty list (no fabrication).
- `master_expert.md` Section 4/Section 7b: when the tool errors or matches no rows, QUOTE
  the tool's message in `simulate_summary`; never report it as "ran with no
  changes". A partial `simulate_rows` list is now expected (app reads full
  results from disk).

### 10.2 Predict summary — elaboration + bold formatting

- `daily_predict.py` `formatted_stats`: all counts, percentages, and category
  names now bolded deterministically (labels were bold; values were not).
- `predict_delivery_delays.md`: summary must start with a 1–2 sentence
  plain-language intro; every number/percentage/severity label/category in
  bullets must be bold; each bullet 2–3 sentences (pattern → quantify →
  operational meaning).

### 10.3 Diagnosis summary — bold formatting

- `diagnose_delay_patterns.md` formatting rules: bold every number,
  percentage, dimension/category name, and risk level; Root Cause Analysis
  ends with 1–2 plain-language actionable sentences.

### 10.4 Chatbot not conversational (BUG — root cause & fix)

Two compounding causes:
1. The Python gate (`resolve_confirmation`) forced every keyword-matching
   message into a plan-confirmation and every other message into a canned
   clarification — questions never reached the agent.
2. Even when the master agent ran, `MasterOutput` had **no field for a
   conversational answer**, and a fallback-advisor handoff (plain string
   output) was silently dropped by the handler — a direct answer was
   structurally impossible to display.

**Fixes:**
- `MasterOutput.chat_response` (new optional field, default ""): direct
  answers for informational questions.
- `master_expert.md` new Section 9 "Conversational Answers": answer informational
  questions from fresh prior results/definitions in `chat_response` without
  re-running tools; leave empty when analysis tools run.
- `chatbot_behavior.md`: distinguishes informational questions (answer
  directly) from action requests (plan-confirmation flow unchanged).
- `app_utils.resolve_confirmation()`: messages that read as questions
  (interrogative opener or trailing "?") route straight to the agent
  ("run") instead of the plan builder. Action commands still get the
  plan-confirmation flow — verified by routing tests.
- `delivery_chat_app.py`: displays `chat_response` (or a handoff string) as
  the assistant reply when no analysis output was produced; tab-summary +
  welcome message shown only for analysis runs.

### 10.5 Double plan-confirmation + wrong tools after "yes" (BUG — root cause & fix)

Reported sequence: plan shown and confirmed → master showed a SECOND plan →
second "yes" → master called recommendation + email instead of predict +
simulate.

Two compounding causes:

1. **Two confirmation layers.** The Python gate (`resolve_confirmation`)
   shows a plan and waits for "yes"; but `chatbot_behavior.md` ALSO instructs
   the master agent to present a plan before calling tools. Before the
   `chat_response` field existed the master had no way to display its plan,
   so the conflict was invisible; once conversational output became possible
   (Section 10.4), the master's own plan surfaced → double confirmation.
2. **Pending-query recovery grabbed "yes" as the query.** When the master
   asks "Shall I proceed?" there is no stashed pending query, so the gate
   recovers it by walking chat history back to the last user message — which
   after a double confirmation is the FIRST "yes", not the original request.
   The master then received literally the message "yes" plus a freshness tag
   (each run is stateless), had no intent to work from, and improvised
   (recommendation + email because prediction/diagnosis data was fresh).

**Fixes:**
- Recovery now SKIPS user messages matching `CONFIRM_RE` when walking back,
  so it always recovers the message that started the exchange.
- New gate action `"run_confirmed"` (returned for a confirmed plan AND for
  quick-action clicks): the handler appends
  `[SYSTEM: PLAN CONFIRMED …]` to the agent query.
- `chatbot_behavior.md` + `master_expert.md`: when the message contains
  `[SYSTEM: PLAN CONFIRMED`, never present a plan or ask again — execute the
  tools immediately (rule overrides all other confirmation rules).

Verified by replaying the exact reported 3-turn sequence through
`resolve_confirmation`: turn 3 now resolves to the ORIGINAL
predict+simulate query with `run_confirmed`, and questions/commands/quick
actions still route as intended.

### 10.6 Enriched deterministic plan labels

The gate's plan labels were generic ("Simulate what-if scenarios") compared to
the master agent's natural-language plans. Rather than paying an extra LLM
round-trip per request for plan display, the deterministic plan now enriches
the simulate step with scenario parameters detected in the user's query:

> 1. Predict delivery delays
> 2. Simulate what-if scenarios (stormy weather, east region)

`_detect_sim_params()` in `app_utils.py` recognises weather / region /
delivery-mode / vehicle phrases using the SAME canonical values and synonyms
as the simulation agent prompt (so "severe weather" previews as "stormy
weather" — exactly what the tool will receive). Only the simulate step is
annotated, and only when it was directly requested — the other tools accept
no scenario filters, so annotating them would imply false precision.

### 10.7 Verification

- `py_compile` clean on all changed files.
- `process_simulate` unit-tested: fresh CSV wins over agent rows (full row
  count, reasons merged by id), stale CSV falls back to agent rows, empty
  case unchanged.
- Routing tests: questions → "run"; imperative analysis commands → "confirm";
  greetings → "greet"; unmatched text → "clarify".
- Note: `tests/test_pydantic_models.py` and `evals/` unaffected
  (`chat_response` has a default; no existing field changed).

---

## 11. Eval fix — human baseline now compares against the latest judge scores (2026-07-04)

The human-baseline report was showing outdated LLM scores (e.g. simulate at
4.x while the latest eval run scored 5/5) because BOTH sides of the
comparison came from static columns in `human_scores.xls` — a snapshot typed
in on June 25. The comparison had no link to fresh eval results.

Fix (the XLS is never modified):
- `evals/conftest.py` `pytest_sessionfinish` now also writes the judge scores
  of each run to `reports/judge_scores_<timestamp>.json` and a stable
  `reports/judge_scores_latest.json`.
  `judge_scores_latest.json` is merged per agent (not overwritten), so a
  single-agent or interrupted run updates only the agents it scored and
  preserves every other agent's most recent scores.
- `evals/test_eval_human_baseline.py` reads the LLM side from the judge's
  in-memory session records when running inside the full suite (fixes a
  write-ordering race where the same-session baseline report was written
  before the fresh scores JSON landed), else from `judge_scores_latest.json` (keyword-matched to XLS agent labels, tolerant
  of naming variants); the XLS `llm_*` columns remain only as a fallback when
  no eval run has been recorded. The report header now states which LLM-score
  source was used, and the divergence test uses the same source.

Note: run the eval suite once after this change (`uv run python
evals/run_evals.py`) so `judge_scores_latest.json` exists before re-running
the baseline comparison.

**Follow-up bug fix (2026-07-05):** the keyword mapper matched labels in the
order predict → diagnose → simulate…, and the simulate agent's label
("Simulate Delay **Prediction**") contains the substring "predict" — so the
simulate row was silently given the predict agent's scores (showing 5.00
while the eval report said 4.67). Keyword order flipped so "simul" is
checked first and "predict" last; verified against all five real labels. Caveat for interpretation: the human scores in the
XLS were given for June 25 outputs; a fully clean calibration still requires
re-scoring fresh outputs by hand (see Section 10 discussion), but the LLM side is
now always current.

---

## 12. Prompt glossary corrections (2026-07-04)

The field glossaries in `format_summary.md` and `diagnose_delay_patterns.md`
were incomplete AND contained definitions that contradicted the actual
feature engineering code (`feature_engineering_4.py`):

| Field | Prompts said | Code actually does |
|---|---|---|
| `schedule_risk` / `avg_schedule_risk` | km_per_expected_hr x mode_urgency | **weather_severity x mode_urgency** (0-16) |
| `weather_severity` | clear=0, hot/cold=1, rainy/foggy=2, stormy=3 | Clear=0, Hot/Cold=1, Foggy=2, Rainy=3, **Stormy=4** |
| `mode_urgency` | standard=0, two_day=1, **next_day**=2, same_day=3 (next_day is not a real mode) | Standard=1, Two Day=2, Express=3, Same Day=4 |
| `vehicle_load_strain` | package_weight_kg / vehicle_capacity | (package_weight_kg **x distance_km**) / vehicle_capacity |

Both glossaries now match the code exactly. `format_summary.md` additionally
gained the missing derived features (km_per_expected_hr, vehicle_capacity,
carrier_avg_schedule, carrier_avg_weight), prediction output fields
(predict_delay, predict_severity_label, delay_reason, llm_insights), and
diagnosis fields (pattern_description, rate_change_pct), organised into
Prediction / Derived / Diagnosis groups and marked as applying to all
summary types.

---

## 13. Latency & visibility — master no longer re-copies predict rows (2026-07-04)

**Symptom:** after the last "-- output received" line, the UI sat silent for
15-30 s before results appeared.

**Cause:** that gap is the master agent GENERATING its final MasterOutput.
Its prompt required copying ~50 predict enrichment rows (plus simulate/
diagnosis/recommendation rows) into the structured output — several thousand
tokens of JSON re-serialisation after all tools were already done. The UI
updates tabs only when that final output completes, and the streaming loop
emitted no status during pure generation.

**Fixes:**
- **predict_rows copy removed.** Verified `MasterOutput.predict_rows` has
  exactly one consumer (`process_predict`'s llm_insights merge) and nothing
  in tests/evals reads it. The app now captures the predict sub-agent's raw
  JSON from the `tool_call_output_item` stream event and parses/validates the
  {delivery_id, llm_insights} rows itself (`_parse_predict_rows`, each row
  validated as `RowEnrichment`). The master prompt (Section 0, Section 2, Section 8) now says to
  leave predict_rows EMPTY; the field remains in the schema with a default
  for backward compatibility, and master-copied rows still win if present.
- **Composing heartbeat.** The streaming loop now appends
  "-- master agent processing tool results / composing output..." when the
  master generates after a tool completes, and refreshes it every 3 s with
  elapsed time until the final output lands (or the next tool starts). No
  more silent gap.
- Row cap stays at 50 (`SC_MCP_ENRICH_ROWS` unchanged, per user decision) —
  the speedup comes from not re-emitting rows through the master, not from
  reducing enrichment coverage.

**Verification:** parser unit-tested against clean JSON, JSON embedded in
wrapper text, and garbage input (invalid/empty-insight rows dropped by
validation); all changed files compile; docs 08/09/18 updated to match.

---

## 14. MasterOutput slimmed to its real job (2026-07-04)

User review question: "why should the master re-copy what sub-agents produce —
that's why we have sub-agents." Verified consumers of every MasterOutput field
and removed everything that was pure transport:

**Removed fields** (all previously copied verbatim from sub-agent outputs, now
captured by the app directly from the tool-call stream via `tool_payloads` +
`_tool_json()`/`_validated_rows()` in `delivery_chat_app.py`, each row
validated against the existing Pydantic models):
- `predict_summary`, `predict_rows` (and the now-unused `DelayEnrichment` model)
- `diagnosis_summary`, `diagnosis_high_risk_rows`, `diagnosis_comparison_rows`
- `simulate_rows`, `recommendation_rows`, `email_alerts`

**Kept fields** (things only the master can write):
- `chat_response` — conversational answers / tool error reports
- `simulate_summary` — qualitative narrative or the simulate tool's error message
- `recommendation_summary` — 2-3 sentence optimization narrative
- `email_alert_summary` — brief email generation status

**format_summary_tool removed from the master's tool list** (agent + tool
remain defined; no names changed). Rationale: it was called for exactly one
summary type (email), and `process_emails()` discarded its output whenever
structured emails existed — the system's only format call was mostly wasted
work, and four "Do NOT call format_summary_tool for X" rules existed just to
protect it. All display formatting is now uniformly deterministic in
`post_processing.py`. Stale prompt lines removed with it, including the
reference to `predict_formatted_stats`/`predict_csv_path` (fields deleted from
MasterOutput long ago).

**Safety check:** nothing in `tests/` or `evals/` reads MasterOutput or
DelayEnrichment (verified by grep); the handler's tab gating now keys off
whether a tool's payload was captured this run, preserving the "don't
overwrite tabs for tools that didn't run" behaviour.

**Effect:** the master's final generation shrinks from thousands of tokens of
re-serialized rows to a handful of short fields — directly reducing the
15-30s post-"output received" delay (Section 13) — and the display data path has a
single source of truth: sub-agent output → stream capture → deterministic
post-processing.

---

## 15. Shared field glossary with @include directives (2026-07-04)

The derived-feature definitions were duplicated (and drifting) across
`predict_delivery_delays.md`, `diagnose_delay_patterns.md`, and
`format_summary.md`. They are now written ONCE in
`config/prompts/shared/field_glossary.md` — the complete, code-accurate list
(all engineered features incl. weight_x_distance and carrier averages, the
prediction output fields, and per-group diagnosis fields), with the four
features present in predict's per-row data marked `[row]`.

`get_instruction()` gained a minimal include mechanism: a line containing
only `@name` is replaced with that prompt file's content (one level, no
recursion; a missing file leaves the directive visible rather than failing).
The three prompts now contain `@field_glossary` instead of divergent copies —
verified that the assembled instruction for each contains the glossary
exactly once and that no other agent picks it up.

Also: `diagnose_delay_patterns.md`'s "## Summary Formatting Rules" heading
renamed to "## Summary Generation & Formatting Rules" — the section defines
both WHAT the summary must contain (sections, ordering, content) and HOW it
is styled, and the old name understated it.

---

## 16. llm_insights now sees the features that drove the prediction (2026-07-04)

User observation: the Random Forest uses the FULL feature vector (19
features), but the rows sent to the predict agent for llm_insights carried
only 4 derived features -- the explanations were blind to mode_urgency
(21.5% importance, the model's #2 feature), carrier_avg_schedule (~8%),
weather_severity (~7%), weight_x_distance (~5%), and cost_per_kg (~3%).

Clarification recorded: llm_insights does not influence the prediction (the
model has already run); it is an explanation layer -- but a faithful
explanation must be able to reference what the model actually weighted.

Changes:
- `daily_predict.py` `_MCP_DISPLAY_COLS`: extended with the five missing
  features, each annotated with its feature importance. Verified all exist in
  `df_predictions` (build_output keeps the full pre-encoding X). Side effect:
  the delayed-orders CSV and the predict tab table gain these columns.
- `shared/field_glossary.md`: [row] markers updated (nine row-level derived
  features now), importances noted, `cost_per_kg` entry added.
- `predict_delivery_delays.md`: row description and llm_insights rules list
  the full row feature set prioritised by importance, with an explicit
  instruction to cite features whose values are notable for THAT row rather
  than the same two mechanically.
- `evals/eval_config.py` `PREDICT_FEATURE_NAMES`: extended with the new
  features (English + snake_case variants) so insights citing them pass the
  feature-reference check.

---

## 17. Progressive tab updates + tool-logging cleanup (2026-07-04)

**Progressive tabs.** Previously all five tabs filled only after the master's
final output arrived. Now `_apply_payload()` (one nested function in the chat
handler) converts a tool's captured payload into tab output + persisted
tab_state, and is called TWICE at most per tool:
- in-stream, immediately on the tool's `tool_call_output_item` event — the
  tab fills the moment that sub-agent finishes (predict results appear while
  diagnose is still running, etc.); the status log notes
  "-- <tool> results shown in tab";
- in the final pass with `reapply=True` for simulate/recommend/email, whose
  display gains the master-written narrative note (predict/diagnose need no
  second pass — their re-apply is a no-op via the `applied_tools` set).
The old duplicated final-pass processing and tab-persistence blocks were
removed; `_apply_payload` is now the single place where payloads become tabs.

**Tool-logging question answered + dead code removed.** The master's event
stream only carries the master's own tool calls (the five sub-agent
wrappers) — all are logged by the generic `tool.call.completed` line. The
`is_mcp_tool` branch that emitted `mcp.tool.call.completed` could never fire:
it matched the INNER MCP tool names (predict_delivery_delays,
get_delay_diagnosis, simulate_order_delays), which never appear at master
level because `as_tool` does not propagate nested sub-agent events. Inner MCP
tools and @tools (recommend_actions, fetch_delayed_orders_for_email)
self-log start/completion/duration from their own code to the same log file.
Removed: the dead branch, `mcp_tool_names`, the `is_mcp_tool` tuple element,
and the now-unused `PIPELINE_TOOL_NAMES` import (the constant remains in
delivery_agents.py for the MCP tool_filter).

---

## 18. Operator-visible warnings, richer recommend logging, CrewAI heading cleanup (2026-07-05)

**Run-warnings channel.** Several failure modes degraded silently — the run
looked successful and only the log file knew: unparseable sub-agent payloads
(tab silently not updated), rows dropped by Pydantic validation, RAG/SLA
retrieval failures folded into tool text. The chat handler now collects
`run_warnings` and appends a "Warnings -- some steps degraded" block to the
final chat reply (with the log file name for the operator), also logged as
`run.warnings`. Sources wired in: `tool.payload.unparseable`,
`tool.rows.dropped` (per-key counts via the `_rows` helper inside
`_apply_payload`). Hard errors now also point to the log file.
`recommendation.md` gained a Degraded-Input Handling section: when the tool
output carries a "[RAG] Failed" note, sla_reference must say "SLA context
unavailable -- retrieval failed" instead of inventing quotes.

**recommend_actions logging** now matches the other tools' step-level style:
WARNING-level logs for missing prerequisites, and a
`tool.recommend_actions.data` line (dims compared, hist/daily high-risk
counts, hotspots, output size) between started/completed.

**CrewAI heading cleanup.** All six sub-agent prompts renamed:
`## Role` → `## Purpose`, `## Goal` → `## Objective`,
`## Backstory` → `## Context`. Safe because the WYSIWYG loader no longer
parses section names (the old Role/Goal/Backstory parser was removed in Section 2);
verified no CrewAI headings remain and assembled prompts contain the new
sections. App README updated.

---

## 19. Loader made fully uniform — master layering via @includes (2026-07-05)

User question: with @includes available, why does get_instruction() still
special-case the master? It shouldn't. `master_expert.md` now begins with
`@security_guardrails` and `@chatbot_behavior` directives, and
`get_instruction()` is one uniform rule for every agent: read the .md file,
expand includes. `_MASTER_LAYERS` / `_LAYER_SEPARATOR` and the master branch
are gone; load_config.py is now 61 lines.

Why keep get_instruction() at all: something must resolve prompt keys to
files (shared/ before agents/), perform the include expansion, and give
delivery_agents.py a single call point with a clear error when a prompt file
is missing. That is now its entire job.

Benefit beyond simplicity: the security → behaviour → expert precedence is
now VISIBLE in the prompt file itself rather than hidden in Python.
Verified: assembled master contains each layer exactly once, in order, with
no unexpanded directives; all other agents unchanged.

---

## 20. Helper dead-code audit (2026-07-05)

Full reference sweep of every function and constant in `helpers/app_utils.py`,
`helpers/post_processing.py`, `helpers/logging_utils.py`, and the module-level
helpers in `delivery_chat_app.py`, checked against the whole project including
tests/ and evals/.

**Result: zero dead functions.** Every helper is referenced at least once;
the "internal-only" ones are the plan-builder internals (_detect_tools,
_merge_steps, regex tables), glossary/formatting internals (_norm_id,
_strip_leading_heading, _ensure_output_dir), and stream-parsing helpers
(_tool_json, _validated_rows) — all with active call sites.

Two zero-value abstractions folded in:
- `clarification_message()` (a function returning a constant string) →
  module constant `CLARIFICATION_MSG`.
- `_usage_from_container()` (one-line dict/attr lookup used once) → inlined
  into `extract_usage_from_event()`.
Verified: routing behaviour unchanged, usage extraction works for dict and
object SDK shapes, no stale references remain.

---

## 21. Eval instructions audit — docs matched to actual behaviour (2026-07-05)

Audit of the eval run instructions in README and docs/21 against
`run_evals.py` / `pytest.ini` / the test files found three mismatches:

1. **RAGAS gating was fiction.** `pytest.ini` claimed ragas-marked tests are
   excluded from default runs and `run_evals.py` offered `--ragas` — but NO
   test carries the marker; `test_eval_rag.py` explicitly runs in the default
   suite. The flag and the `-m "not ragas"` filter were no-ops. Removed both
   and the stale marker registration; docs now state RAGAS runs as part of
   the standard suite.
2. **`--agent master` was broken.** `test_eval_master.py` was deleted at some
   point (pytest cache remnants confirm it once ran) but the runner still
   offered the option — it would fail with "file not found". Entry removed.
3. **Output listings were incomplete.** README/docs/21 now list everything a
   run writes: eval_report_<ts>.md, judge_scores_<ts>.json,
   judge_scores_latest.json (merged; feeds the human baseline),
   human_baseline_report_<ts>.md, and the raw pytest JSON.

---

## 22. Predict judge-score split + RAG eval redesign (2026-07-12)

**Part A — predict summary was crowding out per-row insights in the report.**
`test_eval_predict.py`'s judge call combined the verbose `predict_summary`
(~2500 chars) with 5 rows of per-delivery `llm_insights` into one text blob;
`conftest.py`'s 3000-char report-display truncation let the summary consume
nearly the whole budget, so the generated report showed the summary and cut
off after the first insight row. Fixed by splitting into two separate judge
calls (`test_predict_summary_judge_score` / `test_predict_insights_judge_score`,
agent names `"Predict Delivery Delays — Summary"` / `"— LLM Insights"`), and
raising the truncation limit 3000/4000 → 5500 chars in both `conftest.py`
(report display) and `judge.py` (judge input prompt) so nothing gets cut off.
Added `judge.grouped_scores()` — collapses records sharing a `" — "`-prefixed
base agent name back into one averaged row for the top-level report table and
`judge_scores_latest.json`, so `test_eval_human_baseline.py`'s lookup by plain
agent name (`"Predict Delivery Delays"`) still matches. Net eval count: 43 → 44.

**Part B — RAG eval redesigned from one blended sample to per-topic sampling.**
`test_eval_rag.py` previously ran RAGAS on a single `n=1` sample: every
`sla_reference` citation concatenated into one response, scored against one
shared retrieved-context block. Rerunning with zero code changes showed
faithfulness swinging ~0.80–0.94 — expected for a single-sample LLM-judge
metric, but indistinguishable from a real regression when read as one number.

Two retrieval-depth hypotheses were tested and rejected before settling on the
real fix: narrowing the final cross-encoder rerank stage from top-8 to top-5
(single run: 0.786, no better than baseline) and broadening upstream retrieval
from `TOP_K=15/HYBRID_PRE_FILTER_N=12` to `20/16` (two runs: 0.696 and 1.000 —
wider spread, not narrower). Both reverted; `rag_knowledge.py`'s retrieval
constants are unchanged from the original committed values.

Root insight (repo owner's call): the recommendation agent's own instruction
never varies run to run, so varying *that* query gives RAGAS nothing to
average over. Its **output**, however, naturally cites several distinct SLA
topics per run. Fix: `retrieve_sla_context()` gained an optional
`query_override` param (production call sites don't pass it — behavior
unchanged); the eval now samples up to 2 `recommended_actions` per category
(quick-win/short-term/long-term), issues one independently-retrieved query per
topic, and builds one RAGAS sample per topic (n≈6) instead of one blended
sample — plus a per-topic breakdown table (category, dimension, action,
faithfulness, relevancy) in the report.

This didn't eliminate run-to-run variance in the aggregate (observed ~0.76–0.91
faithfulness across repeated runs since the redesign) — RAGAS's per-claim
judging noise persists at n≈6. What changed is diagnostic: the report now
names which specific topic scored low on a given run (not the same topic each
time), instead of hiding that signal inside one opaque blended score.

**Incidental fix, same session:** `conftest.py`'s RAG→Recommendation merge
(folds RAGAS scores into the `Recommendation Expert Agent` row when both tests
run in one session) only carried over the numeric `ragas_scores`, silently
dropping the new per-topic breakdown text on full-suite runs. Extended to
append the breakdown into the merged record's output too.

**Unrelated, same session:** `tests/conftest.py`'s `pytest_terminal_summary`
hook wrote `smoke_test_report.md` unconditionally, including when zero tests
actually ran (e.g. `pytest tests/ --collect-only`) — overwriting a real
40/40 report with a bogus all-zero one. Added a guard: if no tests ran, leave
the existing report untouched.

Docs updated to match: `README.md` Section 20.2 (predict averaging note), Section 20.3 and
Section 21.5/21.6 (RAG eval numbers and per-topic design), and `docs/21-eval-flow-design.md`
(Section 6 rewritten; stale `ragas`-marker/gating text left over from Section 21's audit
also cleaned up; dated update note added).

