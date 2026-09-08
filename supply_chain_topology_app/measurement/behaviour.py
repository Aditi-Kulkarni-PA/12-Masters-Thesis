"""Classify what a run did when it executed no capability the query required.

Separate from run_status, which records whether execution happened. This records what
happened instead, and only for the runs where nothing ran. NULL for every run that
executed normally.

    wrongful_decline          refused a query that was in scope
    clarification_request     asked what to do rather than acting
    empty_output              produced no usable text
    answer_without_execution  answered from its own knowledge, no capability behind it

Classification is an LLM judgement, not string matching. An earlier keyword version was
rewritten three times in one sitting and relabelled real runs on each pass, which is
enough evidence that the distinctions are semantic rather than lexical. The two that
matter most are the hardest to match on words: a plan ending in "shall I proceed?" and a
substantive answer ending in "if you want me to analyse your file" share most of their
vocabulary and mean opposite things.

The judge is the same fixed model used for the quality rubric, called at temperature 0
with the query, its required capabilities and the run's output. It returns the class and
a one-line rationale, and both are stored, so any published rate can be traced back to
the reasoning that produced it.

clarification_appropriate is recorded alongside: whether a question, if asked, was a
reasonable thing to ask given the query. A poor question is still a clarification
request, not an answer -- the two are kept apart so that answer_without_execution keeps
meaning one thing, that content appeared with no capability supplying it.

No fallback classifier is provided. A judge failure leaves the field NULL rather than
guessing, because a wrong label here is worse than a missing one: it feeds a published
rate, and NULL is visible while a plausible guess is not.
"""

import json

CLASSES = (
    "wrongful_decline",
    "clarification_request",
    "empty_output",
    "answer_without_execution",
)

_PROMPT = """You are classifying the behaviour of an automated assistant.

The assistant was asked a question that required it to run one or more data-analysis
capabilities. It ran NONE of them. Your job is to say what it did instead.

The question it was asked (the harness appends the path to the orders data file to
every query, so the data it needs is always available to it -- a claim that no data was
provided is factually wrong):
{query}

The capabilities it was required to run: {required}

What it returned:
{output}

Choose exactly one class:

- "wrongful_decline": it refused, or said the request was outside what it can help with,
  even though the question is about delivery-delay analysis and is in scope.
- "clarification_request": it asked the user something before acting, or presented a
  plan and waited for approval. Proposing steps and asking whether to proceed is a
  clarification request, however long the message.
- "empty_output": it returned nothing usable -- blank fields, or no substantive text.
- "answer_without_execution": it answered the question from its own general knowledge,
  making substantive claims about delay patterns, causes or recommendations, without
  running anything. An answer that ends with an offer to analyse the data is still an
  answer, not a clarification request.

Also judge, only when the class is clarification_request, whether the question it asked
was a reasonable one given the wording of the query: 1 if the query was genuinely
ambiguous or under-specified in the way the assistant identified, 0 if the query was
clear enough to act on. Use null for every other class.

Respond with ONLY valid JSON:
{{"behaviour_class": "<one of the four>", "clarification_appropriate": <1, 0 or null>,
  "rationale": "<one sentence>"}}"""


def classify(final_answer: str | None, *, query_text: str = "",
             required: list[str] | None = None, judge=None) -> dict:
    """Classify one non-executing run. Returns class, appropriateness flag and rationale.

    *judge* is the callable that reaches the model, injected so this module stays
    importable and testable without a network call. It receives the prompt and returns
    the model's raw text.
    """
    if judge is None:
        raise ValueError(
            "classify() needs a judge callable. Behaviour classification is an LLM "
            "judgement; there is deliberately no keyword fallback."
        )

    output = (final_answer or "").strip()
    prompt = _PROMPT.format(
        query=query_text or "(query text unavailable)",
        required=", ".join(required or []) or "(none recorded)",
        output=output or "(the assistant returned nothing)",
    )

    raw = json.loads(judge(prompt))
    cls = str(raw.get("behaviour_class", "")).strip()
    if cls not in CLASSES:
        raise ValueError(f"judge returned an unknown behaviour_class: {cls!r}")

    appropriate = raw.get("clarification_appropriate")
    if cls != "clarification_request":
        appropriate = None
    elif appropriate is not None:
        appropriate = int(appropriate)

    return {
        "behaviour_class": cls,
        "clarification_appropriate": appropriate,
        "rationale": str(raw.get("rationale", "")).strip(),
    }
