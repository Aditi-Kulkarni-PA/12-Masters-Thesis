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
import signal
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
# Same MODEL every execute_topology.sh subprocess will actually run under (core.clients
# loads .env itself) -- resume must dedup against THIS value, not re-parse .env here and
# risk drifting from what the subprocess sees.
from core.clients import MODEL

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
# Resume: (topology, query_id, run_n, model) quadruples that already have a usable run,
# so a re-invocation after a stop/crash does not create a duplicate run_n for the same
# combo (the store has no uniqueness constraint on the triple -- this check is what
# keeps it unique in practice).
#
# model is part of the key -- not just the triple -- because a model switch between
# batches (e.g. gpt-5.4 -> gpt-5.4-nano) must re-run every combo, not silently reuse an
# old model's row. Found 6-Sep-26 (R-tiktoken-adjacent): the pilot's first nano dry-run
# planned 74 of 99 slots because 25 combos already had a success/partial row from
# gpt-5.4 runs collected 29/30-Aug-26 -- the un-keyed version would have produced a
# batch silently mixing two models under one batch_id.
# ---------------------------------------------------------------------------
def already_done(db_path: str) -> set[tuple[str, str, int, str]]:
    if not Path(db_path).exists():
        return set()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT topology, query_id, run_n, model FROM run WHERE run_status IN ('success','partial')"
        ).fetchall()
    finally:
        conn.close()
    return {(t, q, n, m) for t, q, n, m in rows}


def build_plan(topologies: list[str], queries: list[str], reps: list[int],
              resume: bool, shuffle: bool, db_path: str, model: str) -> list[dict]:
    """Cross-product of topologies x queries x reps, minus already-done combos on THIS
    model when *resume* is set, in randomized execution order unless *shuffle* is False."""
    done = already_done(db_path) if resume else set()
    plan = [
        {"topology": t, "query_id": q, "run_n": n}
        for t in topologies for q in queries for n in reps
        if (t, q, n, model) not in done
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


# Hard ceiling on a single run's wall time. Observed real query_complexity medians top
# out at 181.8s (Q11, five capabilities); this leaves roughly 2.6x headroom above the
# heaviest legitimate query while still bounding how long an unattended batch can be
# blocked by any one hung call -- confirmed necessary 6-Sep-26, when recommendation_tool
# froze mid-batch (near-zero CPU, `sleeping` state, no progress) for 15+ minutes with no
# exception ever surfacing, well past every comparable tool call's 30-50s. Overridable
# with --run-timeout for a batch expected to run heavier queries than any seen so far.
DEFAULT_RUN_TIMEOUT_S = 480


def run_one(item: dict, batch_id: str, run_phase: str, log_dir: Path,
           timeout_s: int = DEFAULT_RUN_TIMEOUT_S) -> tuple[bool, Path, bool]:
    """Invoke execute_topology.sh for one planned run, capturing its full stdout/stderr
    to a per-run log file (see _run_log_path) rather than letting it flood this
    process's own console. run_n MUST be the script's second positional argument, not
    an env var -- the script itself computes and exports SC_RUN_N from that argument,
    overriding anything set in the parent environment (see scripts/execute_topology.sh).

    A run that exceeds timeout_s is killed rather than left to block the batch
    indefinitely -- start_new_session=True puts the whole bash -> uv -> python3 chain in
    its own process group, so a timeout kills every descendant, not just the immediate
    bash child a plain subprocess.run(timeout=...) would reach (uv and the real worker
    underneath would otherwise be orphaned and keep running, still burning API cost).

    Returns (ok, log_path, timed_out) -- timed_out is surfaced separately from ok so a
    caller can tell "killed for exceeding timeout_s" apart from an ordinary non-zero
    exit, which matters here because the two point at different follow-up actions
    (raise the timeout vs. investigate the actual failure)."""
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
        proc = subprocess.Popen(
            ["bash", str(_EXECUTE_SCRIPT), item["topology"], str(item["run_n"])],
            cwd=str(_REPO_ROOT), env=env, stdout=logf, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            returncode = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            logf.write(f"\n\n[run_experiment.py] TIMEOUT after {timeout_s}s -- "
                      f"killing process group {proc.pid} (bash + uv + python3)\n")
            logf.flush()
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # already gone between the timeout firing and the kill
            proc.wait()  # reap so it does not linger as a zombie
            return False, log_path, True
    return returncode == 0, log_path, False


def _record_non_completion(item: dict, batch_id: str, run_phase: str,
                           log_path: Path, timed_out: bool) -> None:
    """Store a row for a run that did not complete, so the attempt stays countable.

    Timeouts and ordinary failures are distinguished in failure_category, because the
    two point at different follow-up actions: raise the timeout, or investigate the
    error in the run's own log.

    A failure to write this row must not stop the batch. The run has already ended, and
    losing one bookkeeping row is a smaller problem than abandoning the remaining runs,
    so the error is reported and the batch continues.
    """
    from measurement.run_store_writer import write_failed_run

    category = "no_completion:timeout" if timed_out else "no_completion:error"
    try:
        write_failed_run(
            query_id=item["query_id"], topology=item["topology"], run_n=item["run_n"],
            model=MODEL, failure_category=category, log_path=str(log_path),
            run_phase=run_phase, batch_id=batch_id,
            execution_order=item.get("execution_order"),
        )
    except Exception as exc:
        print(f"    (could not record the non-completion in the run store: {exc})",
              flush=True)


def _write_batch_summary(batch_id: str, batch_log_dir: Path, plan: list[dict],
                          succeeded: list[dict], failed: list[dict],
                          timed_out: list[dict]) -> Path:
    """One JSON file per batch, inside that batch's own log subfolder alongside its
    per-run logs, so a stopped/interrupted run can be inspected without querying the
    database directly.

    timed_out is reported as its own list rather than folded into failed -- a run killed
    for exceeding --run-timeout needs a different follow-up (raise the ceiling, or
    investigate why that specific combo hangs) than a run that exited with a genuine
    error, and collapsing the two would hide which one happened at a glance."""
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
        "timed_out": [_entry(i) for i in timed_out],
        "remaining": len(plan) - len(succeeded) - len(failed) - len(timed_out),
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
    ap.add_argument("--run-timeout", type=int, default=DEFAULT_RUN_TIMEOUT_S,
                    help=f"kill and fail a single run after this many seconds "
                         f"(default: {DEFAULT_RUN_TIMEOUT_S}, ~2.6x the heaviest "
                         f"observed query median)")
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
                      shuffle=not args.no_shuffle, db_path=DB_PATH, model=MODEL)

    print(f"Batch {batch_id}: {len(plan)} run(s) planned "
          f"({len(topologies)} topolog(ies) x {len(queries)} quer(ies) x {len(reps)} rep(s), "
          f"resume={'on' if resume else 'off'}, model={MODEL})")
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

    succeeded, failed, timed_out = [], [], []
    try:
        for item in plan:
            print(f"[{item['execution_order']}/{len(plan)}] {item['topology']}  "
                  f"{item['query_id']}  run_n={item['run_n']}  ...", end=" ", flush=True)
            ok, log_path, hit_timeout = run_one(item, batch_id, args.run_phase, batch_log_dir,
                                                timeout_s=args.run_timeout)
            if hit_timeout:
                timed_out.append(item)
            else:
                (succeeded if ok else failed).append(item)
            status = "TIMEOUT" if hit_timeout else ("OK" if ok else "FAILED")
            print(f"{status}  ({log_path.name})", flush=True)

            # A run that did not complete never reached write_run(), so without this it
            # would leave no row and every rate computed from the store would use a
            # denominator that quietly excluded it. Recording it keeps the attempt
            # visible and lets completion rate see the failure it exists to measure.
            if not ok:
                _record_non_completion(item, batch_id, args.run_phase, log_path,
                                       timed_out=hit_timeout)
    except KeyboardInterrupt:
        print(f"\nInterrupted after {len(succeeded)} succeeded, {len(failed)} failed, "
              f"{len(timed_out)} timed out. Re-run the same command (resume is on by "
              f"default) to continue.")
        return 130
    finally:
        summary_path = _write_batch_summary(batch_id, batch_log_dir, plan, succeeded,
                                            failed, timed_out)
        print(f"\nBatch {batch_id} finished: {len(succeeded)} succeeded, {len(failed)} failed, "
              f"{len(timed_out)} timed out. Summary: {summary_path}")
        if failed:
            print("Failed (see their log files above for detail):")
            for item in failed:
                print(f"  {item['topology']}  {item['query_id']}  run_n={item['run_n']}")
        if timed_out:
            print(f"Timed out (exceeded --run-timeout={args.run_timeout}s -- re-run these "
                  f"individually, or raise --run-timeout if this is a legitimately heavy "
                  f"combo, not a hang):")
            for item in timed_out:
                print(f"  {item['topology']}  {item['query_id']}  run_n={item['run_n']}")

    return 1 if (failed or timed_out) else 0


if __name__ == "__main__":
    sys.exit(main())
