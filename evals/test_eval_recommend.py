"""
Eval: recommendation_agent (function tool + embedded RAG).

DB isolation: recommend_actions._DB_PATH is hardcoded in the tool module.
We patch it at the module level here — no changes to existing source code.

Checks:
  - recommend_actions function tool was called
  - RecommendedActionsList schema is valid (>= 9 actions)
  - Category balance: >= 3 quick-win, short-term, long-term each
  - sla_reference fields are non-empty
  - Inline RAG grounding: SLA context header present, SLA keywords found
  - SLA grounding: sla_reference phrases trace back to the source document
  - LLM-as-judge scores recommendation quality
  - latency within budget
"""

import time
from pathlib import Path
import pytest
from agent_adapter import run_agent

from conftest import EVAL_DB, APP_DIR
from eval_config import (
    MIN_RECOMMENDATIONS, MIN_JUDGE_SCORE, MAX_RECOMMEND_LATENCY_S, SLA_KEYWORDS,
)
from judge import judge_output, mean_score

from thesis_target import recommendation_agent, RecommendedActionsList
# tools/__init__.py shadows the submodule name with the FunctionTool; use sys.modules
import sys, tools  # noqa: E401 — side-effect: loads tools package and submodules
ra_module = sys.modules["tools.recommend_actions"]

_SLA_FILE = APP_DIR / "knowledge" / "delivery_sla_github_ready.md"


@pytest.fixture(scope="module", autouse=True)
def patch_recommend_db():
    """Patch recommend_actions._DB_PATH to point to the eval DB for this module."""
    original = ra_module._DB_PATH
    ra_module._DB_PATH = EVAL_DB
    yield
    ra_module._DB_PATH = original


@pytest.fixture(scope="module")
async def recommend_result(seeded_eval_db):
    t0 = time.perf_counter()
    result = await run_agent(
        recommendation_agent,
        "Recommend ways to optimize delivery timelines and reduce delays",
    )
    return result, time.perf_counter() - t0


# ── Tool call ─────────────────────────────────────────────────────────────────

async def test_recommend_tool_called(recommend_result):
    result, _ = recommend_result
    assert "recommend_actions" in result.called_tools, (
        "Expected recommend_actions function tool to be called"
    )


# ── Schema ────────────────────────────────────────────────────────────────────

async def test_recommend_schema_valid(recommend_result):
    result, _ = recommend_result
    output = result.final_output
    assert output is not None
    assert isinstance(output, RecommendedActionsList)


async def test_recommend_minimum_count(recommend_result):
    result, _ = recommend_result
    count = len(result.final_output.recommended_actions)
    assert count >= MIN_RECOMMENDATIONS, (
        f"Expected >= {MIN_RECOMMENDATIONS} recommendations, got {count}"
    )


async def test_recommend_category_balance(recommend_result):
    result, _ = recommend_result
    from collections import Counter
    cats = Counter(r.category for r in result.final_output.recommended_actions)
    for cat in ("quick-win", "short-term", "long-term"):
        assert cats[cat] >= 3, (
            f"Expected >= 3 '{cat}' recommendations, got {cats[cat]}"
        )


# ── SLA reference population ──────────────────────────────────────────────────

async def test_recommend_sla_references_non_empty(recommend_result):
    result, _ = recommend_result
    empty = [
        r.action for r in result.final_output.recommended_actions
        if not r.sla_reference.strip()
    ]
    assert not empty, f"sla_reference empty for actions: {empty}"


# ── Inline RAG grounding (no extra API calls) ─────────────────────────────────

async def test_recommend_sla_context_retrieved(recommend_result):
    """The tool output must contain the RAG context header."""
    result, _ = recommend_result
    tool_out = result.tool_outputs.get("recommend_actions", "")
    assert "SLA Knowledge Context" in tool_out or "SLA Reference" in tool_out, (
        "SLA Knowledge Context block not found in recommend_actions tool output — "
        "RAG retrieval may have failed"
    )


async def test_recommend_sla_keywords_present(recommend_result):
    result, _ = recommend_result
    refs = " ".join(r.sla_reference for r in result.final_output.recommended_actions).lower()
    found = SLA_KEYWORDS & set(refs.split())
    assert found, (
        f"No SLA keywords found in sla_reference fields. "
        f"Expected at least one of: {SLA_KEYWORDS}"
    )


async def test_recommend_sla_grounding(recommend_result):
    """Each sla_reference must contain a phrase traceable to the SLA source document.

    The model should quote actual SLA section headings and content, not generic
    labels. We check for a 4-word verbatim window OR a 3-word window anywhere in
    the ref text (excluding the 'SLA N.N' numeric prefix so section headings
    always qualify, e.g. 'SLA 3.2 Weather-Specific Operational Protocols: ...')
    """
    import re
    if not _SLA_FILE.exists():
        pytest.skip(f"SLA source file not found: {_SLA_FILE}")

    sla_text = _SLA_FILE.read_text(encoding="utf-8").lower()
    result, _ = recommend_result
    ungrounded = []
    for r in result.final_output.recommended_actions:
        raw_ref = r.sla_reference.strip().lower()
        if not raw_ref:
            continue
        words = raw_ref.split()
        # 4-word sliding window on the full reference
        grounded_4 = len(words) >= 4 and any(
            " ".join(words[i:i+4]) in sla_text
            for i in range(len(words) - 3)
        )
        # 3-word window on text after stripping numeric SLA prefix (e.g. "sla 3.2")
        stripped = re.sub(r"^sla\s+[\d\.]+\s*", "", raw_ref).strip()
        s_words = stripped.split()
        grounded_3 = len(s_words) >= 3 and any(
            " ".join(s_words[i:i+3]) in sla_text
            for i in range(max(1, len(s_words) - 2))
        )
        if not grounded_4 and not grounded_3:
            ungrounded.append(r.action)

    assert not ungrounded, (
        f"sla_reference for these actions has no phrase traceable to the SLA document: {ungrounded}"
    )


# ── LLM-as-judge ─────────────────────────────────────────────────────────────

async def test_recommend_judge_score(recommend_result):
    result, _ = recommend_result
    actions = result.final_output.recommended_actions
    sample = "\n".join(
        f"[{r.category}] {r.action}: {r.action_desc[:200]} | SLA: {r.sla_reference}"
        for r in actions[:6]
    )
    scores = judge_output(
        "Recommendation Expert Agent",
        sample,
        context=(
            "Relevance: are recommendations specific and backed by delivery data — not generic supply chain advice? "
            "Faithfulness: do SLA references cite actual policy targets, thresholds, or penalty clauses "
            "from the retrieved SLA document — not vague phrases? "
            "Safety: no conflicting or contradictory recommendations; "
            "quick-win / short-term / long-term categorization is logically consistent with effort level."
        ),
        agent_input="Recommend ways to optimize delivery timelines and reduce delays",
    )
    avg = mean_score(scores)
    assert avg >= MIN_JUDGE_SCORE, (
        f"Judge mean score {avg:.2f} < threshold {MIN_JUDGE_SCORE}. "
        f"relevance={scores['relevance']} faithfulness={scores['faithfulness']} "
        f"safety={scores['safety']}. Reasoning: {scores['reasoning']}"
    )


# ── Latency ───────────────────────────────────────────────────────────────────

async def test_recommend_latency(recommend_result):
    _, elapsed = recommend_result
    assert elapsed <= MAX_RECOMMEND_LATENCY_S, (
        f"Recommend agent took {elapsed:.1f}s, limit is {MAX_RECOMMEND_LATENCY_S}s"
    )
