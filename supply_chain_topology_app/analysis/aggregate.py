"""Aggregation pipeline for the topology comparison.

Implements the hierarchy in the proposal, Section 7.3:

    Run  ->  Query  ->  Topology

A run is one execution of a topology on one query. A query is the paired unit of
comparison across topologies. A topology is the condition being compared.

Query level aggregates repeated runs for each (model, topology, query) into a median
with spread, following the rule in Section 7.5. Topology level aggregates the
query-level medians across the workload queries.

The out-of-scope probe is held apart. It requires no capabilities, so capability
coverage, capability precision and scope-adjusted quality are undefined for it, and
its cost measures a refusal rather than a workload. It is reported as a decline rate.
The probe is identified from the query metadata rather than by query id, so renaming
or renumbering the query set does not silently change what is aggregated.

Topology level is also produced per complexity bin, so a topology that holds up on
simple work and breaks down as the workload grows is visible rather than averaged away.
Bin scopes exclude the out-of-scope probe exactly as the workload scope does, which
leaves the low bin covering fewer queries than the others.

Results are written to two tables in long format, one row per measure:

    agg_query      keyed by model, topology, query, measure
    agg_topology   keyed by model, topology, scope, measure
                   scope is 'workload', 'bin:<name>', or 'out_of_scope'

Long format keeps the tables readable as the measure set grows, and lets a chart or a
table select the measures it needs without a schema change.

Usage:
    python -m analysis.aggregate                     # aggregate every model in the store
    python -m analysis.aggregate --model gpt-5.4-nano
    python -m analysis.aggregate --print             # also print a topology summary
    python -m analysis.aggregate --bins              # also print the complexity-bin breakdown
"""

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from analysis import measures, summarise
from measurement.run_store_schema import DB_PATH

# Reporting order for topologies, used by every printed table and by the tracker
# worksheets built from this output. It is deliberately not the registry order: the
# registry lists topologies in the order they were built, while this runs from the
# simplest coordination design to the most decentralised, which is the order the
# thesis discusses them in. Keeping one constant here means the print and the
# spreadsheets cannot drift apart.
#
# A topology absent from this tuple is not dropped. It sorts to the end, so adding one
# to the registry without updating this list degrades the ordering rather than hiding
# the condition from every report.
TOPOLOGY_ORDER = (
    "monolith",
    "sequential",
    "planner_executor",
    "static_graph_dag",
    "static_graph_routed",
    "dynamic_graph",
    "mesh",
    "swarm",
    "swarm_constrained_adaptive",
)


def topology_sort_key(topology: str) -> tuple:
    """Position in TOPOLOGY_ORDER, or the end of the list for an unlisted topology."""
    try:
        return (0, TOPOLOGY_ORDER.index(topology))
    except ValueError:
        return (1, topology)


# Measures summarised by median with spread. Grouped to match the proposal's own
# measure groups, so a row here is traceable to a named measure in Section 7.2.
CONTINUOUS = (
    # Cost (7.2.1)
    "cost_usd",
    "specialist_cost_usd",
    "orchestration_cost_usd",
    "master_cost_usd",
    # Tokens (7.2.1, 7.3)
    "total_tokens",
    "generated_tokens",
    "prompt_tokens",
    "specialist_tokens",
    "orchestration_tokens",
    "master_tokens",
    # Latency and execution timing (7.2.1, 7.2.2)
    "wall_time_s",
    "critical_path_s",
    "actual_span_s",
    "coordinator_idle_s",
    "achievable_concurrency",
    "actual_concurrency",
    "scheduling_deviation",
    # Cost anatomy (7.2.2, 7.3)
    "orchestration_tax",
    "specialist_token_share",
    "orchestration_token_share",
    "master_token_share",
    # Reliability (7.2.2)
    "capability_coverage",
    "capability_precision",
    # Quality (7.2.3)
    "judge_mean",
    "judge_mean_scope_adj",
)

# Measures summarised as a proportion of runs.
RATES = (
    "has_violation",
    "completed",
    "infeasible_overlap",
)

# Bounded measures whose usual value is the optimum, mapped to that optimum.
#
# The median is the wrong topology-level summary for these. Most queries sit at the
# optimum, so the median across a topology's queries reports the optimum regardless of
# how the remaining queries behaved. In the nano pilot the median capability coverage
# is 1.000 for all nine topologies, which ties every condition, while the mean spans
# 0.714 to 1.000 and separates them.
#
# Three figures are therefore published for each: the median for continuity with the
# other measures, the mean, which carries the magnitude of the shortfall, and the
# proportion of queries sitting exactly at the optimum, which is the most readable of
# the three. The query-level medians feed the paired statistical tests unchanged, so
# this affects how results are described rather than how they are compared.
BOUNDED_OPTIMUM = {
    "capability_coverage": 1.0,
    "capability_precision": 1.0,
    "scheduling_deviation": 0.0,
}

_SCHEMA = """
DROP TABLE IF EXISTS agg_query;
CREATE TABLE agg_query (
    model           TEXT NOT NULL,
    topology        TEXT NOT NULL,
    query_id        TEXT NOT NULL,
    complexity_bin  TEXT,
    is_out_of_scope INTEGER NOT NULL,   -- 1 for the probe that requires no capabilities
    measure         TEXT NOT NULL,
    kind            TEXT NOT NULL,      -- continuous | rate
    n               INTEGER NOT NULL,   -- runs contributing to this figure
    median          REAL,
    mean            REAL,
    min             REAL,
    max             REAL,
    q1              REAL,               -- populated only when n >= 5 (Section 7.5)
    q3              REAL,
    rate            REAL,               -- populated for kind = rate
    count           INTEGER,
    PRIMARY KEY (model, topology, query_id, measure)
);

DROP TABLE IF EXISTS agg_topology;
CREATE TABLE agg_topology (
    model     TEXT NOT NULL,
    topology  TEXT NOT NULL,
    scope     TEXT NOT NULL,            -- workload | out_of_scope
    measure   TEXT NOT NULL,
    kind      TEXT NOT NULL,
    n         INTEGER NOT NULL,         -- queries contributing, or runs for a pooled rate
    median    REAL,
    mean      REAL,
    min       REAL,
    max       REAL,
    q1        REAL,
    q3        REAL,
    rate      REAL,
    count     INTEGER,
    PRIMARY KEY (model, topology, scope, measure)
);
"""


# ---------------------------------------------------------------------------
# Layer 1 — run level
# ---------------------------------------------------------------------------
def load_runs(conn: sqlite3.Connection, model: str | None = None) -> list[dict]:
    """Read every run with its quality scores and query metadata, and derive measures.

    Tool names are fetched per run so the capability sets can be built. Runs are
    returned with the derived measures already attached.
    """
    conn.row_factory = sqlite3.Row
    # Excluded runs are dropped here, once, so no downstream measure has to know about
    # them (proposal Section 7.4). The row stays in the store; only the analysis skips it.
    where = "WHERE r.excluded_reason IS NULL"
    params: tuple = ()
    if model:
        where += " AND r.model = ?"
        params = (model,)

    rows = conn.execute(f"""
        SELECT r.*, q.judge_mean, q.judge_mean_scope_adj,
               m.implied_tools_json, m.complexity_bin
        FROM run r
        LEFT JOIN quality_scores q ON q.run_id = r.run_id
        LEFT JOIN query_metadata m ON m.query_id = r.query_id
        {where}
        ORDER BY r.model, r.topology, r.query_id, r.run_n
    """, params).fetchall()

    # One query for all tool names, grouped in memory, rather than one query per run.
    names_by_run: dict[str, list[str]] = defaultdict(list)
    for run_id, tool_name in conn.execute(
        "SELECT run_id, tool_name FROM tool_call ORDER BY run_id, call_order"
    ):
        names_by_run[run_id].append(tool_name)

    # Specialist token and cost sums per run. These are the tool_call half of the run
    # total; the master and orchestration halves sit on the run row itself.
    tokens_by_run: dict[str, tuple[int, int, float]] = {}
    for run_id, p_tok, c_tok, cost in conn.execute(
        "SELECT run_id, COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), "
        "COALESCE(SUM(cost_usd),0) FROM tool_call GROUP BY run_id"
    ):
        tokens_by_run[run_id] = (p_tok, c_tok, cost)

    derived = []
    for row in rows:
        record = dict(row)
        sp_prompt, sp_completion, sp_cost = tokens_by_run.get(record["run_id"], (0, 0, 0.0))
        item = measures.derive(record, names_by_run.get(record["run_id"], []),
                               record.get("implied_tools_json"),
                               specialist_prompt=sp_prompt,
                               specialist_completion=sp_completion,
                               specialist_cost=sp_cost)
        item["complexity_bin"] = record.get("complexity_bin")
        # A query requiring no capabilities is the out-of-scope probe. Identified from
        # metadata so the aggregation does not depend on a query id staying fixed.
        item["is_out_of_scope"] = 1 if not item["required_capabilities"] else 0
        derived.append(item)
    return derived


# ---------------------------------------------------------------------------
# Layer 2 — query level
# ---------------------------------------------------------------------------
def aggregate_queries(runs: list[dict]) -> list[dict]:
    """Aggregate repeated runs into one row per (model, topology, query, measure)."""
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for r in runs:
        grouped[(r["model"], r["topology"], r["query_id"])].append(r)

    out = []
    for (model, topology, query_id), group in grouped.items():
        common = {
            "model": model, "topology": topology, "query_id": query_id,
            "complexity_bin": group[0]["complexity_bin"],
            "is_out_of_scope": group[0]["is_out_of_scope"],
        }

        for name in CONTINUOUS:
            stats = summarise.summarise([g[name] for g in group])
            out.append({**common, "measure": name, "kind": "continuous", **stats,
                        "rate": None, "count": None})

        for name in RATES:
            stats = summarise.rate([g[name] for g in group])
            out.append({**common, "measure": name, "kind": "rate",
                        "n": stats["n"], "median": None, "mean": None, "min": None, "max": None,
                        "q1": None, "q3": None,
                        "rate": stats["rate"], "count": stats["count"]})

        # Scheduling efficiency is defined only on dependency-feasible runs. It is
        # reported for readability against the run logs and is not used for ranking,
        # because restricting to feasible runs scores a frequently-breaching topology
        # on a flattering subset of its own runs (proposal Section 7.2.2).
        feasible = [g["scheduling_ratio"] for g in group if g["infeasible_overlap"] == 0]
        stats = summarise.summarise(feasible)
        out.append({**common, "measure": "scheduling_efficiency", "kind": "continuous",
                    **stats, "rate": None, "count": None})

        # Declining is the correct outcome on the out-of-scope probe only. It is
        # recorded for every query so an unexpected decline elsewhere stays visible.
        stats = summarise.rate([g["declined"] for g in group])
        out.append({**common, "measure": "declined", "kind": "rate",
                    "n": stats["n"], "median": None, "mean": None, "min": None, "max": None,
                    "q1": None, "q3": None,
                    "rate": stats["rate"], "count": stats["count"]})

    return out


# ---------------------------------------------------------------------------
# Layer 3 — topology level
# ---------------------------------------------------------------------------
def _summarise_scope(model: str, topology: str, scope: str, measure: str, kind: str,
                     rows: list[dict]) -> list[dict]:
    """Collapse one group of query-level rows into the output rows for a single scope.

    Shared by the workload scope and by each complexity-bin scope, which aggregate
    identically and differ only in which queries they group. Returns a list because a
    bounded measure emits its at-optimum companion row alongside the main row.
    """
    common = {"model": model, "topology": topology, "scope": scope,
              "measure": measure, "kind": kind}

    if kind != "continuous":
        # Pool the underlying runs rather than averaging the per-query rates, so a
        # query with more repetitions carries its proper weight in the denominator.
        total_n = sum(r["n"] for r in rows)
        total_count = sum(r["count"] or 0 for r in rows)
        return [{**common, "n": total_n, "median": None, "mean": None, "min": None,
                 "max": None, "q1": None, "q3": None,
                 "rate": round(total_count / total_n, 4) if total_n else None,
                 "count": total_count}]

    values = [r["median"] for r in rows]
    out = [{**common, **summarise.summarise(values), "rate": None, "count": None}]

    # For a bounded measure whose usual value is the optimum, publish the proportion of
    # queries sitting exactly there. The median alone reports the optimum for every
    # topology and separates none of them.
    if measure in BOUNDED_OPTIMUM:
        optimum = BOUNDED_OPTIMUM[measure]
        flags = [1 if abs(v - optimum) < 1e-9 else 0 for v in values if v is not None]
        at_opt = summarise.rate(flags)
        out.append({"model": model, "topology": topology, "scope": scope,
                    "measure": f"{measure}_at_optimum", "kind": "rate",
                    "n": at_opt["n"], "median": None, "mean": None,
                    "min": None, "max": None, "q1": None, "q3": None,
                    "rate": at_opt["rate"], "count": at_opt["count"]})
    return out


def aggregate_topologies(query_aggs: list[dict], runs: list[dict]) -> list[dict]:
    """Aggregate query-level figures into one row per (model, topology, scope, measure).

    Continuous measures take the median across the query-level medians, so every query
    contributes equally regardless of how many times it was repeated. Rates are pooled
    across runs, because a rate's denominator is the run.

    Three kinds of scope are produced:

        workload        the 10 substantive queries, the primary unit of comparison
        bin:<name>      those same queries split by complexity_bin, so a topology that
                        holds up on simple work and breaks down on complex work is
                        visible rather than averaged away
        out_of_scope    the probe requiring no capabilities, reported as a decline rate

    The bin scopes exclude the out-of-scope probe, exactly as the workload scope does.
    The probe sits in the low bin, so the low bin covers fewer queries than the others
    and its figures rest on a correspondingly smaller denominator. Every row carries its
    own n for that reason.
    """
    out = []

    # --- workload queries: median across query-level medians ---
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in query_aggs:
        if row["is_out_of_scope"]:
            continue
        grouped[(row["model"], row["topology"], row["measure"], row["kind"])].append(row)

    for (model, topology, measure, kind), rows in grouped.items():
        out.extend(_summarise_scope(model, topology, "workload", measure, kind, rows))

    # --- complexity bins: the same queries, split by how much work each demands ---
    binned: dict[tuple, list[dict]] = defaultdict(list)
    for row in query_aggs:
        if row["is_out_of_scope"] or not row["complexity_bin"]:
            continue
        binned[(row["model"], row["topology"], row["complexity_bin"],
                row["measure"], row["kind"])].append(row)

    for (model, topology, bin_name, measure, kind), rows in binned.items():
        out.extend(_summarise_scope(model, topology, f"bin:{bin_name}", measure, kind, rows))

    # --- out-of-scope probe: reported as a decline rate, kept separate ---
    probe: dict[tuple, list[dict]] = defaultdict(list)
    for r in runs:
        if r["is_out_of_scope"]:
            probe[(r["model"], r["topology"])].append(r)

    for (model, topology), group in probe.items():
        stats = summarise.rate([g["declined"] for g in group])
        out.append({"model": model, "topology": topology, "scope": "out_of_scope",
                    "measure": "declined", "kind": "rate", "n": stats["n"],
                    "median": None, "mean": None, "min": None, "max": None,
                    "q1": None, "q3": None,
                    "rate": stats["rate"], "count": stats["count"]})

        cost = summarise.summarise([g["cost_usd"] for g in group])
        out.append({"model": model, "topology": topology, "scope": "out_of_scope",
                    "measure": "cost_usd", "kind": "continuous", **cost,
                    "rate": None, "count": None})

    return out


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def write_tables(conn: sqlite3.Connection, query_aggs: list[dict],
                 topology_aggs: list[dict], scoped_to_model: bool = False) -> None:
    """Replace the aggregate tables with a freshly computed set.

    With *scoped_to_model* False the tables are dropped and rebuilt whole. Both are
    fully derived from the run store, so a rebuild cannot lose measurement data, and it
    guarantees no row survives from a superseded measure definition.

    With *scoped_to_model* True only the rows belonging to the models present in the new
    data are replaced, and every other model's rows are left alone. Without this, running
    with --model to look at one tier silently deleted every other tier's rows: the tables
    were rebuilt from a run set that had been filtered to one model, so the aggregates
    for the others vanished until a full re-run. Found 6-Sep-26 while pulling
    complexity-bin figures, after a --model gpt-5.4-nano call emptied the mini and
    gpt-5.4 aggregates.
    """
    if not scoped_to_model:
        conn.executescript(_SCHEMA)
    else:
        # Create the tables if this is a first run, then clear only the affected models.
        conn.executescript(_SCHEMA.replace("DROP TABLE IF EXISTS agg_query;", "")
                                  .replace("DROP TABLE IF EXISTS agg_topology;", "")
                                  .replace("CREATE TABLE agg_query",
                                           "CREATE TABLE IF NOT EXISTS agg_query")
                                  .replace("CREATE TABLE agg_topology",
                                           "CREATE TABLE IF NOT EXISTS agg_topology"))
        models = {row["model"] for row in query_aggs} | {row["model"] for row in topology_aggs}
        for model in models:
            conn.execute("DELETE FROM agg_query WHERE model = ?", (model,))
            conn.execute("DELETE FROM agg_topology WHERE model = ?", (model,))

    conn.executemany(
        "INSERT INTO agg_query (model, topology, query_id, complexity_bin, "
        "is_out_of_scope, measure, kind, n, median, mean, min, max, q1, q3, rate, count) "
        "VALUES (:model, :topology, :query_id, :complexity_bin, :is_out_of_scope, "
        ":measure, :kind, :n, :median, :mean, :min, :max, :q1, :q3, :rate, :count)",
        query_aggs,
    )
    conn.executemany(
        "INSERT INTO agg_topology (model, topology, scope, measure, kind, n, "
        "median, mean, min, max, q1, q3, rate, count) "
        "VALUES (:model, :topology, :scope, :measure, :kind, :n, "
        ":median, :mean, :min, :max, :q1, :q3, :rate, :count)",
        topology_aggs,
    )
    conn.commit()


def print_summary(conn: sqlite3.Connection, model: str | None) -> None:
    """Print every proposal measure per topology, in two aligned blocks.

    All fifteen measures from the proposal's Section 7.3 table are printed. They are
    split across two blocks purely so each line stays readable: one for the operational
    measures (cost, tokens, latency) and one for reliability and quality. Both blocks
    list the topologies in the same order, so the rows line up between them.
    """
    where = "AND model = ?" if model else ""
    params = (model,) if model else ()
    rows = conn.execute(f"""
        SELECT model, topology,
               MAX(CASE WHEN measure='cost_usd' THEN n END) AS queries,
               MAX(CASE WHEN measure='cost_usd' THEN median END) AS cost,
               MAX(CASE WHEN measure='total_tokens' THEN median END) AS total_tok,
               MAX(CASE WHEN measure='generated_tokens' THEN median END) AS gen_tok,
               MAX(CASE WHEN measure='wall_time_s' THEN median END) AS latency,
               MAX(CASE WHEN measure='critical_path_s' THEN median END) AS critpath,
               MAX(CASE WHEN measure='orchestration_token_share' THEN median END) AS orch_share,
               MAX(CASE WHEN measure='specialist_token_share' THEN median END) AS spec_share,
               MAX(CASE WHEN measure='judge_mean_scope_adj' THEN median END) AS quality,
               MAX(CASE WHEN measure='capability_coverage' THEN mean END) AS coverage,
               MAX(CASE WHEN measure='capability_coverage_at_optimum' THEN rate END) AS cover_opt,
               MAX(CASE WHEN measure='capability_precision' THEN mean END) AS precision,
               MAX(CASE WHEN measure='capability_precision_at_optimum' THEN rate END) AS prec_opt,
               MAX(CASE WHEN measure='scheduling_efficiency' THEN median END) AS sched_eff,
               MAX(CASE WHEN measure='scheduling_deviation' THEN mean END) AS deviation,
               MAX(CASE WHEN measure='infeasible_overlap' THEN rate END) AS infeasible,
               MAX(CASE WHEN measure='has_violation' THEN rate END) AS violation_rate,
               MAX(CASE WHEN measure='completed' THEN rate END) AS completion_rate
        FROM agg_topology WHERE scope='workload' {where}
        GROUP BY model, topology
    """, params).fetchall()

    # Sorted here rather than in SQL: the reporting order is a fixed list, not a
    # value the query can sort on.
    rows = sorted(rows, key=lambda r: (r[0], topology_sort_key(r[1])))

    if not rows:
        print("No aggregated rows to show. Check that the store holds runs for this model.")
        return

    # The out-of-scope decline rate lives in its own scope, so it is fetched separately
    # and joined in memory rather than forced into the workload query above.
    decline = {(r[0], r[1]): r[2] for r in conn.execute(f"""
        SELECT model, topology, rate FROM agg_topology
        WHERE scope='out_of_scope' AND measure='declined' {where}
    """, params)}

    def fmt(v, spec=".2f"):
        return format(v, spec) if v is not None else "n/a"

    def tok(v):
        return f"{v:,.0f}" if v is not None else "n/a"

    print("Cost, tokens, latency and quality are medians across the 10 workload queries.")
    print("Coverage, precision and scheduling deviation are means, with the share of "
          "queries at the optimum beside each.")
    print("Rates pool the underlying runs. 'qrys' is the denominator: a topology that "
          "failed some queries carries a smaller one, and its cost excludes those runs.")
    print()
    print("=" * 118)
    print("OPERATIONAL MEASURES  (proposal 7.2.1)")
    print("=" * 118)
    h1 = (f"{'model':14} {'topology':28} {'qrys':>4} {'cost':>8} {'tokens':>9} "
          f"{'gen tok':>8} {'latency':>8} {'critpath':>8} {'orch sh':>8} {'spec sh':>8}")
    print(h1)
    print("-" * len(h1))
    for r in rows:
        print(f"{r[0]:14} {r[1]:28} {r[2]:>4} {fmt(r[3], '.4f'):>8} {tok(r[4]):>9} "
              f"{tok(r[5]):>8} {fmt(r[6], '.1f'):>8} {fmt(r[7], '.1f'):>8} "
              f"{fmt(r[8], '.3f'):>8} {fmt(r[9], '.3f'):>8}")

    print()
    print("=" * 118)
    print("RELIABILITY AND QUALITY MEASURES  (proposal 7.2.2, 7.2.3)")
    print("=" * 118)
    h2 = (f"{'model':14} {'topology':28} {'qual':>6} {'cover':>6} {'@opt':>5} "
          f"{'prec':>6} {'@opt':>5} {'schEff':>7} {'schDev':>7} {'infeas':>7} "
          f"{'viol':>5} {'compl':>6} {'declQ1':>7}")
    print(h2)
    print("-" * len(h2))
    for r in rows:
        d = decline.get((r[0], r[1]))
        print(f"{r[0]:14} {r[1]:28} {fmt(r[10]):>6} {fmt(r[11], '.3f'):>6} "
              f"{fmt(r[12]):>5} {fmt(r[13], '.3f'):>6} {fmt(r[14]):>5} "
              f"{fmt(r[15], '.3f'):>7} {fmt(r[16], '.3f'):>7} {fmt(r[17]):>7} "
              f"{fmt(r[18]):>5} {fmt(r[19]):>6} {fmt(d):>7}")


def print_bins(conn: sqlite3.Connection, model: str | None) -> None:
    """Print the complexity-bin breakdown: how each topology holds up as work grows."""
    where = "AND model = ?" if model else ""
    params = (model,) if model else ()
    rows = conn.execute(f"""
        SELECT model, topology, scope,
               MAX(CASE WHEN measure='cost_usd' THEN n END) AS queries,
               MAX(CASE WHEN measure='completed' THEN n END) AS runs,
               MAX(CASE WHEN measure='cost_usd' THEN median END) AS cost,
               MAX(CASE WHEN measure='judge_mean_scope_adj' THEN median END) AS quality,
               MAX(CASE WHEN measure='capability_coverage' THEN mean END) AS coverage,
               MAX(CASE WHEN measure='scheduling_deviation' THEN mean END) AS deviation,
               MAX(CASE WHEN measure='has_violation' THEN rate END) AS violation_rate,
               MAX(CASE WHEN measure='completed' THEN rate END) AS completion_rate
        FROM agg_topology WHERE scope LIKE 'bin:%' {where}
        GROUP BY model, topology, scope ORDER BY model, topology, scope
    """, params).fetchall()

    if not rows:
        print("No complexity-bin rows to show. Check that query_metadata carries a "
              "complexity_bin for the queries in this store.")
        return

    print("Complexity bins hold the same measures as the workload scope, restricted to "
          "the queries in each bin.")
    print("The out-of-scope probe is excluded, so the low bin covers fewer queries than "
          "the others. Read every row against its own counts.")
    print("'runs' counts every run attempted in the bin. 'qrys' counts the queries that "
          "produced a usable cost figure, so it drops to 0 where every run failed while "
          "the rate columns still carry their run denominators.")
    print()
    header = (f"{'model':14} {'topology':28} {'bin':>10} {'runs':>4} {'qrys':>4} "
              f"{'cost':>7} {'qual':>6} {'cover':>6} {'schdev':>7} {'viol':>5} {'compl':>6}")
    print(header)
    print("-" * len(header))

    # Bins print in workload order rather than alphabetically, so the progression from
    # least to most demanding reads down the column.
    bin_order = {"bin:low": 0, "bin:medium": 1, "bin:high": 2, "bin:very_high": 3}
    for r in sorted(rows, key=lambda x: (x[0], topology_sort_key(x[1]),
                                         bin_order.get(x[2], 99))):
        def fmt(v, spec=".2f"):
            return format(v, spec) if v is not None else "n/a"
        print(f"{r[0]:14} {r[1]:28} {r[2].replace('bin:', ''):>10} {r[4]:>4} {r[3]:>4} "
              f"{fmt(r[5], '.4f'):>7} {fmt(r[6]):>6} {fmt(r[7], '.3f'):>6} "
              f"{fmt(r[8], '.3f'):>7} {fmt(r[9]):>5} {fmt(r[10]):>6}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DB_PATH, help=f"run store path (default: {DB_PATH})")
    parser.add_argument("--model", default=None,
                        help="aggregate one model tier only (default: every model in the store)")
    parser.add_argument("--print", dest="show", action="store_true",
                        help="print the topology-level workload summary after writing")
    parser.add_argument("--bins", dest="show_bins", action="store_true",
                        help="print the complexity-bin breakdown after writing")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"ERROR: run store not found at {args.db}. Check the path, or run an experiment first.")
        return 1

    conn = sqlite3.connect(args.db)
    try:
        runs = load_runs(conn, args.model)
        if not runs:
            scope = f" for model {args.model}" if args.model else ""
            print(f"No runs found{scope}. Nothing to aggregate.")
            return 1

        query_aggs = aggregate_queries(runs)
        topology_aggs = aggregate_topologies(query_aggs, runs)
        # A filtered run set must not rebuild the whole table -- see write_tables.
        write_tables(conn, query_aggs, topology_aggs,
                     scoped_to_model=args.model is not None)

        models = sorted({r["model"] for r in runs})
        topologies = sorted({r["topology"] for r in runs})
        print(f"  runs aggregated      : {len(runs)}")
        print(f"  models               : {', '.join(models)}")
        print(f"  topologies           : {len(topologies)}")
        print(f"  agg_query rows       : {len(query_aggs)}")
        print(f"  agg_topology rows    : {len(topology_aggs)}")

        if args.show:
            print()
            print_summary(conn, args.model)
        if args.show_bins:
            print()
            print_bins(conn, args.model)
    except sqlite3.Error as exc:
        print(f"ERROR: the run store could not be read or written: {exc}")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
