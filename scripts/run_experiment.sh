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

uv run --env-file .env python supply_chain_topology_app/run_experiment.py "$@"
