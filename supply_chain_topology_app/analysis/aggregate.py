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
    # Behaviour when a run executed none of the capabilities its query required. Four
    # mutually exclusive classes, reported as separate rates rather than combined into
    # one score, because they are qualitatively different failures: answering without
    # evidence is not the same kind of error as declining work that was in scope.
    "answered_without_execution",
    "wrongful_decline",
    "clarification_request",
    "empty_output",
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

# Both tables are keyed by experiment, not by model. An experiment is one run_phase at
# one model, so keying on the model alone pooled the pilot and the main experiment at the
# frozen tier into a single row -- two campaigns, different N, silently averaged.
# run_phase and model are carried alongside so a row is readable, and filterable, without
# joining back to the experiment table.
_SCHEMA = """
DROP TABLE IF EXISTS agg_query;
CREATE TABLE agg_query (
    experiment_no   INTEGER NOT NULL,
    run_phase       TEXT NOT NULL,
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
    PRIMARY KEY (experiment_no, topology, query_id, measure)
);

DROP TABLE IF EXISTS agg_topology;
CREATE TABLE agg_topology (
    experiment_no INTEGER NOT NULL,
    run_phase TEXT NOT NULL,
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
    PRIMARY KEY (experiment_no, topology, scope, measure)
);
"""


# ---------------------------------------------------------------------------
# Layer 1 — run level
# ---------------------------------------------------------------------------
def load_runs(conn: sqlite3.Connection, model: str | None = None,
              run_phase: str | None = None, experiment_no: int | None = None) -> list[dict]:
    """Read every run with its quality scores and query metadata, and derive measures.

    Tool names are fetched per run so the capability sets can be built. Runs are
    returned with the derived measures already attached.

    Filter by experiment number, or by run phase and model, or by neither. The three
    arguments combine, so --run-phase alone is a valid way to look at every tier in one
    phase.
    """
    conn.row_factory = sqlite3.Row
    # Excluded runs are dropped here, once, so no downstream measure has to know about
    # them (proposal Section 7.4). The row stays in the store; only the analysis skips it.
    #
    # A run with no experiment_no is dropped too. That is an ad-hoc execute_topology.sh
    # invocation, which belongs to no campaign, so it has no denominator to join and must
    # not reach a reported figure.
    where = "WHERE r.excluded_reason IS NULL AND r.experiment_no IS NOT NULL"
    params: list = []
    if experiment_no is not None:
        where += " AND r.experiment_no = ?"
        params.append(experiment_no)
    if run_phase:
        where += " AND r.run_phase = ?"
        params.append(run_phase)
    if model:
        where += " AND r.model = ?"
        params.append(model)

    rows = conn.execute(f"""
        SELECT r.*, q.judge_mean, q.judge_mean_scope_adj,
               m.implied_tools_json, m.complexity_bin
        FROM run r
        LEFT JOIN quality_scores q ON q.run_id = r.run_id
        LEFT JOIN query_metadata m ON m.query_id = r.query_id
        {where}
        ORDER BY r.experiment_no, r.topology, r.query_id, r.run_n
    """, tuple(params)).fetchall()

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
    """Aggregate repeated runs into one row per (experiment, topology, query, measure).

    run_n is deliberately absent from the key: pooling a pair's repetitions is what this
    layer is for. experiment_no bounds that pooling, so the pilot's N=1 at a tier and the
    main experiment's N=3 at the same tier stay two rows rather than one.
    """
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for r in runs:
        grouped[(r["experiment_no"], r["topology"], r["query_id"])].append(r)

    out = []
    for (experiment_no, topology, query_id), group in grouped.items():
        common = {
            "experiment_no": experiment_no,
            "run_phase": group[0]["run_phase"], "model": group[0]["model"],
            "topology": topology, "query_id": query_id,
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
def _summarise_scope(ident: dict, topology: str, scope: str, measure: str, kind: str,
                     rows: list[dict]) -> list[dict]:
    """Collapse one group of query-level rows into the output rows for a single scope.

    *ident* carries experiment_no, run_phase and model — the campaign these rows belong
    to, passed as one dict so the three stay together and cannot drift apart.

    Shared by the workload scope and by each complexity-bin scope, which aggregate
    identically and differ only in which queries they group. Returns a list because a
    bounded measure emits its at-optimum companion row alongside the main row.
    """
    common = {**ident, "topology": topology, "scope": scope,
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
        out.append({**ident, "topology": topology, "scope": scope,
                    "measure": f"{measure}_at_optimum", "kind": "rate",
                    "n": at_opt["n"], "median": None, "mean": None,
                    "min": None, "max": None, "q1": None, "q3": None,
                    "rate": at_opt["rate"], "count": at_opt["count"]})
    return out


def aggregate_topologies(query_aggs: list[dict], runs: list[dict]) -> list[dict]:
    """Aggregate query-level figures into one row per (experiment, topology, scope, measure).

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

    def _ident(row: dict) -> dict:
        return {"experiment_no": row["experiment_no"], "run_phase": row["run_phase"],
                "model": row["model"]}

    # --- workload queries: median across query-level medians ---
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in query_aggs:
        if row["is_out_of_scope"]:
            continue
        grouped[(row["experiment_no"], row["topology"], row["measure"], row["kind"])].append(row)

    for (_, topology, measure, kind), rows in grouped.items():
        out.extend(_summarise_scope(_ident(rows[0]), topology, "workload", measure, kind, rows))

    # --- complexity bins: the same queries, split by how much work each demands ---
    binned: dict[tuple, list[dict]] = defaultdict(list)
    for row in query_aggs:
        if row["is_out_of_scope"] or not row["complexity_bin"]:
            continue
        binned[(row["experiment_no"], row["topology"], row["complexity_bin"],
                row["measure"], row["kind"])].append(row)

    for (_, topology, bin_name, measure, kind), rows in binned.items():
        out.extend(_summarise_scope(_ident(rows[0]), topology, f"bin:{bin_name}",
                                    measure, kind, rows))

    # --- out-of-scope probe: reported as a decline rate, kept separate ---
    probe: dict[tuple, list[dict]] = defaultdict(list)
    for r in runs:
        if r["is_out_of_scope"]:
            probe[(r["experiment_no"], r["topology"])].append(r)

    for (_, topology), group in probe.items():
        ident = _ident(group[0])
        stats = summarise.rate([g["declined"] for g in group])
        out.append({**ident, "topology": topology, "scope": "out_of_scope",
                    "measure": "declined", "kind": "rate", "n": stats["n"],
                    "median": None, "mean": None, "min": None, "max": None,
                    "q1": None, "q3": None,
                    "rate": stats["rate"], "count": stats["count"]})

        cost = summarise.summarise([g["cost_usd"] for g in group])
        out.append({**ident, "topology": topology, "scope": "out_of_scope",
                    "measure": "cost_usd", "kind": "continuous", **cost,
                    "rate": None, "count": None})

    return out


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def write_tables(conn: sqlite3.Connection, query_aggs: list[dict],
                 topology_aggs: list[dict], scoped: bool = False) -> None:
    """Replace the aggregate tables with a freshly computed set.

    With *scoped* False the tables are dropped and rebuilt whole. Both are fully derived
    from the run store, so a rebuild cannot lose measurement data, and it guarantees no
    row survives from a superseded measure definition.

    With *scoped* True only the rows belonging to the experiments present in the new data
    are replaced, and every other experiment's rows are left alone. Without this, running
    a filter to look at one campaign silently deleted every other campaign's rows: the
    tables were rebuilt from a run set that had been filtered, so the aggregates for the
    others vanished until a full re-run. Found 6-Sep-26 while pulling complexity-bin
    figures, after a --model gpt-5.4-nano call emptied the mini and gpt-5.4 aggregates.
    """
    if not scoped:
        conn.executescript(_SCHEMA)
    else:
        # Create the tables if this is a first run, then clear only the affected
        # experiments.
        conn.executescript(_SCHEMA.replace("DROP TABLE IF EXISTS agg_query;", "")
                                  .replace("DROP TABLE IF EXISTS agg_topology;", "")
                                  .replace("CREATE TABLE agg_query",
                                           "CREATE TABLE IF NOT EXISTS agg_query")
                                  .replace("CREATE TABLE agg_topology",
                                           "CREATE TABLE IF NOT EXISTS agg_topology"))
        experiments = ({row["experiment_no"] for row in query_aggs}
                       | {row["experiment_no"] for row in topology_aggs})
        for exp in experiments:
            conn.execute("DELETE FROM agg_query WHERE experiment_no = ?", (exp,))
            conn.execute("DELETE FROM agg_topology WHERE experiment_no = ?", (exp,))

    conn.executemany(
        "INSERT INTO agg_query (experiment_no, run_phase, model, topology, query_id, "
        "complexity_bin, is_out_of_scope, measure, kind, n, median, mean, min, max, "
        "q1, q3, rate, count) "
        "VALUES (:experiment_no, :run_phase, :model, :topology, :query_id, "
        ":complexity_bin, :is_out_of_scope, :measure, :kind, :n, :median, :mean, :min, "
        ":max, :q1, :q3, :rate, :count)",
        query_aggs,
    )
    conn.executemany(
        "INSERT INTO agg_topology (experiment_no, run_phase, model, topology, scope, "
        "measure, kind, n, median, mean, min, max, q1, q3, rate, count) "
        "VALUES (:experiment_no, :run_phase, :model, :topology, :scope, :measure, :kind, "
        ":n, :median, :mean, :min, :max, :q1, :q3, :rate, :count)",
        topology_aggs,
    )
    conn.commit()


def _agg_filter(experiment_no: int | None, run_phase: str | None,
                model: str | None) -> tuple[str, tuple]:
    """SQL fragment and parameters restricting an agg_* query to one campaign.

    Returns ("", ()) when nothing was asked for, so the caller's WHERE clause is
    unaffected. The three filters combine, matching load_runs().
    """
    clauses, params = [], []
    if experiment_no is not None:
        clauses.append("experiment_no = ?")
        params.append(experiment_no)
    if run_phase:
        clauses.append("run_phase = ?")
        params.append(run_phase)
    if model:
        clauses.append("model = ?")
        params.append(model)
    return ("".join(f" AND {c}" for c in clauses), tuple(params))


def print_summary(conn: sqlite3.Connection, where: str = "", params: tuple = ()) -> None:
    """Print every proposal measure per topology, in two aligned blocks.

    All fifteen measures from the proposal's Section 7.3 table are printed. They are
    split across two blocks purely so each line stays readable: one for the operational
    measures (cost, tokens, latency) and one for reliability and quality. Both blocks
    list the topologies in the same order, so the rows line up between them.

    *where* and *params* come from _agg_filter() and restrict the output to one campaign.
    """
    rows = conn.execute(f"""
        SELECT experiment_no, run_phase, model, topology,
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
        GROUP BY experiment_no, topology
    """, params).fetchall()

    # Sorted here rather than in SQL: the reporting order is a fixed list, not a
    # value the query can sort on.
    rows = sorted(rows, key=lambda r: (r[0], topology_sort_key(r[3])))

    if not rows:
        print("No aggregated rows to show. Check that the store holds runs for this "
              "experiment.")
        return

    # The out-of-scope decline rate lives in its own scope, so it is fetched separately
    # and joined in memory rather than forced into the workload query above.
    decline = {(r[0], r[1]): r[2] for r in conn.execute(f"""
        SELECT experiment_no, topology, rate FROM agg_topology
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
    h1 = (f"{'exp':>3} {'phase':6} {'model':14} {'topology':28} {'qrys':>4} {'cost':>8} "
          f"{'tokens':>9} {'gen tok':>8} {'latency':>8} {'critpath':>8} {'orch sh':>8} "
          f"{'spec sh':>8}")
    print(h1)
    print("-" * len(h1))
    for r in rows:
        print(f"{r[0]:>3} {r[1]:6} {r[2]:14} {r[3]:28} {r[4]:>4} {fmt(r[5], '.4f'):>8} "
              f"{tok(r[6]):>9} {tok(r[7]):>8} {fmt(r[8], '.1f'):>8} {fmt(r[9], '.1f'):>8} "
              f"{fmt(r[10], '.3f'):>8} {fmt(r[11], '.3f'):>8}")

    print()
    print("=" * 118)
    print("RELIABILITY AND QUALITY MEASURES  (proposal 7.2.2, 7.2.3)")
    print("=" * 118)
    h2 = (f"{'exp':>3} {'phase':6} {'model':14} {'topology':28} {'qual':>6} {'cover':>6} "
          f"{'@opt':>5} {'prec':>6} {'@opt':>5} {'schEff':>7} {'schDev':>7} {'infeas':>7} "
          f"{'viol':>5} {'compl':>6} {'declQ1':>7}")
    print(h2)
    print("-" * len(h2))
    for r in rows:
        d = decline.get((r[0], r[3]))
        print(f"{r[0]:>3} {r[1]:6} {r[2]:14} {r[3]:28} {fmt(r[12]):>6} "
              f"{fmt(r[13], '.3f'):>6} {fmt(r[14]):>5} {fmt(r[15], '.3f'):>6} "
              f"{fmt(r[16]):>5} {fmt(r[17], '.3f'):>7} {fmt(r[18], '.3f'):>7} "
              f"{fmt(r[19]):>7} {fmt(r[20]):>5} {fmt(r[21]):>6} {fmt(d):>7}")


def print_bins(conn: sqlite3.Connection, where: str = "", params: tuple = ()) -> None:
    """Print the complexity-bin breakdown: how each topology holds up as work grows."""
    rows = conn.execute(f"""
        SELECT experiment_no, run_phase, model, topology, scope,
               MAX(CASE WHEN measure='cost_usd' THEN n END) AS queries,
               MAX(CASE WHEN measure='completed' THEN n END) AS runs,
               MAX(CASE WHEN measure='cost_usd' THEN median END) AS cost,
               MAX(CASE WHEN measure='judge_mean_scope_adj' THEN median END) AS quality,
               MAX(CASE WHEN measure='capability_coverage' THEN mean END) AS coverage,
               MAX(CASE WHEN measure='scheduling_deviation' THEN mean END) AS deviation,
               MAX(CASE WHEN measure='has_violation' THEN rate END) AS violation_rate,
               MAX(CASE WHEN measure='completed' THEN rate END) AS completion_rate
        FROM agg_topology WHERE scope LIKE 'bin:%' {where}
        GROUP BY experiment_no, topology, scope
        ORDER BY experiment_no, topology, scope
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
    header = (f"{'exp':>3} {'phase':6} {'model':14} {'topology':28} {'bin':>10} "
              f"{'runs':>4} {'qrys':>4} {'cost':>7} {'qual':>6} {'cover':>6} "
              f"{'schdev':>7} {'viol':>5} {'compl':>6}")
    print(header)
    print("-" * len(header))

    # Bins print in workload order rather than alphabetically, so the progression from
    # least to most demanding reads down the column.
    bin_order = {"bin:low": 0, "bin:medium": 1, "bin:high": 2, "bin:very_high": 3}
    for r in sorted(rows, key=lambda x: (x[0], topology_sort_key(x[3]),
                                         bin_order.get(x[4], 99))):
        def fmt(v, spec=".2f"):
            return format(v, spec) if v is not None else "n/a"
        print(f"{r[0]:>3} {r[1]:6} {r[2]:14} {r[3]:28} {r[4].replace('bin:', ''):>10} "
              f"{r[6]:>4} {r[5]:>4} {fmt(r[7], '.4f'):>7} {fmt(r[8]):>6} "
              f"{fmt(r[9], '.3f'):>6} {fmt(r[10], '.3f'):>7} {fmt(r[11]):>5} "
              f"{fmt(r[12]):>6}")


def _warn_mixed_config(runs: list[dict]) -> None:
    """Report cells whose pooled runs were produced under different configurations.

    aggregate_queries() pools every run of an (experiment_no, topology, query_id) cell,
    which is the repetition aggregation the design calls for. It does not check that the
    pooled runs are comparable: a re-run after a prompt change carries a different
    config_hash and would be averaged with the runs it was meant to replace, silently.

    config_hash covers model and prompt versions, so a change to either shows up here.
    The validity rule stays a matter of discipline -- this only makes a breach visible
    at report time instead of never.
    """
    by_cell: dict[tuple, set] = defaultdict(set)
    for r in runs:
        ch = r.get("config_hash")
        if ch and not str(ch).startswith("unavailable"):
            by_cell[(r["experiment_no"], r["topology"], r["query_id"])].add(ch)

    mixed = {cell: hashes for cell, hashes in by_cell.items() if len(hashes) > 1}
    if not mixed:
        return
    print(f"  WARNING              : {len(mixed)} cell(s) pool runs with differing "
          f"config_hash (prompt or model version changed mid-experiment)")
    for (experiment_no, topology, query_id), hashes in sorted(mixed)[:5]:
        print(f"                         exp {experiment_no} / {topology} / {query_id}: "
              f"{len(hashes)} configs")
    if len(mixed) > 5:
        print(f"                         ... and {len(mixed) - 5} more")
    print("                         Pre- and post-change runs should not be pooled.")


def _check_experiment_consistency(db_path: str) -> int:
    """Report run rows whose run_phase and model disagree with their experiment_no.

    experiment_no is stored on the run row so a campaign can be selected in one clause,
    which means it duplicates what run_phase and model already say and can be made to
    contradict them by a hand-written UPDATE. Everything downstream groups by the number,
    so a contradicting row would be aggregated under a campaign it does not belong to and
    nothing else would notice. Returns the number of offending rows; 0 is the expected
    result.
    """
    from measurement.run_store_schema import experiment_mismatches

    bad = experiment_mismatches(db_path)
    if not bad:
        return 0
    print(f"  WARNING              : {len(bad)} run row(s) contradict their experiment "
          f"definition")
    for run_id, exp, r_phase, r_model, e_phase, e_model in bad[:5]:
        print(f"                         {run_id[:8]}: row says {r_phase}/{r_model}, "
              f"experiment {exp} is {e_phase}/{e_model}")
    if len(bad) > 5:
        print(f"                         ... and {len(bad) - 5} more")
    print("                         Figures for these experiments are not trustworthy "
          "until this is resolved.")
    return len(bad)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DB_PATH, help=f"run store path (default: {DB_PATH})")
    # Two ways to name a campaign, both accepted. -e is terse; the pair is readable and
    # allows partial filters (--run-phase alone spans every tier in that phase).
    parser.add_argument("-e", "--experiment", type=int, default=None,
                        help="aggregate one experiment only (see run_store_schema.py "
                             "--list-experiments)")
    parser.add_argument("--run-phase", default=None,
                        help="aggregate one phase only, e.g. pilot or main")
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

    filtered = any(v is not None for v in (args.experiment, args.run_phase, args.model))

    conn = sqlite3.connect(args.db)
    try:
        runs = load_runs(conn, model=args.model, run_phase=args.run_phase,
                         experiment_no=args.experiment)
        if not runs:
            bits = [f"{k} {v}" for k, v in (("experiment", args.experiment),
                                            ("phase", args.run_phase),
                                            ("model", args.model)) if v is not None]
            scope = f" for {', '.join(bits)}" if bits else ""
            print(f"No runs found{scope}. Nothing to aggregate.")
            return 1

        query_aggs = aggregate_queries(runs)
        topology_aggs = aggregate_topologies(query_aggs, runs)
        # A filtered run set must not rebuild the whole table -- see write_tables.
        write_tables(conn, query_aggs, topology_aggs, scoped=filtered)

        experiments = sorted({(r["experiment_no"], r["run_phase"], r["model"]) for r in runs})
        topologies = sorted({r["topology"] for r in runs})
        print(f"  runs aggregated      : {len(runs)}")
        print(f"  experiments          : "
              f"{', '.join(f'{e} ({p}/{m})' for e, p, m in experiments)}")
        print(f"  topologies           : {len(topologies)}")
        print(f"  agg_query rows       : {len(query_aggs)}")
        print(f"  agg_topology rows    : {len(topology_aggs)}")
        _warn_mixed_config(runs)
        _check_experiment_consistency(args.db)

        where, params = _agg_filter(args.experiment, args.run_phase, args.model)
        if args.show:
            print()
            print_summary(conn, where, params)
        if args.show_bins:
            print()
            print_bins(conn, where, params)
    except sqlite3.Error as exc:
        print(f"ERROR: the run store could not be read or written: {exc}")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
