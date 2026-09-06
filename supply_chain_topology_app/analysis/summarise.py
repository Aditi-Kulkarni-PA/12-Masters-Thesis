"""Spread and central-tendency helpers for the aggregation layer.

Implements the reporting rule from the proposal, Section 7.5: repeated runs are
summarised by the median. Where N is below 5 the spread is the observed range. Where
N is 5 or more the interquartile range is reported as well.

Values of None are dropped before summarising, so a measure that is undefined for a
run does not pull the median toward zero. The count of values actually used is
returned alongside every summary, so a figure is never read without its denominator.
"""

import statistics

# Below this count the interquartile range describes almost the same points as the
# range itself, so reporting it would imply more data than exists.
_IQR_MIN_N = 5


def summarise(values: list[float | None]) -> dict:
    """Median with spread for one measure across repeated runs.

    Returns n as the number of usable values. Returns None for every statistic when
    no usable value exists, rather than 0, so an absent measure stays distinguishable
    from a measured zero.
    """
    usable = [v for v in values if v is not None]
    if not usable:
        return {"n": 0, "median": None, "mean": None, "min": None, "max": None,
                "q1": None, "q3": None}

    # The mean is carried alongside the median because the two diverge sharply for a
    # bounded measure whose usual value is the optimum. Capability coverage sits at 1.0
    # on most queries, so its median across a topology's queries is 1.0 for every
    # topology in the nano pilot, while the mean separates them from 0.714 to 1.000.
    # See the summary-rule note in aggregate.py.
    summary = {
        "n": len(usable),
        "median": round(statistics.median(usable), 4),
        "mean": round(statistics.fmean(usable), 4),
        "min": round(min(usable), 4),
        "max": round(max(usable), 4),
        "q1": None,
        "q3": None,
    }

    # The interquartile range is added only once enough runs exist to make it
    # meaningful. Below that threshold the range above already describes the spread.
    if len(usable) >= _IQR_MIN_N:
        quartiles = statistics.quantiles(usable, n=4, method="inclusive")
        summary["q1"] = round(quartiles[0], 4)
        summary["q3"] = round(quartiles[2], 4)

    return summary


def rate(flags: list[int | None]) -> dict:
    """Proportion of runs carrying a binary flag, with the denominator retained."""
    usable = [f for f in flags if f is not None]
    if not usable:
        return {"n": 0, "rate": None, "count": 0}
    return {"n": len(usable), "rate": round(sum(usable) / len(usable), 4), "count": sum(usable)}


def spread_label(n: int) -> str:
    """How the spread should be described in a table, given the number of runs."""
    return "IQR" if n >= _IQR_MIN_N else "range"
