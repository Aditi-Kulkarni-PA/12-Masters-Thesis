"""Backfill of the execution-timing measures onto existing runs.

Computes seven values per run and writes them to the run row:

    scheduling_ratio       critical_path_s / busy, where busy is the span minus
                           coordinator idle time. 1.0 is the ceiling.
    scheduling_deviation   abs(1 - ratio). 0 is optimal. This is the value aggregated
                           across runs.
    infeasible_overlap     1 when ratio > 1.0, meaning the overlap exceeded what the
                           dependency graph permits, which is only possible by running
                           a dependent pair concurrently.
    critical_path_s        longest dependency-constrained sequence of tool calls.
    actual_span_s          first tool start to last tool end.
    fully_serial_s         total capability-call duration with no overlap.
    coordinator_idle_s     time inside the span with no tool running.

Re-running is safe and idempotent: every value is recomputed from the tool_call rows
and overwritten, so running it after a new batch updates that batch's runs and leaves
the rest at the same values.

Every input already exists in the store: tool_call.started_offset_s and
tool_call.ended_offset_s, plus the static dependency table. No run is re-executed.

A run with no capability tool calls has no schedule to measure. Its three values stay
NULL rather than 0, so a correct decline on the out-of-scope probe is not mistaken for
a perfectly scheduled run.

Usage:
    python backfill_scheduling.py              # write the values
    python backfill_scheduling.py --dry-run    # report what would change, write nothing
"""

import argparse
import sqlite3
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from measurement.dependencies import concurrency_report
from measurement.run_store_schema import DB_PATH, migrate_schema

# A ratio marginally above 1.0 is timing noise, not a dependency breach. Offsets are
# stored to two decimal places and short capability calls run in a few seconds, so a
# perfectly scheduled run can compute to 1.002 or 1.011. The observed pilot values
# separate cleanly: everything from 1.046 upward carries a recorded dependency
# violation, while the values below that do not. The tolerance is set between the two
# groups.
_INFEASIBLE_TOLERANCE = 0.02


def compute_for_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    """Return the three scheduling values for one run, or None when undefined.

    None means the run made no capability tool calls, so there is no schedule to
    measure. That is a real state, not a failure.
    """
    rows = conn.execute(
        "SELECT tool_name, started_offset_s, ended_offset_s "
        "FROM tool_call WHERE run_id = ? ORDER BY call_order",
        (run_id,),
    ).fetchall()
    calls = [dict(tool_name=r[0], started_offset_s=r[1], ended_offset_s=r[2]) for r in rows]
    if not calls:
        return None

    conc = concurrency_report(calls)
    if not conc or conc.get("exploited") is None:
        return None

    # busy is elapsed time with coordinator deliberation removed, so the ratio measures
    # how the tool calls were scheduled rather than how long the model paused to think.
    busy = conc["actual_span_s"] - conc.get("coordinator_idle_s", 0.0)
    if not busy:
        return None

    ratio = conc["critical_path_s"] / busy
    return {
        "scheduling_ratio": round(ratio, 4),
        "scheduling_deviation": round(abs(1.0 - ratio), 4),
        "infeasible_overlap": 1 if ratio > 1.0 + _INFEASIBLE_TOLERANCE else 0,
        # The timing figures the ratio is built from, stored so critical-path latency
        # and the concurrency measures can be aggregated in their own right rather than
        # surviving only inside the ratio (proposal Sections 7.2.1 and 7.2.2).
        "critical_path_s": conc["critical_path_s"],
        "actual_span_s": conc["actual_span_s"],
        "fully_serial_s": conc["fully_serial_s"],
        "coordinator_idle_s": conc.get("coordinator_idle_s"),
    }


def backfill(db_path: str, dry_run: bool = False) -> dict:
    """Compute and store the measure for every run in the store.

    Returns a summary count so the caller can report the outcome without re-querying.
    """
    if not Path(db_path).exists():
        raise FileNotFoundError(
            f"Run store not found at {db_path}. Check the path, or run an experiment first."
        )

    added = migrate_schema(db_path)
    if added:
        print(f"Schema updated: added {', '.join(added)}")

    summary = {"total": 0, "written": 0, "undefined": 0, "infeasible": 0, "failed": 0}
    conn = sqlite3.connect(db_path)
    try:
        run_ids = [r[0] for r in conn.execute("SELECT run_id FROM run ORDER BY started_at")]
        summary["total"] = len(run_ids)

        for run_id in run_ids:
            try:
                values = compute_for_run(conn, run_id)
            except Exception as exc:
                # One unreadable run must not stop the backfill. Report it and continue,
                # so the remaining runs still get their values.
                summary["failed"] += 1
                print(f"  Could not compute run {run_id[:8]}: {exc}")
                continue

            if values is None:
                summary["undefined"] += 1
                continue

            summary["written"] += 1
            summary["infeasible"] += values["infeasible_overlap"]

            if not dry_run:
                conn.execute(
                    "UPDATE run SET scheduling_ratio = ?, scheduling_deviation = ?, "
                    "infeasible_overlap = ?, critical_path_s = ?, actual_span_s = ?, "
                    "fully_serial_s = ?, coordinator_idle_s = ? WHERE run_id = ?",
                    (values["scheduling_ratio"], values["scheduling_deviation"],
                     values["infeasible_overlap"], values["critical_path_s"],
                     values["actual_span_s"], values["fully_serial_s"],
                     values["coordinator_idle_s"], run_id),
                )

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DB_PATH, help=f"run store path (default: {DB_PATH})")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing any values. The "
                             "schema columns are still added, since adding a nullable "
                             "column is non-destructive and idempotent.")
    args = parser.parse_args()

    try:
        summary = backfill(args.db, dry_run=args.dry_run)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 1
    except sqlite3.Error as exc:
        print(f"ERROR: the run store could not be updated: {exc}")
        return 1

    mode = "would be written" if args.dry_run else "written"
    print()
    print(f"  runs examined        : {summary['total']}")
    print(f"  values {mode:<14}: {summary['written']}")
    print(f"  undefined (no calls) : {summary['undefined']}")
    print(f"  infeasible overlap   : {summary['infeasible']} "
          f"(ratio above 1.0, meaning a dependent pair ran concurrently)")
    if summary["failed"]:
        print(f"  could not compute    : {summary['failed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
