"""
Eval: predict_delivery_delays_agent (MCP tool).

Checks:
  - predict_delivery_delays MCP tool was called
  - DeliveryDelayPredictionResult schema is valid
  - predict_summary is substantive
  - delayed_orders list is populated
  - llm_insights references at least one derived feature name
  - LLM-as-judge scores predict_summary and llm_insights separately (each >= MIN_JUDGE_SCORE);
    the report averages the two into one row for Predict Delivery Delays
  - latency within budget
"""

import time
import pytest
from agent_adapter import run_agent

from conftest import FULL_INPUT_FILE_REL
from eval_config import (
    MIN_DELAYED_ORDERS, MIN_JUDGE_SCORE, MAX_PREDICT_LATENCY_S,
    PREDICT_FEATURE_NAMES,
)
from judge import get_called_tools, judge_output, mean_score

from delivery_agents import predict_delivery_delays_agent
from delivery_agents import DeliveryDelayPredictionResult


@pytest.fixture(scope="module")
async def predict_result(pipeline_mcp_server):
    """Run predict agent once; share across all tests in this module."""
    t0 = time.perf_counter()
    result = await run_agent(
        predict_delivery_delays_agent,
        f"Predict delivery delays for orders in {FULL_INPUT_FILE_REL}",
    )
    return result, time.perf_counter() - t0


# ── Tool call ─────────────────────────────────────────────────────────────────

async def test_predict_tool_called(predict_result):
    result, _ = predict_result
    assert "predict_delivery_delays" in result.called_tools, (
        "Expected predict_delivery_delays MCP tool to be called"
    )


# ── Schema ────────────────────────────────────────────────────────────────────

async def test_predict_schema_valid(predict_result):
    result, _ = predict_result
    output = result.final_output
    assert output is not None, "Agent returned no final output"
    assert isinstance(output, DeliveryDelayPredictionResult), (
        f"Expected DeliveryDelayPredictionResult, got {type(output)}"
    )


# ── Content ───────────────────────────────────────────────────────────────────

async def test_predict_summary_non_empty(predict_result):
    result, _ = predict_result
    summary = result.final_output.predict_summary
    assert len(summary) > 50, f"predict_summary too short ({len(summary)} chars)"


async def test_predict_delayed_orders_populated(predict_result):
    result, _ = predict_result
    rows = result.final_output.delayed_orders
    assert len(rows) >= MIN_DELAYED_ORDERS, (
        f"Expected >= {MIN_DELAYED_ORDERS} delayed orders, got {len(rows)}"
    )


async def test_predict_llm_insights_reference_features(predict_result):
    result, _ = predict_result
    rows = result.final_output.delayed_orders
    missing = []
    for row in rows[:10]:  # spot-check first 10
        insight_lower = row.llm_insights.lower()
        if not any(f in insight_lower for f in PREDICT_FEATURE_NAMES):
            missing.append(row.delivery_id)
    assert not missing, (
        f"llm_insights for delivery_ids {missing} reference no known feature names. "
        f"Expected at least one of: {PREDICT_FEATURE_NAMES}"
    )


# ── LLM-as-judge ─────────────────────────────────────────────────────────────

async def test_predict_summary_judge_score(predict_result):
    result, _ = predict_result
    output = result.final_output
    scores = judge_output(
        "Predict Delivery Delays — Summary",
        output.predict_summary,
        context=(
            "Relevance: does the summary cite specific quantitative stats (counts, percentages, delay rates)? "
            "Faithfulness: are the cited stats plausible and internally consistent — not invented? "
            "Safety: no fabricated delivery IDs or impossible delay values."
        ),
        agent_input=f"Predict delivery delays for orders in {FULL_INPUT_FILE_REL}",
    )
    avg = mean_score(scores)
    assert avg >= MIN_JUDGE_SCORE, (
        f"Judge mean score {avg:.2f} < threshold {MIN_JUDGE_SCORE}. "
        f"relevance={scores['relevance']} faithfulness={scores['faithfulness']} "
        f"safety={scores['safety']}. Reasoning: {scores['reasoning']}"
    )


async def test_predict_insights_judge_score(predict_result):
    result, _ = predict_result
    output = result.final_output
    insights_rows = "\n".join(
        f"| {r.delivery_id} | {r.llm_insights} |"
        for r in output.delayed_orders[:5]
    )
    text = f"| Delivery ID | LLM Insights |\n|---|---|\n{insights_rows}"
    scores = judge_output(
        "Predict Delivery Delays — LLM Insights",
        text,
        context=(
            "Relevance: does each row explain WHY that specific delivery is at risk? "
            "Faithfulness: do llm_insights reference actual derived features (vehicle_load_strain, "
            "schedule_risk, km_per_expected_hr) — not invented metrics? "
            "Safety: no fabricated delivery IDs or impossible delay values."
        ),
        agent_input=f"Predict delivery delays for orders in {FULL_INPUT_FILE_REL}",
    )
    avg = mean_score(scores)
    assert avg >= MIN_JUDGE_SCORE, (
        f"Judge mean score {avg:.2f} < threshold {MIN_JUDGE_SCORE}. "
        f"relevance={scores['relevance']} faithfulness={scores['faithfulness']} "
        f"safety={scores['safety']}. Reasoning: {scores['reasoning']}"
    )


# ── Latency ───────────────────────────────────────────────────────────────────

async def test_predict_latency(predict_result):
    _, elapsed = predict_result
    assert elapsed <= MAX_PREDICT_LATENCY_S, (
        f"Predict agent took {elapsed:.1f}s, limit is {MAX_PREDICT_LATENCY_S}s"
    )
