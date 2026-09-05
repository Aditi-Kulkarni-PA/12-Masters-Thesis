"""Score a completed run's OUTPUT QUALITY and persist it (T42).

    uv run python supply_chain_topology_app/score_topology_run.py <run_id|latest>
    uv run python supply_chain_topology_app/score_topology_run.py --all-unscored
    uv run python supply_chain_topology_app/score_topology_run.py latest --dry-run

Why this exists
---------------
Every run so far reported cost and latency against no quality axis at all, which makes
"monolith is cheaper and faster" unfalsifiable — a condition that skipped work entirely
would win on both. `quality_scores` has existed since T14 and had zero rows.

Reads from the store, never re-runs the topology
------------------------------------------------
`tool_call.output_text` and `run.all_turns_json` are already persisted, so scoring is a
pure read of a completed run. That matters for three reasons: scoring costs no API spend
on the expensive model, a locked run can be scored without being disturbed, and a scoring
bug can be fixed and re-run against the same bytes rather than against a fresh execution
that would differ.

Two places a capability's structured result can live (T42c)
--------------------------------------------------------------
For Planner-Executor/Swarm, a capability's own structured result (predict_summary +
enriched delayed_orders, simulations with simulate_delay_reason, scored
recommended_actions, rendered emails) IS the wrapped sub-agent's return value, captured
directly as that tool call's output_text.

Monolith has no such capture point — it calls the same raw tools, which return
deliberately unenriched data (empty llm_insights, no simulate_delay_reason column at
all, raw unscored text for recommend/email). `core/schemas.py` MonolithOutput gives
monolith's own final answer the same fields instead, so for monolith this data (when
the model actually writes it — see output_contract_monolith.md) lives in
`run.all_turns_json`, not in any tool_call.output_text.

Every extractor below therefore checks BOTH sources for its capability's field(s) —
`_pick()` prefers the tool's own structured value, then checks the coordinator's own
final answer — so a Planner-Executor run and a Monolith run are read the same way
without either one needing special-casing at the call site.

Every capability is judged as TWO artifacts blended into ONE capability-level score
rather than one line item (see _CAPABILITY_BLEND) — NOT a primary-plus-fallback
relationship: whichever of the two the coordinator actually returned this run is
scored at its own fixed, permanent weight, and the pair never coexist for one run:
  predict         = 50% predict_summary (the narrative paragraph) + 50% a 5-row sample
                    of delayed_orders[].llm_insights (per-order grounded reasoning).
  simulate        = 20% simulate_narrative (simulate_summary) + 80% a 5-row sample of
                    simulations[].simulate_delay_reason.
  diagnose        = 80% diagnosis_summary (the written analysis) + 20% the raw
                    diagnose tool's own two DB tables, whenever that's what was
                    returned instead (T42h, decided 23-Aug-26).
  recommendation  = 10% recommendation_summary (short wrapper) + 70% LLM-scored
                    recommended_actions + 20% the raw DB/RAG tool text, whenever
                    that's what was returned instead (T42f/T42g).
  email           = 10% email_alert_summary (short wrapper) + 70% LLM-rendered
                    emails + 20% the raw DB tool text, whenever that's what was
                    returned instead (T42f/T42g).
Sampling 5 rows rather than judging all ~50 keeps judge cost and context bounded while
still catching templated/repetitive per-row writing (see _sample_indices — evenly
spread, deterministic, no RNG/seed to persist).

Splitting recommendation_summary/email_alert_summary out of the old coordinator_
narrative bucket (T42f) matters because each capability's narrative half must be
judged against ITS OWN capability, the same as predict_summary counts toward predict —
lumping it into a generic bucket instead would mean that half of the capability's
output was never actually scored as belonging to it. Splitting diagnose/recommendation/
email's LLM-inference vs raw-DB halves (T42g/T42h) matters because scoring whichever
one was returned at the SAME weight let a run that only ever produced raw retrieval
score as if it had done the real analytical work.

What's left after predict/diagnose/simulate/recommendation/email each have their own
narrative accounted for is just chat_response, merged across every turn and judged on
its own as coordinator_narrative. Per the prompts, chat_response is left empty on a
real analysis turn, so this is usually correctly excluded rather than present.

Built from `run.all_turns_json`, not `run.final_answer` alone. A condition that finishes
its work inside turn 1 (observed on monolith 23-Aug-26: all 5 tools ran during TURN 1,
turn 2 was just "already completed...") leaves its real synthesis on an earlier turn
while `final_answer` — the last turn — is a near-empty confirmation. For each field this
takes the longest non-empty value across every turn, so whichever turn actually wrote it
is the one that gets judged.

Excluded: Mesh has no MasterOutput at all (R17), and Sequential/Static-Graph DAG build
theirs from code rather than an LLM call — for those, the narrative artifacts are
skipped (nothing to judge) rather than scored against a schema they don't use.

judge_mean is a WEIGHTED mean across dimensions, not a plain average — see _DIM_WEIGHTS.

Judge is `gpt-4.1-mini`, fixed, via `evals/judge.py` — the same judge that produced the
T33 parity baseline, so scores here are on the same scale as that reference.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))
_REPO_ROOT = _APP_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from measurement.run_store_schema import DB_PATH, migrate_schema
from measurement.dependencies import canonical
from measurement.run_store_writer import _get_implied_tools

# canonical tool name -> the _CAPABILITY_BLEND key it corresponds to. Used only to map
# query_metadata.implied_tools_json (T42i) onto capability_scores' keys below.
_TOOL_TO_CAPABILITY: dict[str, str] = {
    "predict_delivery_delays_tool": "predict",
    "diagnose_delay_patterns_tool": "diagnose",
    "delay_simulations_tool": "simulate",
    "recommendation_tool": "recommendation",
    "email_alert_tool": "email",
}

# Per-artifact guidance for the judge. Held identical across conditions: the criteria
# describe the DOMAIN task, never the topology, so no condition is judged against a
# standard another one was not held to.
_CRITERIA = {
    "predict_summary": (
        "The cross-dimensional narrative paragraph accompanying today's delay "
        "predictions. Look for: a stated count of delayed orders, a severity "
        "breakdown, and per-dimension detail (region, weather, partner, mode) — "
        "synthesis across dimensions, not a restated count. Claims must match the "
        "numbers present in the output."),
    "predict_row_insights_sample": (
        "A sample of per-order explanations (one LLM-written sentence or two per "
        "delayed order, meant to reference derived features such as schedule_risk, "
        "vehicle_load_strain, km_per_expected_hr, or vehicle_type). Look for: each "
        "explanation grounded in that specific order's own factors rather than a "
        "generic templated sentence, and no two sampled rows reading as near-"
        "identical boilerplate."),
    "diagnose_delay_patterns_tool": (
        "Root-cause diagnosis of today's delays against historical baselines. Look for: "
        "dimension-by-dimension comparison, identified high-risk pattern combinations, "
        "and today-vs-historical framing rather than today's numbers alone."),
    "diagnose_delay_patterns_tool_raw": (
        "The raw DB-computed diagnosis tables (today's stats, historical baseline) — "
        "scored this way whenever the coordinator returned this instead of a written "
        "diagnosis_summary. Judge the underlying retrieved content only: is it "
        "relevant, coherent DB output, not fabricated? Do NOT expect a written "
        "comparison, identified patterns, or synthesis here — that is what "
        "diagnose_delay_patterns_tool covers when it exists."),
    "simulate_narrative": (
        "The coordinator's narrative summary of the what-if simulation. Look for: the "
        "scenario stated in plain terms, an aggregate read of how severity shifted "
        "across affected orders (not just repeating one row), and framing that "
        "follows from the SIMULATED conditions rather than generic hedging."),
    "simulate_row_reasons_sample": (
        "A sample of per-order reasons for the simulated delay under the what-if "
        "scenario. Look for: the reason tied to the SIMULATED conditions actually "
        "applied (the weather/region/vehicle change), not a restatement of the "
        "order's original delay cause, and no two sampled rows reading as near-"
        "identical boilerplate."),
    "recommendation_tool": (
        "Optimisation recommendations across quick-win, short-term and long-term "
        "horizons. Look for: actions tied to specific evidence in the data, and service-"
        "level references where cited. Generic advice unconnected to the data scores low."),
    "recommendation_tool_raw": (
        "The raw DB/RAG-retrieved candidate data behind recommendations — scored this "
        "way whenever the coordinator returned this instead of LLM-scored "
        "recommended_actions. Judge the underlying retrieved content only: is it "
        "relevant SLA/order data, not fabricated? Do NOT expect scored actions, "
        "evidence citations, or synthesis here — that is what recommendation_tool "
        "covers when it exists."),
    "recommendation_summary": (
        "The coordinator's 2-3 sentence wrapper narrative over the recommended actions "
        "(overall optimisation approach and key themes, not a restatement of each "
        "action). Look for: a genuine synthesis consistent with the actions actually "
        "produced, not generic advice that could apply to any run."),
    "email_alert_tool": (
        "Customer delay notification emails. Look for: severity-appropriate tone, the "
        "order's actual details, and no invented commitments (compensation, guaranteed "
        "times) that the data does not support."),
    "email_alert_tool_raw": (
        "The raw DB-retrieved delayed-order data behind customer emails — scored this "
        "way whenever the coordinator returned this instead of rendered emails. Judge "
        "the underlying retrieved content only: is it relevant, not fabricated? Do NOT "
        "expect rendered customer-facing email copy here — that is what "
        "email_alert_tool covers when it exists."),
    "email_alert_summary": (
        "The coordinator's one-line status on the customer emails generated (how many, "
        "broken down by severity template — or the correct 'no delayed orders' message "
        "when prediction found none). Look for: the stated counts/breakdown being "
        "consistent with the emails actually produced, not a vague or generic line."),
    "coordinator_narrative": (
        "The coordinator's own conversational answer (chat_response) for the parts of "
        "the request that are not predict, diagnose, simulate, recommendation, or "
        "email — recommendation_summary and email_alert_summary are judged with their "
        "own capabilities, and simulate_summary is judged separately as "
        "simulate_narrative. Look for: a genuine operational read, specific figures "
        "consistent with a real run having happened, and no claims that outrun what "
        "the tool calls could actually have produced."),
}

# Every field a coordinator's own final answer might carry, across every schema in use
# (MasterOutput's 4 narrative fields, plus MonolithOutput's 8 capability-result fields
# — core/schemas.py). Merged uniformly regardless of type: len() orders both strings
# and lists, so "longest non-empty value across every turn" needs no special-casing.
_MERGE_FIELDS = (
    "chat_response", "simulate_summary", "recommendation_summary", "email_alert_summary",
    "predict_summary", "delayed_orders",
    "diagnosis_summary", "high_risk_patterns", "comparison",
    "simulations", "recommended_actions", "content",
    # EmailCounts (core/schemas.py), carried by MonolithOutput and by the email
    # specialist's EmailsList: the full emailed total and its per-severity split, which
    # are NOT recoverable by counting `content` (that holds only a few samples).
    "total_orders_emailed", "template_breakdown",
)

# coordinator_narrative is scoped down to chat_response ONLY. recommendation_summary
# and email_alert_summary each moved into their own capability's blend below (T42f) —
# they are that capability's narrative gloss, the same role predict_summary/
# simulate_summary play for predict/simulate, not a leftover to lump into a generic
# bucket. Folding them into an undifferentiated "coordinator_narrative" alongside
# chat_response meant recommendation/email's own narrative half was never actually
# scored against ITS capability, and a synthetic 6th "capability" (not one of the 5
# real domain capabilities) diluted the run's mean. Per the prompts (output_contract.md,
# output_contract_monolith.md), chat_response is left empty on a real analysis turn, so
# this is usually absent (excluded, not scored 0 — see _blend()) rather than diluting.
_COORDINATOR_NARRATIVE_FIELDS = ("chat_response",)

# Some capabilities are TWO judged artifacts (a narrative and a sample of the
# underlying per-row reasoning, OR a narrative and the structured deliverable) blended
# into ONE capability-level score, rather than each artifact counting as its own
# equally-weighted line item in the run's overall mean. Weights reflect where the real
# signal is: predict's summary and its per-row grounding matter equally (50/50);
# simulate's per-row reasons are where "did the model actually reason about the
# SIMULATED conditions" shows up, so they dominate over the shorter coordinator gloss
# (20/80); recommendation_summary and email_alert_summary are explicitly a short
# wrapper/status line over their own structured deliverable ("do not restate the
# actions" / one line of counts) — decided 23-Aug-26 (user) at 10/90, weighted down
# further than simulate's narrative since there is even less independent content to
# judge.
#
# diagnose_delay_patterns_tool / recommendation_tool / email_alert_tool each further
# split into an LLM-inference-vs-raw-DB pair (T42g/T42h, decided 23-Aug-26, user): the
# extractor already returns EITHER the LLM-written result (diagnosis_summary /
# LLM-scored recommended_actions / rendered emails) OR the raw DB/RAG-only tool text,
# per run — the two never coexist for one run, so this is not a "primary vs
# fallback" relationship; it is a permanent two-way scoring rule, and whichever one
# the coordinator actually returned is scored at its own fixed weight every time.
# Weighted so the LLM-authored analytical work dominates at 70-80% while the raw
# DB/RAG artifact (diagnose_delay_patterns_tool_raw / recommendation_tool_raw /
# email_alert_tool_raw) is worth 20-30% — real credit for at least getting a genuine
# tool response back, per the user ("at least it ensures we got a tool response;
# higher weightage is for inference") — but a run that only ever produced raw
# retrieval, never real analysis, still cannot climb past that ceiling, because the
# dominant "inference" share is MISSING and scores a hard zero under _blend() (same
# "missing part of an attempted capability = 0" rule as predict/simulate) — it is
# never both directions at once, so this is not a renormalization trick, just the
# existing rule applied to a second axis of the same capability.
_CAPABILITY_BLEND: dict[str, tuple[tuple[str, float], ...]] = {
    "predict":               (("predict_summary", 0.5), ("predict_row_insights_sample", 0.5)),
    "diagnose":               (("diagnose_delay_patterns_tool", 0.8), ("diagnose_delay_patterns_tool_raw", 0.2)),
    "simulate":               (("simulate_narrative", 0.2), ("simulate_row_reasons_sample", 0.8)),
    "recommendation":         (("recommendation_summary", 0.1), ("recommendation_tool", 0.7),
                               ("recommendation_tool_raw", 0.2)),
    "email":                  (("email_alert_summary", 0.1), ("email_alert_tool", 0.7),
                               ("email_alert_tool_raw", 0.2)),
    "coordinator_narrative":  (("coordinator_narrative", 1.0),),
}

# judge_mean is a WEIGHTED mean, not a plain average of the three dimensions. Relevance
# and faithfulness are what the topology comparison is actually about — did it address
# what was asked, grounded in real data — so they carry equal, dominant weight. Safety
# is a pass/fail guard against harmful/misleading content in a low-risk operational
# domain (delivery delay ops), not a differentiator between topologies that are all
# already well clear of it (every score so far is 4.0-5.0), so it is weighted down
# rather than dropped -- a genuine safety failure should still move the mean.
_DIM_WEIGHTS = {"relevance": 0.45, "faithfulness": 0.45, "safety": 0.10}

_MAX_CHARS = 5500          # judge_output truncates at this; truncate here so it is visible
_SAMPLE_N = 5


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _pick_run(c, run_id: str):
    if run_id == "latest":
        return c.execute("SELECT * FROM run ORDER BY started_at DESC LIMIT 1").fetchone()
    return c.execute("SELECT * FROM run WHERE run_id LIKE ?", (run_id + "%",)).fetchone()


def _sample_indices(n: int, k: int = _SAMPLE_N) -> list[int]:
    """k evenly-spaced indices across n items, deterministic — no RNG, no seed to
    persist. Spread across the list rather than "first k" so the sample represents
    the whole run (e.g. every region/weather combination the day produced), not just
    whichever rows the model or the CSV happened to put first. Returns every index if
    n <= k."""
    if n <= k:
        return list(range(n))
    if k <= 1:
        return [0]
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def _parse_leading_json(payload: str) -> dict | None:
    """Parse a tool payload that may be raw-MCP-shaped: a JSON object followed by a
    second, text-wrapped copy (same transport quirk measurement/instrumentation.py's
    is_json() works around). Plain json.loads() raises "Extra data" on that shape."""
    if not payload:
        return None
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        obj, _ = json.JSONDecoder().raw_decode(payload.lstrip())
        return obj
    except Exception:
        return None


def _pick(tool_data: dict | None, coord_data: dict, key: str):
    """Prefer the tool call's own structured value for `key`; otherwise check the
    coordinator's own field of the same name (the MonolithOutput case — see module
    docstring). Returns None if neither source has anything."""
    for src in (tool_data, coord_data):
        if not src:
            continue
        val = src.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, list) and val:
            return val
    return None


def _pick_row_field(tool_data: dict | None, coord_data: dict, list_key: str, row_field: str) -> list[str]:
    """Like _pick(), but for a per-row field inside a list -- and unlike _pick(),
    checks that the field is actually POPULATED, not just that the list exists.

    _pick() alone is wrong here: the raw predict/simulate tools return delayed_orders/
    simulations with the right SHAPE (list_key present, right length) but every row's
    llm_insights/simulate_delay_reason hardcoded empty -- "filled by the predict/
    simulate agent" (prediction_pipeline/src/daily_predict.py). _pick() sees a non-
    empty LIST and stops there, never reaching the coordinator's own enriched version
    (confirmed 23-Aug-26, run 1d267c8e: delayed_orders had real llm_insights in
    all_turns_json, but scoring reported predict_row_insights_sample NOT SCORED,
    because _pick() had already returned the tool's 50 empty-insight rows).

    Tries each source in turn and only accepts one whose rows actually have the field.
    """
    for src in (tool_data, coord_data):
        if not src:
            continue
        rows = src.get(list_key) or []
        values = [str(r.get(row_field) or "").strip()
                 for r in rows if isinstance(r, dict) and r.get(row_field)]
        if values:
            return values
    return []


def _extract_predict_artifacts(tool_data: dict | None, coord_data: dict) -> dict[str, str]:
    """predict has TWO distinct LLM-written things: one narrative paragraph
    (predict_summary) and one short explanation per delayed order
    (delayed_orders[].llm_insights, up to enrich_rows_cap rows — 50 by default).
    Splitting them lets each be judged on what it actually is: a synthesis vs. a
    sample of grounded per-row reasoning, rather than one truncated blob that either
    cuts off before most rows are reached or buries the paragraph in per-row JSON."""
    out: dict[str, str] = {}
    summary = _pick(tool_data, coord_data, "predict_summary")
    if summary:
        out["predict_summary"] = summary

    insights = _pick_row_field(tool_data, coord_data, "delayed_orders", "llm_insights")
    if insights:
        idx = _sample_indices(len(insights))
        out["predict_row_insights_sample"] = "\n\n".join(
            f"[order {i + 1}/{len(insights)}] {insights[i]}" for i in idx)
    return out


def _extract_simulate_artifact(tool_data: dict | None, coord_data: dict) -> dict[str, str]:
    """simulate's only free text is simulations[].simulate_delay_reason, one per
    simulated row, sampled the same way as predict's llm_insights and for the same
    reason: a truncated blob of simulation-row JSON is not a deliberate sample."""
    out: dict[str, str] = {}
    reasons = _pick_row_field(tool_data, coord_data, "simulations", "simulate_delay_reason")
    if reasons:
        idx = _sample_indices(len(reasons))
        out["simulate_row_reasons_sample"] = "\n\n".join(
            f"[order {i + 1}/{len(reasons)}] {reasons[i]}" for i in idx)
    return out


def _extract_diagnose_artifact(payload_raw: str, tool_data: dict | None, coord_data: dict) -> dict[str, str]:
    """diagnose's real work IS diagnosis_summary. Despite its schema field description
    calling it a "summary", in practice it is a substantial analysis (dimension-by-
    dimension comparison, identified high-risk pattern combinations), typically
    2-4k chars — not a short wrapper like recommendation_summary/email_alert_summary
    (T42h, user, 23-Aug-26: "not a 2-3 statement summary... a good analysis with
    insights on patterns").

    diagnose_delay_patterns_tool (narrative) and diagnose_delay_patterns_tool_raw
    (the raw diagnose tool's two computed DB tables: today's stats, historical
    baseline) are mutually exclusive by construction, same T42g pattern as
    recommendation/email's split — revised 23-Aug-26 (user) from an earlier forced-
    zero design to this weighted split instead: "at least it ensures we got a tool
    response; higher weightage is for inference." A run getting at least real DB
    numbers back still earns partial credit; genuine LLM analysis still dominates."""
    out: dict[str, str] = {}
    summary = _pick(tool_data, coord_data, "diagnosis_summary")
    if summary:
        out["diagnose_delay_patterns_tool"] = summary
    elif payload_raw:
        out["diagnose_delay_patterns_tool_raw"] = payload_raw
    return out


def _extract_recommendation_artifact(payload_raw: str, tool_data: dict | None, coord_data: dict) -> dict[str, str]:
    """Prefer the structured recommended_actions list (PE: already the wrapped sub-
    agent's own return value; Monolith: MonolithOutput.recommended_actions), formatted
    so each action's category/evidence/SLA citation is legible to the judge. Otherwise
    uses the tool's raw text when neither source has the structured list — not a
    fallback, a second, permanently-weighted scoring artifact in its own right (T42g).

    Also pulls recommendation_summary (the coordinator's own wrapper narrative, from
    merged_fields only — no tool ever produces it) as its own artifact, so it is judged
    against ITS capability's blend rather than merged into coordinator_narrative
    (T42f).

    recommendation_tool (LLM-scored) and recommendation_tool_raw (raw DB/RAG) are
    mutually exclusive by construction — whichever one the coordinator actually
    returned this run — so the capability blend's inference-vs-raw weighting (T42g)
    always has exactly one of the two to weight, never both at once."""
    out: dict[str, str] = {}
    actions = _pick(tool_data, coord_data, "recommended_actions")
    if actions:
        lines = [
            f"[{a.get('category', '?')}/{a.get('dimension', '?')}] {a.get('action', '')}: "
            f"{a.get('action_desc', '')}  (evidence: {a.get('supporting_data', '')}; "
            f"SLA: {a.get('sla_reference', '')})"
            for a in actions if isinstance(a, dict)
        ]
        if lines:
            out["recommendation_tool"] = "\n\n".join(lines)
    if "recommendation_tool" not in out and payload_raw:
        out["recommendation_tool_raw"] = payload_raw

    summary = coord_data.get("recommendation_summary") if coord_data else None
    if isinstance(summary, str) and summary.strip():
        out["recommendation_summary"] = summary.strip()
    return out


def _extract_email_artifact(payload_raw: str, tool_data: dict | None, coord_data: dict) -> dict[str, str]:
    """Prefer the structured rendered-emails list (PE: the wrapped sub-agent's own
    return value; Monolith: MonolithOutput.content). Otherwise uses the tool's raw
    text when neither source has it — not a fallback, a second, permanently-weighted
    scoring artifact in its own right (T42g).

    Also pulls email_alert_summary (the coordinator's own one-line status, from
    merged_fields only) as its own artifact, judged against ITS capability's blend
    rather than merged into coordinator_narrative (T42f).

    email_alert_tool (LLM-rendered) and email_alert_tool_raw (raw DB) are mutually
    exclusive by construction, same reasoning as recommendation's split (T42g)."""
    out: dict[str, str] = {}
    emails = _pick(tool_data, coord_data, "content")
    if emails:
        lines = [str(e.get("email_content", "")).strip()
                for e in emails if isinstance(e, dict) and e.get("email_content")]
        if lines:
            out["email_alert_tool"] = "\n\n---\n\n".join(lines)
    if "email_alert_tool" not in out and payload_raw:
        out["email_alert_tool_raw"] = payload_raw

    summary = coord_data.get("email_alert_summary") if coord_data else None
    if isinstance(summary, str) and summary.strip():
        out["email_alert_summary"] = summary.strip()
    return out


def _merge_all_turns(all_turns_json: str | None) -> dict[str, str | list]:
    """Longest non-empty value per output field, across every turn.

    Not "last turn wins" and not "first turn wins" — a condition may write a field on
    ANY turn (monolith wrote simulate/recommendation/email_alert_summary on turn 1,
    then a bare confirmation on turn 2). Taking the longest non-empty candidate per
    field is the same rule score_run() already applies to an artifact produced by more
    than one call, applied here per-field instead of per-call, and uniformly across
    str and list fields (len() works for both).

    Returns {field: value} for whichever of _MERGE_FIELDS had content on some turn;
    empty dict if none did (no structured output at all, or nothing was ever written).
    """
    if not all_turns_json:
        return {}
    try:
        turns = json.loads(all_turns_json)
    except (json.JSONDecodeError, TypeError):
        return {}

    merged: dict[str, str | list] = {}
    for turn_text in turns:
        try:
            obj = json.loads(turn_text)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        for field in _MERGE_FIELDS:
            val = obj.get(field)
            if isinstance(val, str):
                val = val.strip()
            if not val:
                continue
            cur = merged.get(field)
            if cur is None or len(val) > len(cur):
                merged[field] = val

    return merged


def _blend(results: dict, parts: tuple[tuple[str, float], ...]) -> dict | None:
    """Combine one or more judged artifacts into one capability-level {relevance,
    faithfulness, safety} score, using the stated weights.

    A missing part of an ATTEMPTED capability scores ZERO for its weighted share, not
    a renormalized skip. Decided explicitly (23-Aug-26, real run 1d267c8e): predict ran
    (predict_summary was written) but delayed_orders[].llm_insights was not filled in
    for a single row despite output_contract_monolith.md instructing it -- that is a
    genuine quality failure (the model was told to do the work and didn't), not a
    structural absence. Renormalizing to 100% weight on predict_summary would have
    scored that run 5.0/5.0/5.0 on "predict" and hidden the failure entirely; scoring
    it 0.5*5.0 + 0.5*0 = 2.5 is what the failure is actually worth.

    Only a capability with NO part present at all is excluded (returns None) rather
    than scored zero -- that is "never attempted this turn" (a query that didn't ask
    for simulate, or Mesh/Sequential/DAG which don't produce these artifacts at all),
    which must stay uncounted rather than punished; see "condition that skips work
    does not gain a quality advantage" below for the companion rule on the other side.
    """
    present = [(k, w) for k, w in parts if k in results]
    if not present:
        return None
    dims = ("relevance", "faithfulness", "safety")
    total_w = sum(w for _, w in parts)   # every part's weight, present or not
    return {d: sum((results[k][d] if k in results else 0.0) * w for k, w in parts) / total_w
           for d in dims}


def score_run(run_id: str, dry_run: bool = False, force: bool = False) -> int:
    from evals.judge import judge_output

    # Ensure all_turns_json (T42b) exists before reading it back -- a standalone
    # invocation against a DB no current-code write_run() call has touched yet would
    # otherwise silently skip the narrative artifacts rather than finding the column.
    migrate_schema(DB_PATH)

    with _conn() as c:
        row = _pick_run(c, run_id)
        if row is None:
            print(f"No run matching {run_id!r}. Use report_topology_run.py --list.")
            return 1
        rid = row["run_id"]
        already = c.execute("SELECT 1 FROM quality_scores WHERE run_id=?", (rid,)).fetchone()
        if already and not force:
            print(f"{rid[:8]} already scored. Use --force to re-score.")
            return 0
        calls = [dict(r) for r in c.execute(
            "SELECT tool_name, output_text, error, empty_payload FROM tool_call "
            "WHERE run_id=? ORDER BY call_order", (rid,))]
        # T42i: which capabilities the query itself implied, fetched here while c is
        # still open. None (not an empty set) means query_metadata has no entry for
        # this query_id yet -- distinguished below from "implied nothing".
        implied_tools = _get_implied_tools(c, row["query_id"]) if row["query_id"] else None

    print(f"\nScoring {rid[:8]}  topology={row['topology']}  model={row['model']}")
    print(f"  judged artifact: up to {len(_CRITERIA)} artifacts, blended into "
          f"{len(_CAPABILITY_BLEND)} capability-level scores (T42c)\n")

    # Keep the longest successful raw payload per capability (a reflect-retry can call
    # a tool twice), parsed once. Neither the raw text nor the parse is thrown away --
    # extraction below needs both: the parsed structured fields when present, and the
    # raw text itself as its own permanently-weighted scoring artifact when not (T42g).
    tool_payloads: dict[str, tuple[str, dict | None]] = {}
    for call in calls:
        cap = canonical(call["tool_name"])
        payload = (call["output_text"] or "").strip()
        if call["error"] or not payload:
            continue
        prev = tool_payloads.get(cap)
        if prev is not None and len(prev[0]) >= len(payload):
            continue
        tool_payloads[cap] = (payload, _parse_leading_json(payload))

    # The coordinator's own final answer, merged across every turn -- the source every
    # extractor below also checks when a tool's own payload doesn't have the
    # structured field (the Monolith case; see module docstring).
    merged_fields = _merge_all_turns(row["all_turns_json"] if "all_turns_json" in row.keys() else None)

    best: dict[str, dict] = {}

    def _add(extracted: dict[str, str]) -> None:
        for key, text in extracted.items():
            # Prefer the longest successful text for an artifact produced more than once.
            if key in best and len(best[key]["payload"]) >= len(text):
                continue
            best[key] = {"payload": text}

    pred_raw, pred_data = tool_payloads.get("predict_delivery_delays_tool", ("", None))
    _add(_extract_predict_artifacts(pred_data, merged_fields))

    _, sim_data = tool_payloads.get("delay_simulations_tool", ("", None))
    _add(_extract_simulate_artifact(sim_data, merged_fields))

    diag_raw, diag_data = tool_payloads.get("diagnose_delay_patterns_tool", ("", None))
    _add(_extract_diagnose_artifact(diag_raw, diag_data, merged_fields))

    rec_raw, rec_data = tool_payloads.get("recommendation_tool", ("", None))
    _add(_extract_recommendation_artifact(rec_raw, rec_data, merged_fields))

    email_raw, email_data = tool_payloads.get("email_alert_tool", ("", None))
    _add(_extract_email_artifact(email_raw, email_data, merged_fields))

    # simulate_narrative is judged on its own (see _CAPABILITY_BLEND); the rest of the
    # leftover narrative fields are judged together as coordinator_narrative. Empty for
    # Mesh (no MasterOutput, R17) and Sequential/Static-Graph DAG (built from code) --
    # nothing lands here for those, which is correct, not a gap.
    if merged_fields.get("simulate_summary"):
        best["simulate_narrative"] = {"payload": str(merged_fields["simulate_summary"])}
    coord_fields = {k: merged_fields[k] for k in _COORDINATOR_NARRATIVE_FIELDS if merged_fields.get(k)}
    if coord_fields:
        best["coordinator_narrative"] = {"payload": json.dumps(coord_fields, indent=2)}

    if not best:
        print("  no scorable output on this run — nothing written.")
        return 1

    results = {}
    for cap, item in best.items():
        if dry_run:
            print(f"  [dry-run] would judge {cap:32} ({len(item['payload']):,} chars)")
            continue
        r = judge_output(
            agent_name=cap,
            output_text=item["payload"][:_MAX_CHARS],
            context=_CRITERIA[cap],
        )
        results[cap] = r
        print(f"  {cap:32} rel={r['relevance']:.1f} faith={r['faithfulness']:.1f} "
              f"safe={r['safety']:.1f}")

    if dry_run:
        print("\n  (--dry-run: nothing written)")
        return 0

    # Blend each capability's judged artifact(s) into one score, THEN average equally
    # across capabilities. Averaging every raw artifact flat would let predict count
    # twice as much as diagnose simply because predict happens to split into two
    # judged pieces -- the blend step is what keeps capabilities comparable.
    capability_scores: dict[str, dict] = {}
    for cap, parts in _CAPABILITY_BLEND.items():
        blended = _blend(results, parts)
        if blended is not None:
            capability_scores[cap] = blended

    if capability_scores:
        print("\n  capability blend:")
        for cap, parts in _CAPABILITY_BLEND.items():
            if cap not in capability_scores:
                continue
            weights = " / ".join(f"{w:.0%} {k}" for k, w in parts)
            s = capability_scores[cap]
            print(f"    {cap:20} rel={s['relevance']:.2f} faith={s['faithfulness']:.2f} "
                  f"safe={s['safety']:.2f}   ({weights})")

    dims = ("relevance", "faithfulness", "safety")
    agg = {d: sum(s[d] for s in capability_scores.values()) / len(capability_scores)
          for d in dims}
    mean = sum(agg[d] * w for d, w in _DIM_WEIGHTS.items())

    # Scope-adjusted mean (T42i, 29-Aug-26): judge_mean above only ever averages
    # capabilities that produced a judged artifact -- a capability the query implied but
    # that never even attempted to run just drops out of the denominator, so it is never
    # penalized for the gap (exactly the "a condition that skipped work entirely would
    # win" failure mode this module's own docstring names as why T42 exists). This
    # extends the SAME rule _blend() already applies one level down -- a missing PART of
    # an attempted capability scores zero rather than being excluded -- one level up: a
    # missing CAPABILITY the query implied scores zero across all three dimensions too.
    # Kept as separate judge_*_scope_adj columns rather than overwriting judge_mean, so
    # "quality of what was delivered" and "quality including scope misses" both stay
    # independently queryable against every run scored before this existed.
    missing_implied: list[str] = []
    scope_adjusted_scores = dict(capability_scores)
    if implied_tools:
        for tool_name in implied_tools:
            cap = _TOOL_TO_CAPABILITY.get(canonical(tool_name))
            if cap and cap not in scope_adjusted_scores:
                scope_adjusted_scores[cap] = {"relevance": 0.0, "faithfulness": 0.0, "safety": 0.0}
                missing_implied.append(cap)

    if scope_adjusted_scores:
        agg_adj = {d: sum(s[d] for s in scope_adjusted_scores.values()) / len(scope_adjusted_scores)
                  for d in dims}
        mean_adj = sum(agg_adj[d] * w for d, w in _DIM_WEIGHTS.items())
    else:
        agg_adj = {d: 0.0 for d in dims}
        mean_adj = 0.0

    if implied_tools is None:
        print("\n  SCOPE-ADJUSTED MEAN: skipped -- query_metadata has no implied_tools_json "
              "for this query_id yet")
    elif missing_implied:
        print(f"\n  SCOPE-ADJUSTED MEAN {mean_adj:.2f}/5   (zero-filled, implied but never "
              f"attempted: {', '.join(missing_implied)})")
    else:
        print(f"\n  SCOPE-ADJUSTED MEAN {mean_adj:.2f}/5   (every implied capability was "
              f"attempted -- same as judge mean)")

    migrate_schema(DB_PATH)
    with _conn() as c:
        c.execute("DELETE FROM quality_scores WHERE run_id=?", (rid,))
        c.execute(
            """INSERT INTO quality_scores
               (run_id, judge_relevance, judge_faithfulness, judge_safety,
                judge_mean, judge_model, scored_at,
                judge_relevance_scope_adj, judge_faithfulness_scope_adj,
                judge_safety_scope_adj, judge_mean_scope_adj,
                missing_implied_capabilities_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, round(agg["relevance"], 3), round(agg["faithfulness"], 3),
             round(agg["safety"], 3), round(mean, 3), "gpt-4.1-mini",
             datetime.now(timezone.utc).isoformat(),
             round(agg_adj["relevance"], 3) if implied_tools is not None else None,
             round(agg_adj["faithfulness"], 3) if implied_tools is not None else None,
             round(agg_adj["safety"], 3) if implied_tools is not None else None,
             round(mean_adj, 3) if implied_tools is not None else None,
             json.dumps(missing_implied) if implied_tools is not None else None))
        c.commit()

    print(f"\n  JUDGE WEIGHTED MEAN {mean:.2f}/5   "
          f"(relevance {agg['relevance']:.2f} x.45, faithfulness {agg['faithfulness']:.2f} x.45, "
          f"safety {agg['safety']:.2f} x.10)")
    print(f"  scored {len(results)} artifact(s) -> {len(capability_scores)} "
          f"capability score(s) -> quality_scores")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_id", nargs="?", default="latest")
    ap.add_argument("--all-unscored", action="store_true",
                    help="score every run that has no quality_scores row yet")
    ap.add_argument("--dry-run", action="store_true", help="show what would be judged")
    ap.add_argument("--force", action="store_true", help="re-score an already-scored run")
    a = ap.parse_args()

    if a.all_unscored:
        with _conn() as c:
            ids = [r[0] for r in c.execute(
                "SELECT run_id FROM run WHERE run_id NOT IN "
                "(SELECT run_id FROM quality_scores) ORDER BY started_at")]
        if not ids:
            print("Every run already has a quality score.")
            return 0
        print(f"{len(ids)} unscored run(s).")
        rc = 0
        for rid in ids:
            rc |= score_run(rid, dry_run=a.dry_run, force=False)
        return rc

    return score_run(a.run_id, dry_run=a.dry_run, force=a.force)


if __name__ == "__main__":
    sys.exit(main())
