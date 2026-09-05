"""
Experiment harness (T34): plans a batch of topology x query x repetition runs and
executes each one by invoking scripts/execute_topology.sh, so every run still goes
through that script's existing measurement-mode guardrails (NO_CACHE, dev-path
fallback off) unchanged.

Run from the repo root, same convention as execute_topology.sh:

    python supply_chain_topology_app/run_experiment.py -t all -q all -r all -n 1
    python supply_chain_topology_app/run_experiment.py -q 1,2,3 -r 1,2
    python supply_chain_topology_app/run_experiment.py --dry-run
    python supply_chain_topology_app/run_experiment.py --list

Why a subprocess per run, not an import loop
---------------------------------------------
execute_topology.py resolves its topology at MODULE import time (the builder call
happens once, when the module loads, from SC_TOPOLOGY) -- it cannot be re-imported
for a second topology inside one Python process. Each planned run is therefore its
own `bash execute_topology.sh <topology> <run_n>` subprocess, matching how a human
would invoke it by hand.

Resume and batching
--------------------
Every run this harness launches is stamped with the same batch_id and a per-run
execution_order (see measurement/run_store_schema.py). Resume is on by default: a
(topology, query_id, run_n) combo that already has a success/partial run is skipped,
so stopping the harness partway through (Ctrl-C) and re-running the same command
later continues rather than duplicates work.

Console output vs. log files
-----------------------------
execute_topology.sh/py print a large amount of per-run detail (banners, turn-by-turn
text, tool/cost tables, run-validity checks) -- fine for a single manual run, unreadable
once a batch runs dozens of them back to back. Each run's full stdout/stderr is
therefore captured to its own file under log/batches/<batch_id>/, and this process's
own stdout stays limited to one progress line per run plus the pass/fail outcome and
that file's path. Tail a run in flight with `tail -f` on its log path (subprocesses run
with PYTHONUNBUFFERED=1 so the file fills as the run progresses, not only at the end).
"""
import argparse
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))
_REPO_ROOT = _APP_DIR.parent
_EXECUTE_SCRIPT = _REPO_ROOT / "scripts" / "execute_topology.sh"

from topologies.registry import REGISTRY
from measurement.run_store_schema import DB_PATH

_LOG_DIR = _APP_DIR / "log"


# ---------------------------------------------------------------------------
# Selection parsing -- "all" or a comma-separated list, brackets optional
# (accepts both "1,2,3" and "[1,2,3]").
# ---------------------------------------------------------------------------
def _parse_list_arg(raw: str) -> list[str]:
    return [tok.strip() for tok in raw.strip("[] ").split(",") if tok.strip()]


def _all_query_ids(db_path: str) -> list[str]:
    """Every query_id in query_metadata, sorted ascending by numeric suffix."""
    conn = sqlite3.connect(db_path)
    try:
        ids = [r[0] for r in conn.execute("SELECT query_id FROM query_metadata")]
    finally:
        conn.close()
    return sorted(ids, key=lambda q: int(re.sub(r"\D", "", q) or 0))


def resolve_topologies(raw: str) -> list[str]:
    """'all' -> every BUILT topology (topologies/registry.py is the single source of
    truth, same list execute_topology.sh enables); otherwise a validated subset."""
    built = [name for name, spec in REGISTRY.items() if spec.is_built]
    if raw.strip().lower() == "all":
        return built
    wanted = _parse_list_arg(raw)
    unknown = [t for t in wanted if t not in REGISTRY]
    if unknown:
        raise SystemExit(f"ABORT: unknown topology/ies {unknown}. Valid: {sorted(REGISTRY)}")
    not_built = [t for t in wanted if not REGISTRY[t].is_built]
    if not_built:
        raise SystemExit(f"ABORT: topology/ies not yet built: {not_built}")
    return wanted


def resolve_queries(raw: str, db_path: str) -> list[str]:
    """'all' -> every frozen query in query_metadata; '1,2,3' or 'Q1,Q2,Q3' -> that
    subset, validated so a typo aborts here rather than deep inside a paid run."""
    all_ids = _all_query_ids(db_path)
    if raw.strip().lower() == "all":
        return all_ids
    tokens = _parse_list_arg(raw)
    wanted = [t.upper() if t.upper().startswith("Q") else f"Q{t}" for t in tokens]
    unknown = [t for t in wanted if t not in all_ids]
    if unknown:
        raise SystemExit(f"ABORT: unknown query id(s) {unknown}. Known: {all_ids}")
    return wanted


def resolve_reps(raw: str, total_reps: int) -> list[int]:
    """'all' -> run_n 1..total_reps; '1,2' -> exactly those run_n values."""
    if raw.strip().lower() == "all":
        return list(range(1, total_reps + 1))
    return [int(t) for t in _parse_list_arg(raw)]


# ---------------------------------------------------------------------------
# Resume: (topology, query_id, run_n) triples that already have a usable run, so a
# re-invocation after a stop/crash does not create a duplicate run_n for the same
# combo (the store has no uniqueness constraint on the triple -- this check is what
# keeps it unique in practice).
# ---------------------------------------------------------------------------
def already_done(db_path: str) -> set[tuple[str, str, int]]:
    if not Path(db_path).exists():
        return set()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT topology, query_id, run_n FROM run WHERE run_status IN ('success','partial')"
        ).fetchall()
    finally:
        conn.close()
    return {(t, q, n) for t, q, n in rows}


def build_plan(topologies: list[str], queries: list[str], reps: list[int],
              resume: bool, shuffle: bool, db_path: str) -> list[dict]:
    """Cross-product of topologies x queries x reps, minus already-done combos when
    *resume* is set, in randomized execution order unless *shuffle* is False."""
    done = already_done(db_path) if resume else set()
    plan = [
        {"topology": t, "query_id": q, "run_n": n}
        for t in topologies for q in queries for n in reps
        if (t, q, n) not in done
    ]
    if shuffle:
        random.shuffle(plan)
    for i, item in enumerate(plan, start=1):
        item["execution_order"] = i
    return plan


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def _run_log_path(log_dir: Path, item: dict) -> Path:
    """Per-run log file name: zero-padded execution order (sorts correctly) plus
    topology/query/run_n, so the file is identifiable without opening it."""
    return log_dir / (
        f"{item['execution_order']:03d}_{item['topology']}_{item['query_id']}_n{item['run_n']}.log"
    )


def run_one(item: dict, batch_id: str, run_phase: str, log_dir: Path) -> tuple[bool, Path]:
    """Invoke execute_topology.sh for one planned run, capturing its full stdout/stderr
    to a per-run log file (see _run_log_path) rather than letting it flood this
    process's own console. run_n MUST be the script's second positional argument, not
    an env var -- the script itself computes and exports SC_RUN_N from that argument,
    overriding anything set in the parent environment (see scripts/execute_topology.sh)."""
    env = os.environ.copy()
    env.update({
        "SC_QUERY_ID": item["query_id"],
        "SC_BATCH_ID": batch_id,
        "SC_EXECUTION_ORDER": str(item["execution_order"]),
        "SC_RUN_PHASE": run_phase,
        # Without this, Python block-buffers stdout once it is no longer a tty, so the
        # log file would only fill in at the end instead of as the run progresses.
        "PYTHONUNBUFFERED": "1",
    })
    log_path = _run_log_path(log_dir, item)
    with open(log_path, "w", encoding="utf-8") as logf:
        result = subprocess.run(
            ["bash", str(_EXECUTE_SCRIPT), item["topology"], str(item["run_n"])],
            cwd=str(_REPO_ROOT), env=env, stdout=logf, stderr=subprocess.STDOUT,
        )
    return result.returncode == 0, log_path


def _write_batch_summary(batch_id: str, batch_log_dir: Path, plan: list[dict],
                          succeeded: list[dict], failed: list[dict]) -> Path:
    """One JSON file per batch, inside that batch's own log subfolder alongside its
    per-run logs, so a stopped/interrupted run can be inspected without querying the
    database directly."""
    def _entry(i: dict) -> dict:
        return {"topology": i["topology"], "query_id": i["query_id"], "run_n": i["run_n"],
                "log": _run_log_path(batch_log_dir, i).name}

    path = batch_log_dir / "summary.json"
    path.write_text(json.dumps({
        "batch_id": batch_id,
        "log_dir": str(batch_log_dir),
        "planned": len(plan),
        "succeeded": [_entry(i) for i in succeeded],
        "failed": [_entry(i) for i in failed],
        "remaining": len(plan) - len(succeeded) - len(failed),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-t", "--topology", default="all",
                    help="'all' or comma-list of topology names (default: all)")
    ap.add_argument("-q", "--query", default="all",
                    help="'all' or comma-list of query numbers/ids, e.g. 1,2,3 or Q1,Q2 (default: all)")
    ap.add_argument("-r", "--reps", default="all",
                    help="'all' (run_n 1..--total-reps) or comma-list of specific run_n values, e.g. 1,2 (default: all)")
    ap.add_argument("-n", "--total-reps", type=int, default=3,
                    help="what '-r all' expands to (default: 3; pass 1 for a single-pass pilot)")
    ap.add_argument("-b", "--batch", default=None,
                    help="batch label to record (default: auto-generated UTC timestamp)")
    ap.add_argument("--run-phase", default="pilot",
                    help="run_phase to record on every run (default: pilot)")
    ap.add_argument("--no-resume", action="store_true",
                    help="do not skip combos that already have a success/partial run")
    ap.add_argument("--no-shuffle", action="store_true",
                    help="execute in plan order instead of a randomized order")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit; no API calls")
    ap.add_argument("--list", action="store_true", help="list built topologies and frozen queries, exit")
    args = ap.parse_args()

    if args.list:
        print("Built topologies:")
        for name, spec in REGISTRY.items():
            if spec.is_built:
                print(f"  {name}")
        print("\nFrozen queries:")
        for qid in _all_query_ids(DB_PATH):
            print(f"  {qid}")
        return 0

    topologies = resolve_topologies(args.topology)
    queries = resolve_queries(args.query, DB_PATH)
    reps = resolve_reps(args.reps, args.total_reps)
    batch_id = args.batch or f"batch_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    resume = not args.no_resume

    plan = build_plan(topologies, queries, reps, resume=resume,
                      shuffle=not args.no_shuffle, db_path=DB_PATH)

    print(f"Batch {batch_id}: {len(plan)} run(s) planned "
          f"({len(topologies)} topolog(ies) x {len(queries)} quer(ies) x {len(reps)} rep(s), "
          f"resume={'on' if resume else 'off'})")
    if not plan:
        print("Nothing to do -- every requested combination already has a run. "
              "Pass --no-resume to force a re-run.")
        return 0

    if args.dry_run:
        print(f"\n{'order':>5}  {'topology':28} {'query':6} run_n")
        for item in plan:
            print(f"{item['execution_order']:>5}  {item['topology']:28} "
                  f"{item['query_id']:6} {item['run_n']}")
        print("\n(--dry-run: nothing executed)")
        return 0

    # One subfolder per batch holds every run's full detailed output plus the summary
    # JSON -- this process's own stdout stays limited to a progress line per run.
    batch_log_dir = _LOG_DIR / "batches" / batch_id
    batch_log_dir.mkdir(parents=True, exist_ok=True)
    print(f"Detailed per-run output -> {batch_log_dir}/")

    succeeded, failed = [], []
    try:
        for item in plan:
            print(f"[{item['execution_order']}/{len(plan)}] {item['topology']}  "
                  f"{item['query_id']}  run_n={item['run_n']}  ...", end=" ", flush=True)
            ok, log_path = run_one(item, batch_id, args.run_phase, batch_log_dir)
            (succeeded if ok else failed).append(item)
            print(f"{'OK' if ok else 'FAILED'}  ({log_path.name})", flush=True)
    except KeyboardInterrupt:
        print(f"\nInterrupted after {len(succeeded)} succeeded, {len(failed)} failed. "
              f"Re-run the same command (resume is on by default) to continue.")
        return 130
    finally:
        summary_path = _write_batch_summary(batch_id, batch_log_dir, plan, succeeded, failed)
        print(f"\nBatch {batch_id} finished: {len(succeeded)} succeeded, {len(failed)} failed. "
              f"Summary: {summary_path}")
        if failed:
            print("Failed (see their log files above for detail):")
            for item in failed:
                print(f"  {item['topology']}  {item['query_id']}  run_n={item['run_n']}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
