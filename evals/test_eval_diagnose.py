"""
Eval: diagnose_delay_patterns_agent (MCP tool).

Prerequisite: seeded_eval_db must have been populated by predict first.
Checks:
  - get_delay_diagnosis MCP tool was called
  - DelayDiagnosisResult schema is valid
  - high_risk_patterns and comparison rows populated
  - risk_level values are valid enum members
  - LLM-as-judge scores diagnosis_summary
  - latency within budget
"""

import time
import pytest
from agent_adapter import run_agent

from eval_config import (
    MIN_HIGH_RISK_PATTERNS, MIN_COMPARISON_ROWS,
    MIN_JUDGE_SCORE, MAX_DIAGNOSE_LATENCY_S,
)
from judge import get_called_tools, judge_output, mean_score

from delivery_agents import diagnose_delay_patterns_agent
from delivery_agents import DelayDiagnosisResult

_VALID_RISK_LEVELS = {"critical", "high", "medium"}


@pytest.fixture(scope="module")
async def diagnose_result(seeded_eval_db, pipeline_mcp_server):
    """Run diagnose agent once; seeded_eval_db listed first to ensure DB is ready."""
    t0 = time.perf_counter()
    result = await run_agent(
        diagnose_delay_patterns_agent,
        "Provide delay patterns and root cause diagnosis for today's orders",
    )
    return result, time.perf_counter() - t0


# ── Tool call ─────────────────────────────────────────────────────────────────

async def test_diagnose_tool_called(diagnose_result):
    result, _ = diagnose_result
    assert "get_delay_diagnosis" in result.called_tools, (
        "Expected get_delay_diagnosis MCP tool to be called"
    )


# ── Schema ────────────────────────────────────────────────────────────────────

async def test_diagnose_schema_valid(diagnose_result):
    result, _ = diagnose_result
    output = result.final_output
    assert output is not None
    assert isinstance(output, DelayDiagnosisResult)


# ── Content ───────────────────────────────────────────────────────────────────

async def test_diagnose_high_risk_patterns(diagnose_result):
    result, _ = diagnose_result
    patterns = result.final_output.high_risk_patterns
    assert len(patterns) >= MIN_HIGH_RISK_PATTERNS, (
        f"Expected >= {MIN_HIGH_RISK_PATTERNS} high-risk patterns, got {len(patterns)}"
    )


async def test_diagnose_comparison_rows(diagnose_result):
    result, _ = diagnose_result
    rows = result.final_output.comparison
    assert len(rows) >= MIN_COMPARISON_ROWS, (
        f"Expected >= {MIN_COMPARISON_ROWS} comparison rows, got {len(rows)}"
    )


async def test_diagnose_summary_non_empty(diagnose_result):
    result, _ = diagnose_result
    summary = result.final_output.diagnosis_summary
    assert len(summary) > 50, f"diagnosis_summary too short ({len(summary)} chars)"


async def test_diagnose_risk_levels_valid(diagnose_result):
    result, _ = diagnose_result
    invalid = [
        p.risk_level for p in result.final_output.high_risk_patterns
        if p.risk_level.lower() not in _VALID_RISK_LEVELS
    ]
    assert not invalid, f"Invalid risk_level values found: {invalid}"


# ── LLM-as-judge ─────────────────────────────────────────────────────────────

async def test_diagnose_judge_score(diagnose_result):
    result, _ = diagnose_result
    scores = judge_output(
        "Diagnose Delay Patterns",
        result.final_output.diagnosis_summary,
        context=(
            "Relevance: does the summary identify specific root cause patterns by name "
            "(delivery mode, weather condition, region)? "
            "Faithfulness: are daily vs historical comparisons cited with actual numbers or "
            "percentages drawn from the DB — not estimated or fabricated? "
            "Safety: no alarming or misleading severity claims without data support."
        ),
        agent_input="Provide delay patterns and root cause diagnosis for today's orders",
    )
    avg = mean_score(scores)
    assert avg >= MIN_JUDGE_SCORE, (
        f"Judge mean score {avg:.2f} < threshold {MIN_JUDGE_SCORE}. "
        f"relevance={scores['relevance']} faithfulness={scores['faithfulness']} "
        f"safety={scores['safety']}. Reasoning: {scores['reasoning']}"
    )


# ── Latency ───────────────────────────────────────────────────────────────────

async def test_diagnose_latency(diagnose_result):
    _, elapsed = diagnose_result
    assert elapsed <= MAX_DIAGNOSE_LATENCY_S, (
        f"Diagnose agent took {elapsed:.1f}s, limit is {MAX_DIAGNOSE_LATENCY_S}s"
    )
