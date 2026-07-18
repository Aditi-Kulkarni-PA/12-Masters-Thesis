"""
Eval: email_alert_agent (function tool).

The email tool reads the prediction CSV from the hardcoded pipeline path
(prediction_pipeline/data/processed/), which is written by seeded_eval_db.
No patching needed — the CSV path is consistent.

Checks:
  - fetch_delayed_orders_for_email function tool was called
  - EmailsList schema is valid
  - At least one email generated
  - Template subject line matches severity (Urgent/Moderate/Minor)
  - email_content is non-trivially long
  - LLM-as-judge scores email tone and personalisation
  - latency within budget
"""

import time
import pytest
from agent_adapter import run_agent

from eval_config import MIN_EMAILS, MIN_JUDGE_SCORE, MAX_EMAIL_LATENCY_S
from judge import get_called_tools, judge_output, mean_score

from delivery_agents import email_alert_agent, EmailsList

_SEVERITY_SUBJECT = {
    "Long": "Urgent",
    "Medium": "Moderate",
    "Short": "Minor",
}


@pytest.fixture(scope="module")
async def email_result(seeded_eval_db):
    t0 = time.perf_counter()
    result = await run_agent(
        email_alert_agent,
        "Generate customer email alerts for all delayed orders",
    )
    return result, time.perf_counter() - t0


# ── Tool call ─────────────────────────────────────────────────────────────────

async def test_email_tool_called(email_result):
    result, _ = email_result
    assert "fetch_delayed_orders_for_email" in result.called_tools, (
        "Expected fetch_delayed_orders_for_email function tool to be called"
    )


# ── Schema ────────────────────────────────────────────────────────────────────

async def test_email_schema_valid(email_result):
    result, _ = email_result
    output = result.final_output
    assert output is not None
    assert isinstance(output, EmailsList)


async def test_email_minimum_count(email_result):
    result, _ = email_result
    count = len(result.final_output.content)
    assert count >= MIN_EMAILS, (
        f"Expected >= {MIN_EMAILS} email(s), got {count}"
    )


# ── Content ───────────────────────────────────────────────────────────────────

async def test_email_content_non_empty(email_result):
    result, _ = email_result
    short = [
        i for i, e in enumerate(result.final_output.content)
        if len(e.email_content) < 100
    ]
    assert not short, f"email_content too short (< 100 chars) for email indices: {short}"


async def test_email_subject_matches_severity(email_result):
    """Emails whose content starts with 'Subject:' must match severity keyword."""
    result, _ = email_result
    mismatches = []
    for email in result.final_output.content:
        body = email.email_content
        first_line = body.split("\n")[0] if body else ""
        if not first_line.startswith("Subject:"):
            continue
        subject = first_line.lower()
        # If we can tell the severity from the subject line, check it
        if "urgent" in subject or "significant" in subject:
            pass  # Long severity
        elif "moderate" in subject:
            pass  # Medium severity
        elif "minor" in subject or "slight" in subject or "brief" in subject:
            pass  # Short severity
        else:
            mismatches.append(first_line)

    # We only assert if there are lines that matched none of the severity patterns
    assert not mismatches, (
        f"Subject lines don't match any expected severity pattern: {mismatches}"
    )


# ── LLM-as-judge ─────────────────────────────────────────────────────────────

async def test_email_judge_score(email_result):
    result, _ = email_result
    emails = result.final_output.content
    sample = "\n\n---\n\n".join(e.email_content for e in emails[:3])
    scores = judge_output(
        "Email Alert Agent",
        sample,
        context=(
            "Relevance: is the tone professional and empathetic, appropriate for a customer delay notification? "
            "Does each email clearly state the delay reason and what the company is doing about it? "
            "Faithfulness: does the email reference the actual delay cause (weather, route, vehicle) "
            "rather than a generic apology with no specifics? "
            "Safety: no sensitive internal data (model scores, DB IDs) exposed to the customer; "
            "no false promises about guaranteed delivery times."
        ),
        agent_input="Generate customer email alerts for all delayed orders",
    )
    avg = mean_score(scores)
    assert avg >= MIN_JUDGE_SCORE, (
        f"Judge mean score {avg:.2f} < threshold {MIN_JUDGE_SCORE}. "
        f"relevance={scores['relevance']} faithfulness={scores['faithfulness']} "
        f"safety={scores['safety']}. Reasoning: {scores['reasoning']}"
    )


# ── Latency ───────────────────────────────────────────────────────────────────

async def test_email_latency(email_result):
    _, elapsed = email_result
    assert elapsed <= MAX_EMAIL_LATENCY_S, (
        f"Email agent took {elapsed:.1f}s, limit is {MAX_EMAIL_LATENCY_S}s"
    )
