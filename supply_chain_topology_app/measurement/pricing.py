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

def load_pricing_table(path: Path = _PRICING_FILE) -> dict:
    table = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            table[row["model"]] = {
                "input_price_per_1m": float(row["input_price_per_1m"]),
                "output_price_per_1m": float(row["output_price_per_1m"]),
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
    cost = (usage.get("prompt_tokens", 0) / 1_000_000) * prices["input_price_per_1m"]
    cost += (usage.get("completion_tokens", 0) / 1_000_000) * prices["output_price_per_1m"]
    return round(cost, 6)


def total_sub_agent_cost(model: str, sub_agent_usage: list[dict]) -> float:
    """Sum estimate_cost() across every sub-agent usage record, skipping
    any that come back None (unpriced model)."""
    costs = [estimate_cost(model, u) for u in sub_agent_usage]
    return round(sum(c for c in costs if c is not None), 6)