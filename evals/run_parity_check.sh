#!/usr/bin/env bash
# evals/run_parity_check.sh
#
# Compares a MAF eval run against the two frozen parity reference files.
# Never re-runs evals (no token cost) — only reads existing judge_scores
# JSON files already in evals/reports/.
#
# Usage:
#   ./evals/run_parity_check.sh                  # auto-picks the most recent judge_scores file
#   ./evals/run_parity_check.sh 20260719T140312   # use a specific timestamp instead
#
set -uo pipefail   # no -e: we need to capture both comparisons' exit codes, not bail on the first

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORTS_DIR="$SCRIPT_DIR/reports"
REF_DIR="$REPORTS_DIR/parity_reference"
REF_OPENAI="$SCRIPT_DIR/reports/parity_reference_maf_pre_refactor/judge_scores_openai_sdk_baseline.json"
REF_MAF_PRE="$SCRIPT_DIR/reports/parity_reference_maf_pre_refactor/judge_scores_maf_pre_refactor.json"


# --- sanity check: frozen references must exist ---
for ref in "$REF_OPENAI" "$REF_MAF_PRE"; do
    if [ ! -f "$ref" ]; then
        echo "ERROR: missing reference file: $ref" >&2
        echo "Set up parity_reference/ first — see evals/reports/parity_reference/README or the migration notes." >&2
        exit 1
    fi
done

# --- pick the MAF file to compare: explicit timestamp arg, or most recent by mtime ---
if [ $# -ge 1 ]; then
    TIMESTAMP="$1"
    MAF_FILE="$REPORTS_DIR/judge_scores_${TIMESTAMP}.json"
    if [ ! -f "$MAF_FILE" ]; then
        echo "ERROR: no file found at $MAF_FILE" >&2
        exit 1
    fi
else
    # -t sorts by modification time (newest first); excludes the always-overwritten
    # judge_scores_latest.json and anything under subdirectories (parity_reference/, archive*/).
    MAF_FILE=$(ls -t "$REPORTS_DIR"/judge_scores_*.json 2>/dev/null | grep -v "judge_scores_latest.json" | head -n 1)
    if [ -z "${MAF_FILE:-}" ]; then
        echo "ERROR: no judge_scores_*.json files found in $REPORTS_DIR" >&2
        echo "Run 'uv run python evals/run_evals.py --stack maf' first." >&2
        exit 1
    fi
fi

echo "Comparing MAF run: $MAF_FILE"
echo

echo "=================================================================="
echo "1) Post-refactor MAF  vs  frozen pre-refactor MAF (isolates refactor effect)"
echo "=================================================================="
uv run python "$SCRIPT_DIR/compare_parity.py" --baseline "$REF_MAF_PRE" --maf "$MAF_FILE"
STATUS_1=$?
echo

echo "=================================================================="
echo "2) Post-refactor MAF  vs  frozen OpenAI SDK baseline (original parity check)"
echo "=================================================================="
uv run python "$SCRIPT_DIR/compare_parity.py" --baseline "$REF_OPENAI" --maf "$MAF_FILE"
STATUS_2=$?
echo

if [ $STATUS_1 -ne 0 ] || [ $STATUS_2 -ne 0 ]; then
    echo "RESULT: One or more parity checks FAILED — see tables above."
    exit 1
fi
echo "RESULT: Both parity checks PASSED."
