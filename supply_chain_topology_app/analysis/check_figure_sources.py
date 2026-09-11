"""
Figure source check (T15): every planned figure traced back to the columns that feed it.

Each figure in docs/thesis-topology-tradeoffs/reporting/analysis-report-design-spec.md
declares here which run-store fields it reads. This script resolves every declaration
against the live store and reports anything that does not exist.

The point is timing. Twenty-one figure tasks read from agg_query/agg_topology, and a
measure that was never computed is invisible until someone sits down to draw the figure
-- at which point the fix is a re-run, not an edit. Checked here it is an hour.

A reference is one of:
    run.<column>            a column on the run table
    tool_call.<column>      a column on the tool_call table
    quality_scores.<column> a column on quality_scores
    query_metadata.<column> a column on query_metadata
    measure:<name>          a measure name present in agg_query / agg_topology
    derived:<note>          computed at figure time from references already listed;
                            recorded so the derivation is stated rather than assumed

Usage:
    python supply_chain_topology_app/analysis/check_figure_sources.py
    python supply_chain_topology_app/analysis/check_figure_sources.py --db path/to.db
"""
import argparse
import sqlite3
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from measurement.run_store_schema import DB_PATH

# Figure -> what it reads. Keys match the F-numbers in analysis-report-design-spec.md;
# `task` is the tracker task that owns the figure, or None where the spec records no
# owner. Keeping the owner here means an unowned figure is visible in this output rather
# than only in the spec prose.
FIGURES: dict[str, dict] = {
    "F1": {"task": "T63", "what": "Pareto frontier, quality vs cost",
           "needs": ["measure:cost_usd", "measure:judge_mean_scope_adj"]},
    "F2": {"task": "T63", "what": "Pareto frontier, quality vs latency",
           "needs": ["measure:wall_time_s", "measure:judge_mean_scope_adj"]},
    "F3": {"task": "D2.3", "what": "Pareto frontier shift, simple vs multi_hop",
           "needs": ["measure:cost_usd", "measure:judge_mean_scope_adj",
                     "query_metadata.complexity_bin"]},
    "F4": {"task": "T65", "what": "Cost anatomy, tokens per capability",
           "needs": ["tool_call.tool_name", "tool_call.total_tokens",
                     "tool_call.prompt_tokens", "tool_call.completion_tokens"]},
    "F5": {"task": "D3.1", "what": "Orchestration tax, coordination vs specialist share",
           "needs": ["measure:orchestration_token_share", "measure:specialist_token_share",
                     "measure:orchestration_tax", "run.master_total_tokens"]},
    "F6": {"task": "D2.1", "what": "Quality vs capabilities-required",
           "needs": ["measure:judge_mean_scope_adj", "query_metadata.implied_tools_json",
                     "query_metadata.query_complexity_score"]},
    "F7": {"task": "D2.2", "what": "Cost vs capabilities-required, log y",
           "needs": ["measure:cost_usd", "query_metadata.implied_tools_json"]},
    "F8": {"task": "D4.1", "what": "Design-family frontier",
           "needs": ["measure:cost_usd", "measure:judge_mean_scope_adj",
                     "derived:topology -> design-family mapping, declared in the figure code"]},
    "F9": {"task": "D3.2", "what": "Cumulative cost across the query set",
           "needs": ["run.grand_total_cost_usd", "run.query_id", "run.topology"]},
    "F10": {"task": "D5.1", "what": "Critical-difference diagram (Friedman + Nemenyi)",
            "needs": ["measure:judge_mean_scope_adj",
                      "derived:per-(topology, query) matrix from agg_query"]},
    "F11": {"task": "D5.2", "what": "Forest plot of paired effect sizes",
            "needs": ["measure:judge_mean_scope_adj",
                      "derived:paired differences vs a reference condition"]},
    "F12": {"task": "D6.1", "what": "Scope-selection accuracy heatmap",
            "needs": ["query_metadata.implied_tools_json", "tool_call.tool_name",
                      "tool_call.unprompted", "measure:capability_coverage",
                      "measure:capability_precision"]},
    "F13": {"task": "D6.2", "what": "Execution timeline (Gantt)",
            "needs": ["tool_call.started_offset_s", "tool_call.ended_offset_s",
                      "tool_call.duration_s", "run.tool_base_offset_s"]},
    "F14": {"task": "D6.3", "what": "Robustness probe behaviour on the out-of-scope query",
            "needs": ["measure:declined", "run.behaviour_class",
                      "run.clarification_appropriate", "run.behaviour_rationale"]},
    "F15": {"task": "T68", "what": "Variance across repetitions",
            "needs": ["run.run_n", "measure:cost_usd", "measure:judge_mean_scope_adj",
                      "derived:spread across run_n within an experiment"]},
    "F16": {"task": "D2.1", "what": "Latency vs query complexity",
            "needs": ["measure:wall_time_s", "measure:critical_path_s",
                      "query_metadata.query_complexity_score"]},
    "F17": {"task": "T152", "what": "Composite topology score",
            "needs": ["measure:wall_time_s", "measure:cost_usd",
                      "measure:judge_mean_scope_adj", "measure:judge_mean",
                      "derived:normalised composite, weights declared in the figure code"]},
    "F18": {"task": "T155", "what": "3D interactive scatter, latency x cost x quality",
            "needs": ["measure:wall_time_s", "measure:cost_usd",
                      "measure:judge_mean_scope_adj"]},
}


def _live_names(db_path: str) -> tuple[dict[str, set], set]:
    """Columns per table, and the measure names present in the aggregate tables."""
    conn = sqlite3.connect(db_path)
    try:
        tables = {t: {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
                  for t in ("run", "tool_call", "quality_scores", "query_metadata")}
        measures: set = set()
        for t in ("agg_topology", "agg_query"):
            try:
                measures |= {r[0] for r in conn.execute(f"SELECT DISTINCT measure FROM {t}")}
            except sqlite3.OperationalError:
                pass          # aggregates not built yet
    finally:
        conn.close()
    return tables, measures


def resolve(ref: str, tables: dict[str, set], measures: set) -> tuple[bool, str]:
    """(ok, detail) for one declared reference."""
    if ref.startswith("derived:"):
        return True, "derived at figure time"
    if ref.startswith("measure:"):
        name = ref.split(":", 1)[1]
        return (name in measures), ("in agg_*" if name in measures else "NOT an aggregated measure")
    if "." in ref:
        table, col = ref.split(".", 1)
        if table not in tables:
            return False, f"unknown table {table}"
        return (col in tables[table]), ("present" if col in tables[table] else f"NOT a column of {table}")
    return False, "unrecognised reference form"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DB_PATH, help=f"run store path (default: {DB_PATH})")
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"ERROR: run store not found at {args.db}.")
        return 1

    tables, measures = _live_names(args.db)
    if not measures:
        print("WARNING: no aggregated measures found. Run generate_metrics_report.sh first,\n"
              "         or every measure: reference below will read as missing.\n")

    missing_total, unowned = 0, []
    print(f"{'fig':5} {'task':6} {'refs':>4}  status")
    print("-" * 72)
    for fig, spec in FIGURES.items():
        results = [(ref, *resolve(ref, tables, measures)) for ref in spec["needs"]]
        bad = [r for r in results if not r[1]]
        missing_total += len(bad)
        if spec["task"] is None:
            unowned.append(fig)
        flag = "OK" if not bad else f"{len(bad)} MISSING"
        print(f"{fig:5} {str(spec['task'] or '-'):6} {len(results):>4}  {flag}   {spec['what']}")
        for ref, ok, detail in results:
            if not ok:
                print(f"                     ! {ref} -- {detail}")

    print("-" * 72)
    print(f"figures: {len(FIGURES)}   unresolved references: {missing_total}")
    if unowned:
        print(f"figures with no owning task: {', '.join(unowned)}")
    if missing_total:
        print("\nEvery unresolved reference is a figure that cannot be drawn from the store\n"
              "as it stands. Fix the declaration or add the measure before figure work.")
    return 1 if missing_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
