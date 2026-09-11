#!/usr/bin/env bash
# delete_topology_run.sh — remove runs from the run store, sparing locked ones.
#
# Usage:
#   ./scripts/delete_topology_run.sh                    # --unlocked (default)
#   ./scripts/delete_topology_run.sh --unlocked         # every run with lock_rows = 0
#   ./scripts/delete_topology_run.sh --all              # EVERYTHING, locked included
#   ./scripts/delete_topology_run.sh --dry-run          # show what would go, delete nothing
#   ./scripts/delete_topology_run.sh --orphan-logs      # also sweep logs no run claims
#   ./scripts/delete_topology_run.sh --unlocked <run_id> [run_id ...]
#   ./scripts/delete_topology_run.sh -e 4               # one experiment, unlocked rows only
#   ./scripts/delete_topology_run.sh --run-phase main --model gpt-5.4 --dry-run
#
# The default is --unlocked and not --all on purpose: the expensive mistake here is
# irreversible, and a locked run is one whose numbers the thesis cites. --all therefore
# demands a typed confirmation naming the count it is about to destroy.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."   # repo root: pyproject.toml, uv.lock and .env live there

# Pin the environment to the repo, not to wherever you happened to invoke from.
# Without this, a shell that derives UV_PROJECT_ENVIRONMENT from $PWD sent uv off to
# build a fresh 270-package venv under scripts/ — different library versions from the
# runs already in the store, which quietly breaks cross-run comparability.
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"

APP="supply_chain_topology_app"
SCOPE="unlocked"
DRY=0
ORPHANS=0
IDS=()
# Campaign filters, passed through to delete_runs(). Empty means "no filter" and is
# rendered as Python None below.
EXPERIMENT=""
RUN_PHASE=""
MODEL=""

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)   usage; exit 0 ;;
    --all)       SCOPE="all";      shift ;;
    --unlocked)  SCOPE="unlocked"; shift ;;
    --dry-run)   DRY=1;            shift ;;
    --orphan-logs) ORPHANS=1;      shift ;;
    -e|--experiment) EXPERIMENT="$2"; shift 2 ;;
    --run-phase) RUN_PHASE="$2";   shift 2 ;;
    --model)     MODEL="$2";       shift 2 ;;
    -*)          echo "Unknown option: $1" >&2; usage; exit 2 ;;
    *)           IDS+=("$1");      shift ;;
  esac
done

# Rendered as Python literals for the two heredocs below: an int, a quoted string, or
# None. Quoting here rather than in the Python keeps both call sites identical.
PY_EXP="${EXPERIMENT:-None}"
PY_PHASE=$([[ -n "$RUN_PHASE" ]] && echo "'$RUN_PHASE'" || echo None)
PY_MODEL=$([[ -n "$MODEL" ]] && echo "'$MODEL'" || echo None)
FILTERS="experiment_no=$PY_EXP, run_phase=$PY_PHASE, model=$PY_MODEL"

PY_IDS="None"
if [[ ${#IDS[@]} -gt 0 ]]; then
  PY_IDS="["
  for i in "${IDS[@]}"; do PY_IDS+="'$i',"; done
  PY_IDS+="]"
fi

# --- always show the damage first -------------------------------------------
PREVIEW=$(uv run python -c "
import sys; sys.path.insert(0, '$APP')
from measurement.run_store_schema import delete_runs, DB_PATH
r = delete_runs(scope='$SCOPE', run_ids=$PY_IDS, dry_run=True, purge_orphan_logs=bool($ORPHANS), $FILTERS)
kids = ' '.join(f'{k}={v}' for k, v in (r.get('children') or {}).items()) or 'none'
logs = len(r.get('logs') or []) + len(r.get('orphan_logs') or [])
print(f\"{r['runs']}|{r.get('spared_locked',0)}|{kids}|{logs}\")
")
COUNT=${PREVIEW%%|*}; REST=${PREVIEW#*|}
SPARED=${REST%%|*}; REST2=${REST#*|}
KIDS=${REST2%%|*}; LOGS=${REST2#*|}

echo "=================================================================="
echo " scope        : $SCOPE"
[[ -n "$EXPERIMENT" ]] && echo " experiment   : $EXPERIMENT"
[[ -n "$RUN_PHASE" ]]  && echo " run phase    : $RUN_PHASE"
[[ -n "$MODEL" ]]      && echo " model        : $MODEL"
echo " runs to drop : $COUNT"
echo " child rows   : $KIDS"
echo " log files    : $LOGS"
[[ "$SCOPE" == "unlocked" ]] && echo " locked spared: $SPARED"
echo "=================================================================="

if [[ "$COUNT" -eq 0 && "$LOGS" -eq 0 ]]; then
  echo "Nothing to delete."
  exit 0
fi

if [[ "$DRY" -eq 1 ]]; then
  echo "(--dry-run: nothing was deleted)"
  exit 0
fi

# --- --all must be typed out, spelling the count ----------------------------
if [[ "$SCOPE" == "all" ]]; then
  echo
  echo "WARNING: --all deletes LOCKED runs too. Locked runs are the ones whose numbers"
  echo "         the write-up cites; re-creating them costs real API spend, and figures"
  echo "         regenerated later are not the figures you reported."
  echo
  read -r -p "Type 'delete $COUNT' to confirm: " REPLY_TEXT
  if [[ "$REPLY_TEXT" != "delete $COUNT" ]]; then
    echo "Aborted — nothing deleted."
    exit 1
  fi
fi

uv run python -c "
import sys; sys.path.insert(0, '$APP')
from measurement.run_store_schema import delete_runs
r = delete_runs(scope='$SCOPE', run_ids=$PY_IDS, purge_orphan_logs=bool($ORPHANS), $FILTERS)
print(f\"deleted {r['runs']} run(s)\")
for k, v in (r.get('children') or {}).items():
    print(f'  {k:16} {v} row(s)')
if r.get('logs'):
    print(f\"  {'log files':16} {len(r['logs'])} deleted\")
if r.get('spared_locked'):
    print(f\"  spared {r['spared_locked']} locked run(s)\")
"

echo
echo "remaining:"
uv run python "$APP/report_topology_run.py" --list 2>/dev/null | head -8
