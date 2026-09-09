"""
F17 sample computation -- composite topology score (latency + cost + quality).

Built 30-Aug-26 (Aditi + Claude, docs/thesis-topology-tradeoffs/reporting/analysis-report-design-spec.md F17). Reads real
data directly from run_store.db -- this is not synthetic, but it IS a dev-mode snapshot
(1-3 of 10/11 queries per topology, opportunistic coverage), not a pilot result. Rerun
this unchanged against the DB once pilot coverage exists; nothing here needs to change
for that, the query already excludes Q_PLACEHOLDER_1 and Q9 and joins whatever rows are
on disk.

Per-query normalization, not global (see design spec F17 for why): a topology is only
compared against OTHER topologies that ran the SAME query, which avoids conflating
"this topology is efficient" with "this topology happened to only run cheap, easy
queries". Latency and cost are inverted (lower raw value -> higher normalized score);
quality is not (higher raw value -> higher normalized score). Equal 1/3 weighting is a
stated design choice, not derived from anything -- flag it as such wherever this score
is cited.

Quality source: judge_mean_scope_adj is preferred, falling back to judge_mean where the
scope-adjusted score is NULL (most historical runs -- see design spec §9 open decision
#7). Each point's quality_adj flag records which source was actually used.
"""
import sqlite3
import statistics

DB_PATH = "supply_chain_topology_app/data/run_store.db"


def normalize(value, lo, hi, invert):
    """Min-max scale value into [0,1]; invert=True means lower raw value scores higher.
    A single-topology query (lo == hi) has nothing to compare against, so it scores a
    neutral 1.0 rather than dividing by zero."""
    if hi == lo:
        return 1.0
    scaled = (value - lo) / (hi - lo)
    return 1 - scaled if invert else scaled


def load_points(db_path: str = DB_PATH):
    """One row per (topology, query_id) with real success runs averaged together."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """SELECT r.topology, r.query_id, r.wall_time_s, r.grand_total_cost_usd,
                  qs.judge_mean, qs.judge_mean_scope_adj
           FROM run r JOIN quality_scores qs ON qs.run_id = r.run_id
           WHERE r.query_id NOT IN ('Q_PLACEHOLDER_1', 'Q9')
             AND r.run_status = 'success'"""
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()

    # Group repeated runs of the same (topology, query_id) so each becomes one point.
    grouped: dict[str, dict[str, list]] = {}
    for r in rows:
        quality = r["judge_mean_scope_adj"] if r["judge_mean_scope_adj"] is not None else r["judge_mean"]
        is_adjusted = r["judge_mean_scope_adj"] is not None
        grouped.setdefault(r["query_id"], {}).setdefault(r["topology"], []).append(
            (r["wall_time_s"], r["grand_total_cost_usd"], quality, is_adjusted)
        )
    return grouped


def per_query_composite(grouped: dict) -> dict:
    """One composite score per (topology, query_id), normalized within that query only."""
    points = {}
    for query_id, by_topology in grouped.items():
        means = {
            topology: (
                statistics.mean(v[0] for v in values),
                statistics.mean(v[1] for v in values),
                statistics.mean(v[2] for v in values),
                len(values),
                any(v[3] for v in values),
            )
            for topology, values in by_topology.items()
        }
        lats = [v[0] for v in means.values()]
        costs = [v[1] for v in means.values()]
        quals = [v[2] for v in means.values()]
        lo_lat, hi_lat = min(lats), max(lats)
        lo_cost, hi_cost = min(costs), max(costs)
        lo_qual, hi_qual = min(quals), max(quals)

        for topology, (lat, cost, qual, n_rep, adjusted) in means.items():
            n_lat = normalize(lat, lo_lat, hi_lat, invert=True)
            n_cost = normalize(cost, lo_cost, hi_cost, invert=True)
            n_qual = normalize(qual, lo_qual, hi_qual, invert=False)
            composite = (n_lat + n_cost + n_qual) / 3 * 100
            points[(topology, query_id)] = {
                "lat": lat, "cost": cost, "qual": qual, "n_rep": n_rep,
                "quality_adj": adjusted, "score": round(composite, 1),
                "n_topologies_on_query": len(means),
            }
    return points


def topology_rollup(points: dict) -> list[dict]:
    """One row per topology: mean of its available per-query composites."""
    by_topology: dict[str, list] = {}
    for (topology, query_id), point in points.items():
        by_topology.setdefault(topology, []).append((query_id, point))

    rollup = []
    for topology, items in by_topology.items():
        rollup.append({
            "topology": topology,
            "score": round(statistics.mean(p["score"] for _, p in items), 1),
            "n_queries": len(items),
            "queries": sorted(q for q, _ in items),
        })
    rollup.sort(key=lambda r: -r["score"])
    return rollup


if __name__ == "__main__":
    grouped = load_points()
    points = per_query_composite(grouped)
    rollup = topology_rollup(points)
    for row in rollup:
        print(f"{row['topology']:22} score={row['score']:5.1f}  "
              f"queries={row['n_queries']}/10  {row['queries']}")
