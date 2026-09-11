#!/usr/bin/env bash
# execute_experiment.sh — plan and execute a batch of topology x query x repetition runs.
#
# Thin wrapper, same pattern as execute_topology.sh: pins the venv to the repo, then
# hands off to execute_experiment.py, which does the actual planning/orchestration and
# calls execute_topology.sh once per planned run.
#
# Every batch states TWO things: which experiment it belongs to (a run_phase at a model)
# and which repetition it is. Neither has a default, and --model is checked against
# OPENAI_MODEL in .env before anything is spent.
#
# Usage:
#   ./execute_experiment.sh -e 4 --run-n 2                    # experiment 4, repetition 2
#   ./execute_experiment.sh --run-phase main --model gpt-5.4 --run-n 2
#   ./execute_experiment.sh --run-phase main --model gpt-5.4 --run-n 1 --new-experiment
#   ./execute_experiment.sh -e 4 --run-n 2 -q 1,2,3           # three queries only
#   ./execute_experiment.sh -e 4 --run-n 2 -t swarm,mesh -q 6 # specific topologies + one query
#   ./execute_experiment.sh -e 4 --run-n 2 --dry-run          # preview the plan, no API calls
#   ./execute_experiment.sh --list                            # built topologies and frozen queries
#   ./execute_experiment.sh -e 4 --run-n 2 --no-resume        # re-run combos even if already done
#   ./execute_experiment.sh -h                                # full option list (see execute_experiment.py)
#
# List the experiments that exist:
#   uv run python supply_chain_topology_app/measurement/run_store_schema.py --list-experiments
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
# Pre-flight: every condition must use the same generation parameters, or the batch is not
# a topology comparison. temperature is written as a separate literal at 23 sites across the
# nine topology files and core/agents.py, and config_hash does not cover generation settings,
# so drift would reach a batch and leave no trace in the run store. Cheap, reads source only,
# and refuses to spend on a batch that is already invalid.
uv run python supply_chain_topology_app/cli/check_model_parity.py
parity_rc=$?
if [ $parity_rc -ne 0 ]; then
  echo "=================================================================="
  echo " ABORTED: model generation parameters differ across conditions."
  echo " Fix the mismatch above before running a measurement batch."
  echo "=================================================================="
  exit $parity_rc
fi

uv run --env-file .env python supply_chain_topology_app/cli/execute_experiment.py "$@"
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
