"""Pulls the figures the tracker worksheets report, straight from agg_topology.

One source for every number written into Thesis_Project_Tracker_v3.xlsx, so a value in
a worksheet and the same value in the run store cannot drift. Reads only; nothing here
writes to the run store or re-executes a run.

Exposes:
    headline(model)      per-tier run counts, outcome split, stored cost, wall time
    topology_row(model, topology)   the per-topology measures Section 3 reports
    spread(model)        quality range, standard deviation and top-6 spread
    spearman(a, b)       rank correlation between two equal-length sequences
"""

import sqlite3
import statistics as st
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "run_store.db"

MODELS = ["gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.4"]

# Fixed reporting order, matching analysis/aggregate.py's TOPOLOGY_ORDER.
ORDER = ["monolith", "sequential", "planner_executor", "static_graph_dag",
         "static_graph_routed", "dynamic_graph", "mesh", "swarm",
         "swarm_constrained_adaptive"]

LABEL = dict(zip(ORDER, ["Monolith", "Sequential", "Planner-Executor", "Static Graph DAG",
                         "Static Graph Routed", "Dynamic Graph", "Mesh", "Swarm",
                         "Swarm Constrained Adaptive"]))

# Which stored field carries each measure. A rate measure has no mean; a distribution
# measure has no rate. Naming it here stops a caller reading the wrong column and
# silently reporting a median where the worksheet says rate.
FIELD = {
    "cost_usd": "median",
    "judge_mean_scope_adj": "median",
    "judge_mean": "median",
    "capability_coverage": "mean",
    "capability_precision": "mean",
    "has_violation": "rate",
    "completed": "rate",
    "declined": "rate",
    "wall_time_s": "median",
    "total_tokens": "median",
}


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def _agg(scope="workload", run_phase=None):
    """agg_topology keyed by (model, topology, measure) for one scope.

    agg_topology is keyed by experiment, and one model can appear in more than one
    experiment -- the pilot at a tier and the main experiment at that same frozen tier.
    Keying on the model alone would then map two rows to one key and keep whichever the
    cursor returned last, silently reporting one campaign's figure under the other's.

    *run_phase* selects which campaign to read. A collision that survives the filter
    raises rather than resolving itself arbitrarily.
    """
    sql = "SELECT * FROM agg_topology WHERE scope=?"
    params = [scope]
    if run_phase:
        sql += " AND run_phase=?"
        params.append(run_phase)

    out, seen = {}, {}
    with _conn() as c:
        for r in c.execute(sql, params):
            key = (r["model"], r["topology"], r["measure"])
            if key in out and seen[key] != r["experiment_no"]:
                raise ValueError(
                    f"{key} appears in experiments {seen[key]} and {r['experiment_no']}. "
                    f"Pass run_phase= to say which campaign this figure is for."
                )
            out[key] = dict(r)
            seen[key] = r["experiment_no"]
    return out


def value(agg, model, topology, measure):
    """One measure for one cell, read from its own correct field. None when absent."""
    row = agg.get((model, topology, measure))
    return None if row is None else row.get(FIELD[measure])


def headline(model, run_phase=None):
    """Per-tier totals across every live run, workload and out-of-scope together.

    Excluded runs are left out, matching aggregate.py's own load_runs() filter, so a
    harness-abort row cannot depress a completion rate it never participated in.

    *run_phase* restricts the count to one campaign. Without it a tier that was run in
    both the pilot and the main experiment totals both together.
    """
    sql = "SELECT * FROM run WHERE model=? AND excluded_reason IS NULL"
    params = [model]
    if run_phase:
        sql += " AND run_phase=?"
        params.append(run_phase)
    with _conn() as c:
        rows = [dict(r) for r in c.execute(sql, params)]
    n = len(rows)
    split = {s: sum(1 for r in rows if r["run_status"] == s)
             for s in ("success", "partial", "failed", "no_execution")}
    cost = sum(r["grand_total_cost_usd"] or 0 for r in rows)
    walls = [r["wall_time_s"] for r in rows if r["wall_time_s"]]
    return {
        "n": n,
        "split": split,
        "completion_pct": split["success"] / n * 100 if n else 0.0,
        "stored_cost": cost,
        "cost_per_run": cost / n if n else 0.0,
        "wall_mean": st.mean(walls) if walls else 0.0,
    }


def spread(agg, model, measure="judge_mean_scope_adj"):
    """How far apart a tier puts the topologies on *measure*.

    top6 is the spread once the three weakest topologies are set aside: a tier can look
    discriminating purely because one condition failed, and the top-6 figure is what
    shows whether the working topologies are actually separated.
    """
    vals = [value(agg, model, t, measure) for t in ORDER]
    vals = [v for v in vals if v is not None]
    top6 = sorted(vals)[-6:]
    return {
        "min": min(vals), "max": max(vals), "range": max(vals) - min(vals),
        "sd": st.pstdev(vals), "n": len(vals),
        "top6_min": min(top6), "top6_max": max(top6),
        "top6_range": max(top6) - min(top6),
    }


def spearman(x, y):
    """Rank correlation between two equal-length sequences. No tie correction --
    the measures this is applied to are continuous and ties are not expected; a tie
    would bias the coefficient slightly toward zero rather than inventing agreement."""
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0] * len(v)
        for pos, i in enumerate(order):
            out[i] = pos + 1
        return out
    rx, ry = ranks(x), ranks(y)
    n = len(x)
    d2 = sum((rx[i] - ry[i]) ** 2 for i in range(n))
    return 1 - 6 * d2 / (n * (n * n - 1))


def rank_stability(agg, measure):
    """Spearman correlation for *measure* between each adjacent pair of tiers, and
    across the full nano-to-gpt-5.4 span. Returns None for any pair where a topology
    is missing the measure, rather than correlating over a shortened list."""
    cols = {}
    for m in MODELS:
        vals = [value(agg, m, t, measure) for t in ORDER]
        if any(v is None for v in vals):
            return None
        cols[m] = vals
    return {
        "nano_mini": spearman(cols["gpt-5.4-nano"], cols["gpt-5.4-mini"]),
        "mini_full": spearman(cols["gpt-5.4-mini"], cols["gpt-5.4"]),
        "nano_full": spearman(cols["gpt-5.4-nano"], cols["gpt-5.4"]),
    }


def triple(agg, topology, measure, fmt="{:.2f}"):
    """The 'nano / mini / 5.4' string Section 3 of the Model Comparison sheet uses."""
    parts = []
    for m in MODELS:
        v = value(agg, m, topology, measure)
        parts.append("—" if v is None else fmt.format(v))
    return " / ".join(parts)


def main():
    agg = _agg()
    for m in MODELS:
        h = headline(m)
        print(f"{m:14} n={h['n']:>3}  {h['split']['success']} success / "
              f"{h['split']['partial']} partial / {h['split']['no_execution']} no-execution  "
              f"completion={h['completion_pct']:.1f}%  stored=${h['stored_cost']:.2f}  "
              f"wall={h['wall_mean']:.0f}s")
    print()
    for t in ORDER:
        print(f"{LABEL[t]:28} cost {triple(agg, t, 'cost_usd', '${:.4f}'):>34}  "
              f"quality {triple(agg, t, 'judge_mean_scope_adj'):>20}")
    print()
    for m in MODELS:
        s = spread(agg, m)
        print(f"{m:14} range={s['range']:.2f} ({s['min']:.2f}-{s['max']:.2f}) "
              f"sd={s['sd']:.3f} top6={s['top6_range']:.2f}")


if __name__ == "__main__":
    main()
