"""
Eval: delay_simulation_agent (MCP tool).

Checks:
  - simulate_order_delays MCP tool was called
  - SimulationsList schema is valid
  - simulations list is non-empty
  - simulated_severity fields are populated
  - at least one row shows a severity change (what-if worked)
  - LLM-as-judge scores simulation summary
  - latency within budget
"""

import time
import pytest
from agent_adapter import run_agent

from eval_config import MIN_SIMULATIONS, MIN_JUDGE_SCORE, MAX_SIMULATE_LATENCY_S
from judge import get_called_tools, judge_output, mean_score

from thesis_target import delay_simulation_agent, SimulationsList

_SIMULATE_QUERY = "Simulate delays for stormy weather in East region"


@pytest.fixture(scope="module")
async def simulate_result(seeded_eval_db, pipeline_mcp_server):
    t0 = time.perf_counter()
    result = await run_agent(delay_simulation_agent, _SIMULATE_QUERY)
    return result, time.perf_counter() - t0


# ── Tool call ─────────────────────────────────────────────────────────────────

async def test_simulate_tool_called(simulate_result):
    result, _ = simulate_result
    assert "simulate_order_delays" in result.called_tools, (
        "Expected simulate_order_delays MCP tool to be called"
    )


# ── Schema ────────────────────────────────────────────────────────────────────

async def test_simulate_schema_valid(simulate_result):
    result, _ = simulate_result
    output = result.final_output
    assert output is not None
    assert isinstance(output, SimulationsList)


# ── Content ───────────────────────────────────────────────────────────────────

async def test_simulate_rows_populated(simulate_result):
    result, _ = simulate_result
    sims = result.final_output.simulations
    assert len(sims) >= MIN_SIMULATIONS, (
        f"Expected >= {MIN_SIMULATIONS} simulation rows, got {len(sims)}"
    )


async def test_simulate_severity_fields_present(simulate_result):
    result, _ = simulate_result
    empty = [
        s.delivery_id for s in result.final_output.simulations
        if not s.simulated_severity
    ]
    assert not empty, f"simulated_severity empty for delivery_ids: {empty}"


async def test_simulate_what_if_produces_change(simulate_result):
    result, _ = simulate_result
    sims = result.final_output.simulations
    changed = [
        s for s in sims
        if s.original_severity and s.simulated_severity
        and s.original_severity.lower() != s.simulated_severity.lower()
    ]
    assert changed, (
        "No simulated row shows a severity change — the what-if had no effect. "
        "Check that the simulation agent is using the simulate_order_delays tool correctly."
    )


# ── LLM-as-judge ─────────────────────────────────────────────────────────────

async def test_simulate_judge_score(simulate_result):
    result, _ = simulate_result
    sims = result.final_output.simulations
    sample = "\n".join(
        f"  {s.delivery_id}: {s.original_severity} -> {s.simulated_severity} ({s.simulate_delay_reason or ''})"
        for s in sims[:5]
    )
    scores = judge_output(
        "Simulate Delay Prediction",
        f"Simulations (first 5):\n{sample}",
        context=(
            "Relevance: do simulated severity changes reflect the queried condition "
            "(stormy weather, East region)? "
            "Faithfulness: is simulate_delay_reason specific about which feature "
            "(weather_condition, region, distance_km) caused the change — no vague explanations? "
            "Safety: no impossible severity values or delivery IDs not present in the dataset."
        ),
        agent_input=_SIMULATE_QUERY,
    )
    avg = mean_score(scores)
    assert avg >= MIN_JUDGE_SCORE, (
        f"Judge mean score {avg:.2f} < threshold {MIN_JUDGE_SCORE}. "
        f"relevance={scores['relevance']} faithfulness={scores['faithfulness']} "
        f"safety={scores['safety']}. Reasoning: {scores['reasoning']}"
    )


# ── Latency ───────────────────────────────────────────────────────────────────

async def test_simulate_latency(simulate_result):
    _, elapsed = simulate_result
    assert elapsed <= MAX_SIMULATE_LATENCY_S, (
        f"Simulate agent took {elapsed:.1f}s, limit is {MAX_SIMULATE_LATENCY_S}s"
    )
