"""
Eval: RAG quality — RAGAS faithfulness + answer relevancy.

Runs as part of the default eval suite (no --ragas flag needed).

The recommendation agent's own instruction/query never varies, so varying
*that* wouldn't give RAGAS a meaningful spread of samples. Instead: the agent's
OUTPUT naturally cites several distinct SLA topics — one recommendation might
reference weather policy, another partner benchmarks, another distance rules.
We sample a diverse subset of recommended_actions (up to 2 per category:
quick-win / short-term / long-term), issue a SEPARATE retrieval query per
topic (bypassing the tool-output summarizer via query_override), and build one
RAGAS sample per topic. This gives RAGAS a real multi-sample dataset — n > 1 —
instead of one blended sample, so the reported score is a genuine mean across
distinct SLA sections rather than a single noisy LLM judgment.
"""

import re
import pytest
from agent_adapter import run_agent

from conftest import EVAL_DB
from eval_config import (
    MIN_FAITHFULNESS, MIN_ANSWER_RELEVANCY,
    MIN_CONTEXT_PRECISION, MAX_HALLUCINATION_RATE,
)
from judge import judge_output

from delivery_agents import recommendation_agent, RecommendedActionsList
from tools.rag_knowledge import retrieve_sla_context
import sys, tools  # noqa: E401
ra_module = sys.modules["tools.recommend_actions"]

_MAX_PER_CATEGORY = 2


def _select_diverse_actions(output: RecommendedActionsList) -> list:
    """Pick up to _MAX_PER_CATEGORY actions per category, skipping empty sla_reference."""
    by_category: dict[str, list] = {}
    for action in output.recommended_actions:
        if not action.sla_reference.strip():
            continue
        by_category.setdefault(action.category, []).append(action)

    selected = []
    for category, actions in by_category.items():
        selected.extend(actions[:_MAX_PER_CATEGORY])
    return selected


def _parse_retrieved_sections(context_str: str) -> list[str]:
    """Split retrieve_sla_context()'s formatted block into individual chunk strings."""
    sections = re.split(r"\n### Retrieved Section \d+\n", context_str)
    return [s.strip() for s in sections[1:] if s.strip()]  # sections[0] is the header line


async def test_rag_faithfulness_and_relevancy(seeded_eval_db):
    """Run recommendation agent once, then score faithfulness + answer_relevancy
    per-topic across a diverse sample of its recommended_actions."""
    try:
        from ragas import EvaluationDataset, evaluate
        from ragas.dataset_schema import SingleTurnSample
        from ragas.metrics import Faithfulness, AnswerRelevancy, LLMContextPrecisionWithoutReference
        from ragas.llms import LangchainLLMWrapper
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        pytest.skip(f"RAGAS dependencies not installed: {e}. Run: uv sync --extra eval")

    # Patch recommend_actions to read from the eval DB
    original_db = ra_module._DB_PATH
    ra_module._DB_PATH = EVAL_DB
    try:
        result = await run_agent(
            recommendation_agent,
            "Recommend ways to optimize delivery timelines and reduce delays",
        )
    finally:
        ra_module._DB_PATH = original_db

    output = result.final_output
    assert output is not None, "Recommendation agent returned no output"

    tool_out = result.tool_outputs.get("recommend_actions", "")
    if "--- SLA Knowledge Context" not in tool_out:
        pytest.skip("Could not find SLA context block in recommend_actions output")

    selected = _select_diverse_actions(output)
    if not selected:
        pytest.skip("No recommended_actions with non-empty sla_reference found")

    # One retrieval + one RAGAS sample per topic, each independently retrieved.
    #
    # Two separate strings per topic, not one:
    # - retrieval_query: terse, keyword-dense, built from the operational CONDITION
    #   (supporting_data) plus a single primary dimension — matches how the SLA
    #   doc itself is phrased (e.g. "Stormy | Halt same-day and express dispatches
    #   if wind speed > 60 km/h"). Compound dimensions ("delivery_mode + weather +
    #   region") are de-duplicated to the first one — asking about 3 concepts at
    #   once dilutes the embedding against a doc organized by single topic per
    #   section. This is what actually drives retrieval.
    # - topic_question: a natural-language question over the same condition, used
    #   only as RAGAS's `user_input` field (what AnswerRelevancy / ContextPrecision
    #   judge against) — kept separate because those metrics want a coherent
    #   question to reason about, even though a terse string retrieves better.
    samples = []
    per_topic_rows = []
    for action in selected:
        primary_dimension = action.dimension.split(" + ")[0].replace("_", " ").strip()
        retrieval_query = f"{primary_dimension}: {action.supporting_data}"
        topic_question = (
            f"What SLA policy, target, or threshold applies to {primary_dimension} "
            f"given: {action.supporting_data}?"
        )
        context_block = retrieve_sla_context(tool_output="", query_override=retrieval_query)
        chunks = _parse_retrieved_sections(context_block)
        if not chunks:
            continue
        samples.append(SingleTurnSample(
            user_input=topic_question,
            retrieved_contexts=chunks,
            response=action.sla_reference,
        ))
        per_topic_rows.append((action.category, action.dimension, action.action, topic_question))

    if not samples:
        pytest.skip("No SLA chunks retrieved for any sampled topic")

    dataset = EvaluationDataset(samples=samples)
    llm = LangchainLLMWrapper(ChatOpenAI(model="gpt-4.1-mini", temperature=0))

    # Four metrics covering: groundedness (Faithfulness), answer relevance
    # (AnswerRelevancy), context relevance (LLMContextPrecisionWithoutReference —
    # reuses the same user_input/retrieved_contexts/response fields, no extra
    # retrieval needed), and hallucination rate (derived below as 1 - faithfulness,
    # no extra LLM call needed).
    scores = evaluate(
        dataset=dataset,
        metrics=[
            Faithfulness(llm=llm),
            AnswerRelevancy(llm=llm),
            LLMContextPrecisionWithoutReference(llm=llm),
        ],
    )
    score_df = scores.to_pandas()
    faithfulness       = float(score_df["faithfulness"].mean())
    answer_rel         = float(score_df["answer_relevancy"].mean())
    context_precision  = float(score_df["llm_context_precision_without_reference"].mean())
    hallucination_rate = 1.0 - faithfulness

    assert faithfulness >= MIN_FAITHFULNESS, (
        f"RAGAS mean faithfulness {faithfulness:.3f} < threshold {MIN_FAITHFULNESS} "
        f"(n={len(samples)} topics)"
    )
    assert answer_rel >= MIN_ANSWER_RELEVANCY, (
        f"RAGAS mean answer_relevancy {answer_rel:.3f} < threshold {MIN_ANSWER_RELEVANCY} "
        f"(n={len(samples)} topics)"
    )
    assert context_precision >= MIN_CONTEXT_PRECISION, (
        f"RAGAS mean context precision {context_precision:.3f} < threshold {MIN_CONTEXT_PRECISION} "
        f"(n={len(samples)} topics)"
    )
    assert hallucination_rate <= MAX_HALLUCINATION_RATE, (
        f"Derived hallucination rate {hallucination_rate:.3f} > threshold {MAX_HALLUCINATION_RATE} "
        f"(n={len(samples)} topics)"
    )

    # Per-topic breakdown for the markdown report — shows whether retrieval quality
    # varies by SLA section rather than hiding it behind one blended score.
    breakdown_lines = [
        f"Sampled {len(samples)} distinct SLA topics across "
        f"{len({c for c, *_ in per_topic_rows})} categories:\n",
        "| Category | Dimension | Action | Faithfulness | Relevancy | Context Precision | Hallucination Rate |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, (category, dimension, action_name, _q) in enumerate(per_topic_rows):
        row = score_df.iloc[i]
        row_hallucination = 1.0 - row["faithfulness"]
        breakdown_lines.append(
            f"| {category} | {dimension} | {action_name[:50]} "
            f"| {row['faithfulness']:.3f} | {row['answer_relevancy']:.3f} "
            f"| {row['llm_context_precision_without_reference']:.3f} | {row_hallucination:.3f} |"
        )
    response_text = "\n".join(breakdown_lines)

    judge_output(
        "RAG — SLA Knowledge Retrieval",
        response_text,
        context=(
            "Relevance: does the per-topic breakdown show consistent SLA grounding across "
            "categories? Faithfulness: are sla_reference citations traceable to their own "
            "independently-retrieved SLA context? Safety: no hallucinated SLA thresholds."
        ),
        agent_input="Recommend ways to optimize delivery timelines and reduce delays",
        ragas_scores={
            "faithfulness": faithfulness,
            "answer_relevancy": answer_rel,
            "context_precision": context_precision,
            "hallucination_rate": hallucination_rate,
        },
    )
