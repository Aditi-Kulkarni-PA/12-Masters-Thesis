"""Builds the Full Measures Comparison worksheet's figures from agg_topology.

Every measure named in proposal Sections 7.2.1, 7.2.2 and 7.2.3, for each of the three
tiers, plus the same set split by complexity bin. Reads only.

Two shapes are produced, matching how the worksheet reports each kind:

    dist(measure, model)   median across the 9 topologies, with the min and max of the
                           per-topology values. Answers "what does a typical topology
                           cost here, and how far apart are they".
    pooled(measure, model) one rate over every underlying run, with its numerator and
                           denominator. A rate cannot be averaged across topologies
                           without weighting, so these pool the runs directly.

Reporting a distribution measure as a pooled figure, or the reverse, silently changes
what the number means, so the two are kept apart and each measure is registered as one
or the other in KIND below.
"""

import sqlite3
import statistics as st
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "run_store.db"

MODELS = ["gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.4"]
ORDER = ["monolith", "sequential", "planner_executor", "static_graph_dag",
         "static_graph_routed", "dynamic_graph", "mesh", "swarm",
         "swarm_constrained_adaptive"]
BINS = ["bin:low", "bin:medium", "bin:high", "bin:very_high"]

# How each measure is reported. 'rate' measures pool their runs; everything else is
# summarised as a median across topologies with the spread shown.
KIND = {}


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def load(scope="workload"):
    """agg_topology for one scope, keyed by (model, topology, measure)."""
    with _conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM agg_topology WHERE scope=?", (scope,))]
    for r in rows:
        KIND[r["measure"]] = r["kind"]
    return {(r["model"], r["topology"], r["measure"]): r for r in rows}


def dist(agg, model, measure, fmt="{:.4f}", field="median"):
    """Median across topologies, with the per-topology min and max in brackets.

    The bracketed pair is the spread BETWEEN topologies, not a within-topology
    confidence interval — two different things that look alike once printed.
    """
    vals = [agg[(model, t, measure)][field]
            for t in ORDER if (model, t, measure) in agg
            and agg[(model, t, measure)][field] is not None]
    if not vals:
        return "—"
    return (f"{fmt.format(st.median(vals))} "
            f"({fmt.format(min(vals))} to {fmt.format(max(vals))})")


def pooled(agg, model, measure, scope="workload"):
    """One rate over every run in scope, as 'NN.N% (num/den)'.

    Summed from the stored per-topology counts rather than recomputed from the run
    table, so the worksheet and the aggregate tables cannot disagree.
    """
    num = den = 0
    for t in ORDER:
        row = agg.get((model, t, measure))
        if row is None or row["rate"] is None:
            continue
        den += row["n"]
        num += row["count"] if row["count"] is not None else round(row["rate"] * row["n"])
    if not den:
        return "—"
    return f"{num / den * 100:.1f}% ({num}/{den})"


def pooled_mean(agg, model, measure, fmt="{:.3f}"):
    """One mean over every run, with the per-topology min and max in brackets.

    Used where the worksheet's own row label says 'mean'. A median across the nine
    topology values is a different quantity and reads high whenever most topologies sit
    at the ceiling: coverage at gpt-5.4 is a median of 1.000 but a mean of 0.941, and
    only the second reflects the runs that omitted work.
    """
    num = den = 0
    lo = hi = None
    for t in ORDER:
        row = agg.get((model, t, measure))
        if row is None or row["mean"] is None:
            continue
        num += row["mean"] * row["n"]
        den += row["n"]
        lo = row["mean"] if lo is None else min(lo, row["mean"])
        hi = row["mean"] if hi is None else max(hi, row["mean"])
    if not den:
        return "—", None
    m = num / den
    return f"{fmt.format(m)} ({fmt.format(lo)} to {fmt.format(hi)})", m


def bin_value(model, measure, scope, fmt="{:.4f}", field="median"):
    """One complexity bin's figure for one tier: median across topologies for a
    distribution measure, pooled rate for a rate measure."""
    agg = load(scope)
    if KIND.get(measure) == "rate":
        return pooled(agg, model, measure, scope)
    return dist(agg, model, measure, fmt, field).split(" (")[0]


def headline(model):
    """Run counts, outcome split, stored cost and mean wall time for one tier."""
    with _conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM run WHERE model=? AND excluded_reason IS NULL", (model,))]
    n = len(rows)
    split = {s: sum(1 for r in rows if r["run_status"] == s)
             for s in ("success", "partial", "failed", "no_execution")}
    cost = sum(r["grand_total_cost_usd"] or 0 for r in rows)
    walls = [r["wall_time_s"] for r in rows if r["wall_time_s"]]
    return {"n": n, "split": split, "cost": cost,
            "per_run": cost / n if n else 0,
            "wall": st.mean(walls) if walls else 0}


def excluded(model):
    """How many rows this tier has outside every figure, and why."""
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT excluded_reason, COUNT(*) n FROM run WHERE model=? AND "
            "excluded_reason IS NOT NULL GROUP BY excluded_reason", (model,))]


if __name__ == "__main__":
    agg = load()
    for m in MODELS:
        h = headline(m)
        print(f"{m:14} n={h['n']} cost=${h['cost']:.2f} wall={h['wall']:.0f}s")
        print(f"   cost      {dist(agg, m, 'cost_usd', '${:.4f}')}")
        print(f"   tokens    {dist(agg, m, 'total_tokens', '{:,.0f}')}")
        print(f"   coverage  {dist(agg, m, 'capability_coverage', '{:.3f}', 'mean')}")
        print(f"   violation {pooled(agg, m, 'has_violation')}")
        print(f"   completed {pooled(agg, m, 'completed')}")
        print(f"   quality   {dist(agg, m, 'judge_mean_scope_adj', '{:.3f}')}")
