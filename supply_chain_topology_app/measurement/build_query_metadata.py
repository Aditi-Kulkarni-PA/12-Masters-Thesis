"""T16 -- freeze/sync the evaluation query set into query_metadata.

Source of truth is the human-editable Excel file at measurement/query_set_v1.xlsx
(sheet "Query Set"), NOT this script. Edit the Excel, then re-run this script to sync
(run from the supply_chain_topology_app/ root, same convention as
measurement/run_store_schema.py):

    python measurement/build_query_metadata.py                 # sync query_set_v1.xlsx
    python measurement/build_query_metadata.py path/to/other.xlsx
    python measurement/build_query_metadata.py --dry-run        # preview, no DB write

Replaces the original placeholder version of this file, which hardcoded a single
Q_PLACEHOLDER_1 row directly in Python. That worked as a schema smoke test but meant
editing the query set meant editing code -- this reads the query set from the same
Excel file a human maintains, so "modify the Excel, re-run this script" is the whole
workflow (per Aditi, 23-Aug-26).

Column -> schema mapping (see data/query_set_v1.xlsx "README" sheet for the full
description of each column):
    capabilities    (comma-separated short names) -> implied_tools_json
                    (wrapped tool names, via core.tool_descriptions.TOOL_NAME_BY_CAPABILITY
                    -- the SAME map topologies/*.py uses to register these tools, so this
                    can never silently drift from what a topology actually calls)
                    ALSO -> query_complexity_score, computed from the short names via
                    CAPABILITY_WEIGHT below (not read from the Excel -- derived, so the
                    weighting has exactly one definition and can't drift from the figures
                    that read it). Floor value 1 for a query needing no capabilities
                    (e.g. Q1, the out-of-scope probe) -- see CAPABILITY_WEIGHT's docstring.
    complexity_bin  'low' | 'medium' | 'high' | 'very_high' -> query_metadata.complexity_bin
                    (read as-is from the Excel, NOT derived -- Aditi's stated bucketing,
                    5-Sep-26: {Q1,Q2}=low, {Q3,Q4,Q5}=medium, {Q6,Q7,Q8}=high,
                    {Q9,Q10,Q11}=very_high)
    scenario_tags   (comma-separated free text)    -> scenario_tags_json
    notes                                          -> NOT loaded; human reference only

Checks lock_rows on query_metadata before overwriting (Risk Log R26 discipline) -- but
note that column is currently a no-op in practice: query_metadata is in LOCK_TABLES (so
create_schema() would refuse to wipe it while a row is locked) but NOT in _RUN_SCOPED,
which is the list set_lock() actually cascades to. Nothing in the codebase currently sets
query_metadata.lock_rows = 1 for any row, so this guard costs nothing today and simply
future-proofs the sync if that ever changes -- it never blocks a real sync right now.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

import openpyxl

# This file lives in measurement/, but imports "measurement.X" / "core.X" like every
# other module here -- those only resolve if the app ROOT (not measurement/) is on
# sys.path. run_store_schema.py gets away without this because it has no such
# self-referencing imports; this script does, so it inserts its own root explicitly
# rather than depending on how/where it happens to be invoked from.
_MEASUREMENT_DIR = Path(__file__).resolve().parent
_APP_DIR = _MEASUREMENT_DIR.parent  # measurement/ -> supply_chain_topology_app/
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from measurement.run_store_schema import DB_PATH, migrate_schema
from core.tool_descriptions import TOOL_NAME_BY_CAPABILITY

DEFAULT_XLSX = _MEASUREMENT_DIR / "query_set_v1.xlsx"
_VALID_TIERS = {"simple", "multi_hop"}
_HEADERS = ("query_id", "query_text", "capabilities", "complexity_tier",
            "complexity_bin", "scenario_tags", "held_out", "query_set_version", "notes")

# Query complexity score (Aditi, 30-Aug-26, replacing a bare capability count as the
# progression axis used by the workload-scaling figures -- design spec's "The
# progression axis, corrected" section, docs/thesis-topology-tradeoffs/reporting/analysis-report-design-spec.md).
#
# A query's score is the SUM of its implied capabilities' weights, not a count of how
# many it needs -- two queries needing the same NUMBER of capabilities can carry very
# different real load (predict+email is lighter than predict+diagnose), and a count
# can't tell them apart. Weights are Aditi's stated low-to-high ordering, a design
# choice about relative capability weight, not something measured from run data --
# state that wherever the score is cited, don't read it as an empirical finding.
#
# Computed HERE, once, at query-set build time, and persisted to
# query_metadata.query_complexity_score -- every figure that needs this axis (F6, F7,
# F16, F8 in the design spec) reads the column rather than recomputing the weighting
# inline, so the weight table has exactly one definition project-wide.
#
# A query needing ZERO capabilities (Q1, an out-of-scope/refusal probe -- see this
# module's own row for it) is still placed on this axis, at its floor value of 1 --
# revised 5-Sep-26 (Aditi), superseding the 30-Aug-26 decision to score it NULL. The
# NULL choice was made specifically to avoid 0 reading as "the lightest real query"
# when it meant "not part of this axis at all" -- that concern stands for 0, but not
# for 1: a floor value of 1 keeps Q1 orderable alongside every other query on the same
# complexity axis (lowest, since it requires no capability execution at all) without
# colliding with a real single-capability query's own score (Q2/predict-only = 3).
# Query IDs were renumbered the same day (5-Sep-26) into ascending
# query_complexity_score order -- this comment already reflects the current numbering;
# see query_set_v1.xlsx's Q1 note for the full old-ID -> new-ID mapping.
# F14 in the design spec still measures Q1 separately on restraint, not on capability
# load -- this floor value is for the complexity-ordering axis only, not a claim that
# Q1 belongs to F14's restraint measurement.
CAPABILITY_WEIGHT = {
    "email":     1,
    "simulate":  2,
    "predict":   3,
    "diagnose":  4,
    "recommend": 5,
}

# Floor score for a query implying zero capabilities (currently only Q1) -- see the
# comment above CAPABILITY_WEIGHT for why this is 1, not 0 or NULL.
_ZERO_CAPABILITY_FLOOR_SCORE = 1

# Complexity bucket for the stratified quality table (Aditi, 5-Sep-26). Read as-is from
# the Excel's complexity_bin column, not derived from query_complexity_score -- the
# bucket boundaries are a separate design choice from the score itself and may not
# always fall at equal score intervals.
_VALID_COMPLEXITY_BINS = {"low", "medium", "high", "very_high"}


def query_complexity_score(capabilities: list[str]) -> int:
    """Sum of CAPABILITY_WEIGHT over *capabilities* (short names, e.g. 'predict'), or
    _ZERO_CAPABILITY_FLOOR_SCORE for a query needing no capabilities -- see the
    module-level note above CAPABILITY_WEIGHT for why that is a floor value of 1."""
    if not capabilities:
        return _ZERO_CAPABILITY_FLOOR_SCORE
    return sum(CAPABILITY_WEIGHT[c] for c in capabilities)


def _split(raw: str) -> list[str]:
    return [s.strip() for s in (raw or "").split(",") if s.strip()]


def read_query_set(xlsx_path: Path) -> list[dict]:
    """Read the 'Query Set' sheet into validated row dicts, ready for the DB."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    if "Query Set" not in wb.sheetnames:
        raise ValueError(f"{xlsx_path} has no 'Query Set' sheet (found {wb.sheetnames})")
    ws = wb["Query Set"]

    header_row = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    if tuple(header_row) != _HEADERS:
        raise ValueError(
            f"'Query Set' header row does not match expected columns.\n"
            f"  expected: {_HEADERS}\n  found:    {tuple(header_row)}")

    rows = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not any(r):
            continue  # skip fully blank rows
        query_id, query_text, capabilities, complexity_tier, complexity_bin, \
            scenario_tags, held_out, query_set_version, notes = r

        errors = []
        if not query_id:
            errors.append("query_id is blank")
        if not query_text:
            errors.append("query_text is blank")
        if complexity_tier not in _VALID_TIERS:
            errors.append(f"complexity_tier {complexity_tier!r} not in {_VALID_TIERS}")
        if complexity_bin not in _VALID_COMPLEXITY_BINS:
            errors.append(f"complexity_bin {complexity_bin!r} not in {_VALID_COMPLEXITY_BINS}")

        caps = _split(capabilities)
        unknown = [c for c in caps if c not in TOOL_NAME_BY_CAPABILITY]
        if unknown:
            errors.append(
                f"unrecognized capabilities {unknown} -- must be from "
                f"{sorted(TOOL_NAME_BY_CAPABILITY)}")

        if errors:
            raise ValueError(f"{query_id or '<blank id>'}: " + "; ".join(errors))

        rows.append({
            "query_id": str(query_id).strip(),
            "query_text": str(query_text).strip(),
            "complexity_tier": complexity_tier,
            "complexity_bin": complexity_bin,
            "implied_tools_json": json.dumps([TOOL_NAME_BY_CAPABILITY[c] for c in caps]),
            "scenario_tags_json": json.dumps(_split(scenario_tags)),
            "held_out": int(held_out or 0),
            "query_set_version": str(query_set_version or "").strip(),
            "query_complexity_score": query_complexity_score(caps),
        })

    seen = set()
    dupes = {r["query_id"] for r in rows if r["query_id"] in seen or seen.add(r["query_id"])}
    if dupes:
        raise ValueError(f"duplicate query_id(s) in {xlsx_path}: {sorted(dupes)}")

    return rows


def sync_to_db(rows: list[dict], db_path: str = DB_PATH, dry_run: bool = False) -> dict:
    """Upsert rows into query_metadata, refusing to touch any locked row."""
    migrate_schema(db_path)  # ensures query_metadata.lock_rows exists on older DBs
    conn = sqlite3.connect(db_path)
    try:
        locked = {
            qid for (qid,) in conn.execute(
                "SELECT query_id FROM query_metadata WHERE lock_rows = 1")
        }
        to_write = [r for r in rows if r["query_id"] not in locked]
        skipped = [r["query_id"] for r in rows if r["query_id"] in locked]

        if not dry_run:
            for r in to_write:
                conn.execute(
                    """INSERT OR REPLACE INTO query_metadata
                       (query_id, query_text, complexity_tier, complexity_bin,
                        implied_tools_json, scenario_tags_json, held_out,
                        query_set_version, query_complexity_score)
                       VALUES (:query_id, :query_text, :complexity_tier, :complexity_bin,
                               :implied_tools_json, :scenario_tags_json, :held_out,
                               :query_set_version, :query_complexity_score)""",
                    r,
                )
            conn.commit()
        return {"written": [r["query_id"] for r in to_write], "skipped_locked": skipped}
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xlsx", nargs="?", default=str(DEFAULT_XLSX),
                     help=f"path to the query-set workbook (default: {DEFAULT_XLSX})")
    ap.add_argument("--dry-run", action="store_true", help="preview only, no DB write")
    args = ap.parse_args()

    xlsx_path = Path(args.xlsx)
    if not xlsx_path.exists():
        print(f"error: {xlsx_path} not found", file=sys.stderr)
        sys.exit(1)

    rows = read_query_set(xlsx_path)
    result = sync_to_db(rows, dry_run=args.dry_run)

    verb = "Would write" if args.dry_run else "Wrote"
    print(f"{verb} {len(result['written'])} row(s) to query_metadata from {xlsx_path.name}:")
    for r in rows:
        if r["query_id"] in result["written"]:
            n_tools = len(json.loads(r["implied_tools_json"]))
            score = r["query_complexity_score"]
            score_str = f"score={score:>2}" if score is not None else "score=N/A"
            print(f"  {r['query_id']:5} [{r['complexity_tier']:9}] [{r['complexity_bin']:9}] "
                  f"{score_str} {n_tools} tool(s) -- {r['query_text'][:70]}")
    if result["skipped_locked"]:
        print(f"\nSkipped (lock_rows=1, protected from overwrite): {result['skipped_locked']}")
        print("  Nothing in the codebase currently sets this lock for query_metadata rows "
              "(see the module docstring) -- if you're seeing this, something new set it "
              "deliberately, so check before clearing it by hand in the DB.")


if __name__ == "__main__":
    main()
