#!/usr/bin/env bash
# generate_metrics_report.sh — produce the full metrics report from the run store.
#
# Runs the whole reporting pipeline in one command:
#
#   1. backfill_scheduling.py   computes the execution-timing measures for every run
#                               (critical path, concurrency span, scheduling ratio and
#                               deviation, infeasible overlap, coordinator idle time)
#   2. analysis/aggregate.py    rebuilds agg_query and agg_topology, then prints the
#                               topology summary and the complexity-bin breakdown
#
# Step 1 runs first because step 2 reads the columns it writes. Skipping it would leave
# critical-path latency, achievable/actual concurrency and scheduling deviation absent
# for any run added since the last report.
#
# Usage:
#   ./generate_metrics_report.sh                          # FULL REPORT: every experiment, every view
#   ./generate_metrics_report.sh -e 3                      # one experiment only
#   ./generate_metrics_report.sh --run-phase main          # one phase, every tier in it
#   ./generate_metrics_report.sh --run-phase pilot --model gpt-5.4-mini
#   ./generate_metrics_report.sh --model gpt-5.4-mini      # one tier, every phase it appears in
#   ./generate_metrics_report.sh --no-bins                 # topology summary only
#   ./generate_metrics_report.sh --no-print                # complexity bins only
#   ./generate_metrics_report.sh --no-print --no-bins      # rebuild the tables, print nothing
#   ./generate_metrics_report.sh --skip-timing             # skip step 1 (see below)
#   ./generate_metrics_report.sh --db path/to/other.db     # report on a different store
#
# What this does NOT do: it makes no API calls and re-executes no run. Both steps derive
# everything from what execute_experiment.sh/execute_topology.sh already stored — the tool
# call offsets, token counts and judge scores — so running it as often as you like costs
# nothing and cannot alter a measurement.
#
# Both steps are idempotent. Step 1 recomputes and overwrites every timing value from the
# stored offsets; step 2 drops and rebuilds both aggregate tables. Running this twice in a
# row produces identical output.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."   # repo root: pyproject.toml, uv.lock and .env live there

# Pin the environment to the repo, matching every other script in this folder.
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"

# The full report is the default here, unlike aggregate.py itself where each view is
# opt-in: running this script by hand means wanting to see everything. Both the topology
# summary and the complexity-bin breakdown print unless explicitly suppressed.
#
# Campaign filters (-e / --run-phase / --model) are not parsed here. Anything this loop
# does not recognise falls through to aggregate.py, which owns their meaning -- parsing
# them twice is how the two would drift apart.
#
# --no-print      omit the topology summary
# --no-bins       omit the complexity-bin breakdown
# --skip-timing   omit step 1. Only useful when the timing measures are known current and
#                 the store is large enough that recomputing them is not worth the wait.
ARGS=("$@")
SHOW_PRINT=1
SHOW_BINS=1
RUN_TIMING=1
DB_ARGS=()
AGG_ARGS=()
for ((i = 0; i < ${#ARGS[@]}; i++)); do
  arg="${ARGS[$i]}"
  case "$arg" in
    --no-print)    SHOW_PRINT=0 ;;
    --no-bins)     SHOW_BINS=0 ;;
    --skip-timing) RUN_TIMING=0 ;;
    # --print and --bins are accepted but redundant, since both are already on.
    --print|--bins) ;;
    # --db applies to both steps, so it is captured separately and passed to each.
    --db)
      DB_ARGS=(--db "${ARGS[$((i + 1))]}")
      ((i++))
      ;;
    --db=*)
      DB_ARGS=(--db "${arg#--db=}")
      ;;
    *) AGG_ARGS+=("$arg") ;;
  esac
done

[[ "$SHOW_PRINT" == "1" ]] && AGG_ARGS+=("--print")
[[ "$SHOW_BINS" == "1" ]] && AGG_ARGS+=("--bins")

# --- step 1: execution-timing measures -------------------------------------------------
if [[ "$RUN_TIMING" == "1" ]]; then
  echo "=================================================================="
  echo " step 1/2  execution-timing measures"
  echo "=================================================================="
  uv run --env-file .env python supply_chain_topology_app/cli/backfill_scheduling.py \
    ${DB_ARGS[@]+"${DB_ARGS[@]}"}
  echo
fi

# --- step 2: aggregation and report ----------------------------------------------------
echo "=================================================================="
echo " step 2/2  aggregation and report"
echo "=================================================================="
uv run --env-file .env python supply_chain_topology_app/analysis/aggregate.py \
  ${DB_ARGS[@]+"${DB_ARGS[@]}"} ${AGG_ARGS[@]+"${AGG_ARGS[@]}"}
