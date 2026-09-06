"""
Model pricing lookup — kept as an editable CSV (core/model_pricing.csv)
rather than hardcoded, so a new model can be priced by adding a row, no
code change needed.
"""
import csv
from pathlib import Path
import logging

_PRICING_FILE = Path(__file__).resolve().parent / "model_pricing.csv"
_logger = logging.getLogger(__name__)

# Fraction of the fresh input price charged for a prompt token served from the provider's
# cache, used when the CSV carries no explicit cached price for a model. Providers
# commonly bill cached input at a tenth of the fresh rate. VERIFY this against the
# provider's current price list before citing any cost figure in the thesis: it is a
# default, not a measured value, and it scales every cached token in the study.
_DEFAULT_CACHED_PRICE_RATIO = 0.10


def load_pricing_table(path: Path = _PRICING_FILE) -> dict:
    table = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            input_price = float(row["input_price_per_1m"])
            # Optional column: a model without it falls back to the default ratio above.
            cached = row.get("cached_input_price_per_1m")
            table[row["model"]] = {
                "input_price_per_1m": input_price,
                "output_price_per_1m": float(row["output_price_per_1m"]),
                "cached_input_price_per_1m": (
                    float(cached) if cached not in (None, "")
                    else input_price * _DEFAULT_CACHED_PRICE_RATIO
                ),
            }
    return table


_PRICING = load_pricing_table()




def estimate_cost(model: str, usage: dict) -> float | None:
    """usage: a dict with prompt_tokens/completion_tokens, as returned by
    measurement.instrumentation.extract_usage(). Returns cost in USD, or None if
    `model` has no pricing row (logged as a warning, not raised -- every
    caller just wants to blank the cost, not crash)."""
    prices = _PRICING.get(model)
    if not prices:
        _logger.warning("No pricing row for model '%s' in %s -- add one. Cost will show as blank.",
                         model, _PRICING_FILE.name)
        return None
    # Prompt tokens served from the provider's cache are billed at a fraction of the
    # fresh rate, so they are priced separately rather than at the full input price.
    # Charging every prompt token at the fresh rate overstated cost by roughly 1.4x
    # across the pilot, and most at the tier with the highest input price (Risk Log R69).
    # A response carrying no cached count gives cached = 0 and prices exactly as before.
    prompt = usage.get("prompt_tokens", 0)
    cached = min(usage.get("cached_tokens", 0) or 0, prompt)
    fresh = prompt - cached

    cost = (fresh / 1_000_000) * prices["input_price_per_1m"]
    cost += (cached / 1_000_000) * prices["cached_input_price_per_1m"]
    cost += (usage.get("completion_tokens", 0) / 1_000_000) * prices["output_price_per_1m"]
    return round(cost, 6)


def total_sub_agent_cost(model: str, sub_agent_usage: list[dict]) -> float:
    """Sum estimate_cost() across every sub-agent usage record, skipping
    any that come back None (unpriced model)."""
    costs = [estimate_cost(model, u) for u in sub_agent_usage]
    return round(sum(c for c in costs if c is not None), 6)