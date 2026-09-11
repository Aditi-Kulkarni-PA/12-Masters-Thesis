#!/usr/bin/env bash
# execute_topology.sh — execute one topology condition and persist a run record.
#
# Usage:
#   ./execute_topology.sh                           # defaults: planner_executor, Q11, run_n 1
#   ./execute_topology.sh -t swarm                  # named topology
#   ./execute_topology.sh -t sequential -q Q4       # topology + query from the frozen set
#   ./execute_topology.sh -t planner_executor -n 3  # topology + run_n
#   ./execute_topology.sh --list                    # show topologies and which are built
#   ./execute_topology.sh --cache -t planner_executor  # freshness reuse (NOT for measurement)
#
# Options:
#   -t, --topology   one topology name. Default planner_executor
#   -q, --query      one query id from the frozen set, e.g. Q4 or 4. Default Q11, the
#                    full workflow (renumbered 5-Sep-26, was Q10)
#   -n, --run-n      repetition number recorded on the run. Default 1
#   --cache          allow cache/freshness reuse. Exploratory use only
#   --list           list topologies and exit
#
# The flags match execute_experiment.sh, which takes the same -t and -q in their
# comma-list form. This script runs exactly one topology against exactly one query, so
# each takes a single value here.
#
# A run launched here is AD-HOC: it belongs to no experiment, so experiment_no is left
# NULL and the row stays unlocked. Aggregation selects by experiment, so an ad-hoc run
# never reaches a reported figure. Measurement batches go through execute_experiment.sh,
# which resolves an experiment_no and stamps it on every run it launches.
#
# Defaults live here so a measurement run cannot silently differ from another by
# whichever env vars happened to be exported in the shell.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."   # repo root: pyproject.toml, uv.lock and .env live there

# Pin the environment to the repo, not to wherever you happened to invoke from.
# Without this, a shell that derives UV_PROJECT_ENVIRONMENT from $PWD sent uv off to
# build a fresh 270-package venv under scripts/ — different library versions from the
# runs already in the store, which quietly breaks cross-run comparability.
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
TOPOLOGY="planner_executor"
RUN_N="1"
QUERY_ID=""        # empty means execute_topology.py's own default, Q11

# Measurement mode: disables the response cache AND the freshness-skip marker, so
# every run executes all work independently. Required for latency/cost figures to
# mean anything (see delivery_chat_app.py:81). Risk Log R16 — must stay 1 for any
# run whose numbers are used in the thesis.
NO_CACHE="1"

# Dev-path fallback must be OFF for measurement runs — Risk Log R10.
export SC_DEV_PATH_FALLBACK="0"

# ---------------------------------------------------------------------------
# Topologies — keep in sync with topologies/registry.py
# Uncomment a line as its orchestration code lands. Prompts already exist for all
# seven under config/prompts/coordinators/<name>/; only the code is missing, and
# the registry raises NotImplementedError rather than falling back silently.
# ---------------------------------------------------------------------------
TOPOLOGIES=(
  "planner_executor"      # CORE — built (T32)
  "monolith"              # CORE — built (T37): one LLM context, raw tools, domain prompts inlined
  "sequential"            # CORE — built (T38): planner fixes the order, executor runs one forced tool per turn
  "static_graph_dag"      # CORE — built (T39): leveled scheduler, levels run concurrently
  "dynamic_graph"        # OPTIONAL (T100 gate) — T87: MagenticBuilder ledger — built, not yet run
  "static_graph_routed"  # CORE (promoted 29-Aug-26) — T108: intent router gates a subset of the DAG graph — built
  "mesh"                 # OPTIONAL (T100 gate) — T40: concurrent peer-to-peer messaging over a
                          #   complete WorkflowBuilder graph, no coordinator — built, not yet run
  "swarm"                # CORE — T99: pure mode. Seed plans wave 1 only; every downstream
                          #   capability is requested agent-to-agent via request_specialist, no
                          #   dependency table anywhere — rebuilt 30-Aug-26, built.
  "swarm_constrained_adaptive"  # CORE — T99: split from swarm 30-Aug-26. Same seed/blackboard
                          #   shape, but code gates WHEN a capability runs against
                          #   measurement/dependencies.py's TRUE_DEPENDENCIES — built.
)

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; }

list_topologies() {
  echo "Topologies enabled in this script:"
  for t in "${TOPOLOGIES[@]}"; do echo "  $t"; done
  echo
  echo "Full registry (including not-yet-built):"
  uv run python -c "
from pathlib import Path; import sys
sys.path.insert(0, 'supply_chain_topology_app')
from topologies.registry import REGISTRY
for n, s in REGISTRY.items():
    print(f'  {n:18} {\"BUILT\" if s.is_built else \"not built\":10} {s.note}')
"
}

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
# Flags only. A bare positional argument is rejected rather than interpreted: the script
# used to take the topology and run_n positionally, so silently accepting one would let an
# old invocation run against the wrong defaults instead of saying what changed.
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          usage; exit 0 ;;
    --list)             list_topologies; exit 0 ;;
    --cache)            NO_CACHE="0"; shift ;;
    -t|--topology)      TOPOLOGY="$2"; shift 2 ;;
    -q|--query)         QUERY_ID="$2"; shift 2 ;;
    -n|--run-n)         RUN_N="$2";    shift 2 ;;
    -*)                 echo "Unknown option: $1" >&2; usage; exit 2 ;;
    *)
      echo "ERROR: unexpected argument '$1'. This script takes flags, not positional" >&2
      echo "       arguments. Use -t <topology> -q <query> -n <run_n>." >&2
      exit 2 ;;
  esac
done

# Accept a bare number for the query, matching execute_experiment.sh's -q.
if [[ -n "$QUERY_ID" && "$QUERY_ID" =~ ^[0-9]+$ ]]; then
  QUERY_ID="Q$QUERY_ID"
fi

if ! [[ "$RUN_N" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --run-n must be a positive integer, got '$RUN_N'." >&2
  exit 2
fi

# Reject a topology that is commented out above, before paying for any API call.
if ! printf '%s\n' "${TOPOLOGIES[@]}" | grep -qx "$TOPOLOGY"; then
  echo "ERROR: topology '$TOPOLOGY' is not enabled in this script." >&2
  echo "       Uncomment it in TOPOLOGIES once its orchestration code exists." >&2
  echo >&2
  list_topologies >&2
  exit 2
fi

if [[ "$NO_CACHE" != "1" ]]; then
  echo "WARNING: cache/freshness reuse is ENABLED (--cache)."
  echo "         Timing and token figures from this run are NOT comparable."
  echo
fi

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
echo "=================================================================="
echo " topology : $TOPOLOGY"
echo " query    : ${QUERY_ID:-<default>}"
echo " run_n    : $RUN_N"
echo " no-cache : $NO_CACHE   (1 = measurement mode)"
echo " started  : $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================================================="

# SC_EXPECT_NO_CACHE mirrors SC_NO_CACHE and appears ONLY here, never in .env.
# execute_topology.py aborts if the two disagree, which would mean something (uv --env-file,
# a stray export) overrode the caller's intent and the run's label would not match
# its actual cache state.
#
# SC_TOPOLOGY, SC_RUN_N and SC_QUERY_ID are all set from this script's own flags, so a
# variable of the same name left exported in the calling shell cannot change what runs.
SC_NO_CACHE="$NO_CACHE" \
SC_EXPECT_NO_CACHE="$NO_CACHE" \
SC_TOPOLOGY="$TOPOLOGY" \
SC_RUN_N="$RUN_N" \
SC_QUERY_ID="$QUERY_ID" \
uv run --env-file .env python supply_chain_topology_app/cli/execute_topology.py

echo
echo "finished : $(date '+%Y-%m-%d %H:%M:%S')"
