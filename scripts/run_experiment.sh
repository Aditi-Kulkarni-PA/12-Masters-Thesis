#!/usr/bin/env bash
# run_experiment.sh — plan and execute a batch of topology x query x repetition runs.
#
# Thin wrapper, same pattern as execute_topology.sh: pins the venv to the repo, then
# hands off to run_experiment.py, which does the actual planning/orchestration and
# calls execute_topology.sh once per planned run.
#
# Usage:
#   ./run_experiment.sh                          # all topologies, all queries, N=3
#   ./run_experiment.sh -q all -r all -n 1        # this pilot: N=1, everything else default
#   ./run_experiment.sh -q 1,2,3 -r 1,2           # specific queries, specific repetitions
#   ./run_experiment.sh -t swarm,mesh -q 6        # specific topologies + one query
#   ./run_experiment.sh --dry-run                 # preview the plan, no API calls
#   ./run_experiment.sh --list                    # show built topologies and frozen queries
#   ./run_experiment.sh --no-resume               # re-run combos even if already done
#   ./run_experiment.sh -h                        # full option list (see run_experiment.py)
#
# Console output stays to one line per run; each run's full detail (banners, turn
# text, tool/cost tables, run-validity checks) is written to its own file under
# supply_chain_topology_app/log/batches/<batch_id>/ instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."   # repo root: pyproject.toml, uv.lock and .env live there

# Pin the environment to the repo, matching execute_topology.sh (see that script's
# own comment for why: a shell that derives UV_PROJECT_ENVIRONMENT from $PWD would
# otherwise build a second venv under scripts/, silently different from every run
# already in the store).
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"

# Whole-batch start/end/duration banner -- separate from execute_topology.sh's own
# per-run "started/finished" timestamps, which cover one run each. SECONDS is bash's
# built-in elapsed-time counter, reset just before the batch starts. The command runs
# with -e temporarily off so a failing/interrupted batch still prints its end time and
# duration instead of the script dying silently mid-batch; the original exit code is
# preserved and returned at the very end.
BATCH_START="$(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================================================="
echo " batch started : $BATCH_START"
echo "=================================================================="
SECONDS=0

set +e
uv run --env-file .env python supply_chain_topology_app/run_experiment.py "$@"
rc=$?
set -e

elapsed=$SECONDS
printf -v DURATION_FMT '%02d:%02d:%02d' $((elapsed/3600)) $((elapsed%3600/60)) $((elapsed%60))
echo "=================================================================="
echo " batch started  : $BATCH_START"
echo " batch finished : $(date '+%Y-%m-%d %H:%M:%S')"
echo " total duration : $DURATION_FMT"
echo "=================================================================="
exit $rc
