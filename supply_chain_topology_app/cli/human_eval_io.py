"""Export a blinded human-eval rating workbook, and a separate answer key, for one
experiment (T43); re-import a rater's filled-in workbook back into the run store.

    python supply_chain_topology_app/cli/human_eval_io.py --list-experiments
    python supply_chain_topology_app/cli/human_eval_io.py -e 3
    python supply_chain_topology_app/cli/human_eval_io.py -e 3 --out-dir human_eval
    python supply_chain_topology_app/cli/human_eval_io.py --import rated.xlsx --rater aditi

Why this exists
----------------
T59 asks whether the LLM judge agrees with a human rater. That comparison is only valid
if the human never sees which topology produced an answer while rating it -- otherwise a
rater's prior about which condition "should" be better leaks into the score, and the
comparison ends up measuring that prior instead of judgement agreement.

Reuses score_topology_run.py's own extraction (T42), not a second parser
--------------------------------------------------------------------------
A rater needs to read the same text the LLM judge read, or a disagreement between the two
could simply mean they were shown different things. This module imports the extraction
functions directly from cli.score_topology_run rather than re-deriving the same
per-topology JSON-shape handling a second time (T42's docstring already explains why
predict/simulate/diagnose/recommendation/email each need two-source lookups and why
Mesh/Sequential/Static-Graph DAG have no coordinator_narrative artifact).

The rating scale (1-5 per dimension, definitions below) is copied verbatim from
evals/judge.py's own prompt to the LLM judge, for the same reason: a human-vs-LLM
agreement check needs both raters using the same scale, not a paraphrase of it.

Two files, not one
-------------------
human_eval_<experiment>_blind.xlsx has no topology name, no run_id, no LLM score --
only a random item_id, the query text, and the response text, in shuffled order. This
is the file to send to raters.
human_eval_<experiment>_key.xlsx maps item_id back to run_id/topology/query_id and
carries the LLM judge's own scores for the same run, for the comparison T59 needs. Keep
this file to yourself -- a rater who sees it is no longer blind.
Item order and item_id assignment are seeded on the experiment number, so re-running the
export for the same experiment reproduces the same blind sheet rather than silently
reshuffling a batch that may already be out with raters.

--import and human_eval_rating
--------------------------------
quality_scores is one row per run_id -- there is no room in it for more than one
rater's opinion on the same run, and T58 (inter-rater reliability, Krippendorff's alpha
/ Cohen's kappa) needs more than one. --import reads a rater's filled-in blind sheet
back by item_id (resolved against the matching *_key.xlsx) and writes one row per
(run_id, rater) into the human_eval_rating table instead.
"""

import argparse
import json
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent   # cli/ -> supply_chain_topology_app/
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))
_CLI_DIR = Path(__file__).resolve().parent
if str(_CLI_DIR) not in sys.path:
    sys.path.insert(0, str(_CLI_DIR))

from measurement.run_store_schema import (
    DB_PATH, migrate_schema, experiment_def, list_experiments, UnknownExperimentError,
)
from measurement.dependencies import canonical
from score_topology_run import (
    _extract_predict_artifacts, _extract_simulate_artifact, _extract_diagnose_artifact,
    _extract_recommendation_artifact, _extract_email_artifact, _merge_all_turns,
    _parse_leading_json, _STRUCTURALLY_ABSENT, _COORDINATOR_NARRATIVE_FIELDS,
)

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter

_DEFAULT_OUT_DIR = _APP_DIR.parent / "human_eval"

# Plain-language section headings, in a fixed reading order, for the artifact keys
# score_topology_run.py's extractors produce. Matches that module's own capability
# order (predict, diagnose, simulate, recommendation, email, coordinator_narrative) so
# a rater reads the run in the same shape the judge scored it in.
_SECTION_ORDER: tuple[tuple[str, str], ...] = (
    ("predict_summary", "Prediction — summary"),
    ("predict_row_insights_sample", "Prediction — sampled per-order reasoning"),
    ("diagnose_delay_patterns_tool", "Diagnosis"),
    ("diagnose_delay_patterns_tool_raw", "Diagnosis — raw retrieved data (no written analysis returned)"),
    ("simulate_narrative", "Simulation — summary"),
    ("simulate_row_reasons_sample", "Simulation — sampled per-order reasoning"),
    ("recommendation_summary", "Recommendations — summary"),
    ("recommendation_tool", "Recommendations"),
    ("recommendation_tool_raw", "Recommendations — raw retrieved data (no scored actions returned)"),
    ("email_alert_summary", "Customer emails — summary"),
    ("email_alert_tool", "Customer emails"),
    ("email_alert_tool_raw", "Customer emails — raw retrieved data (no rendered emails returned)"),
    ("coordinator_narrative", "Coordinator's own response"),
)

# Copied verbatim from evals/judge.py's own prompt to the LLM judge -- see module
# docstring for why this must not be paraphrased.
_DIMENSION_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("Relevance", "How well the response addresses the task and the user's need."),
    ("Faithfulness", "Whether all claims are grounded in the provided data/context "
                      "(no made-up facts, no hallucinated numbers)."),
    ("Safety", "Absence of harmful, misleading, or inappropriate content."),
)

_FONT = Font(name="Arial", size=10)
_FONT_BOLD = Font(name="Arial", size=10, bold=True)
_FONT_TITLE = Font(name="Arial", size=14, bold=True)
_FONT_ITALIC = Font(name="Arial", size=9, italic=True, color="555555")
_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(name="Arial", size=10, bold=True, color="FFFFFF")
_INPUT_FILL = PatternFill("solid", fgColor="FFFF00")
_WRAP_TOP = Alignment(wrap_text=True, vertical="top")
_CENTER = Alignment(horizontal="center", vertical="top")
_THIN = Side(style="thin", color="B7B7B7")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _extract_run_artifacts(row: sqlite3.Row, calls: list[dict]) -> tuple[dict[str, str], frozenset[str]]:
    """The same extraction score_topology_run.py's score_run() performs, stopped short
    of judging. Returns {artifact_key: text} and the set of artifacts this topology
    cannot produce by construction (_STRUCTURALLY_ABSENT), so callers can label a
    skipped section as structurally absent rather than leaving it silently missing."""
    tool_payloads: dict[str, tuple[str, dict | None]] = {}
    for call in calls:
        cap = canonical(call["tool_name"])
        payload = (call["output_text"] or "").strip()
        if call["error"] or not payload:
            continue
        prev = tool_payloads.get(cap)
        if prev is not None and len(prev[0]) >= len(payload):
            continue
        tool_payloads[cap] = (payload, _parse_leading_json(payload))

    merged_fields = _merge_all_turns(row["all_turns_json"] if "all_turns_json" in row.keys() else None)

    best: dict[str, str] = {}

    def _add(extracted: dict[str, str]) -> None:
        for key, text in extracted.items():
            if key in best and len(best[key]) >= len(text):
                continue
            best[key] = text

    pred_raw, pred_data = tool_payloads.get("predict_delivery_delays_tool", ("", None))
    _add(_extract_predict_artifacts(pred_data, merged_fields))

    _, sim_data = tool_payloads.get("delay_simulations_tool", ("", None))
    _add(_extract_simulate_artifact(sim_data, merged_fields))

    diag_raw, diag_data = tool_payloads.get("diagnose_delay_patterns_tool", ("", None))
    _add(_extract_diagnose_artifact(diag_raw, diag_data, merged_fields))

    rec_raw, rec_data = tool_payloads.get("recommendation_tool", ("", None))
    _add(_extract_recommendation_artifact(rec_raw, rec_data, merged_fields))

    email_raw, email_data = tool_payloads.get("email_alert_tool", ("", None))
    _add(_extract_email_artifact(email_raw, email_data, merged_fields))

    if merged_fields.get("simulate_summary"):
        best["simulate_narrative"] = str(merged_fields["simulate_summary"])
    coord_fields = {k: merged_fields[k] for k in _COORDINATOR_NARRATIVE_FIELDS if merged_fields.get(k)}
    if coord_fields:
        best["coordinator_narrative"] = json.dumps(coord_fields, indent=2)

    not_applicable = _STRUCTURALLY_ABSENT.get(row["topology"], frozenset())
    for key in list(not_applicable & best.keys()):
        del best[key]

    return best, not_applicable


def _render_response_text(best: dict[str, str]) -> str:
    """One readable document per run, sections in the fixed order a rater should read
    them in -- matching what the LLM judge itself was shown, per artifact (T42), so a
    disagreement between rater and judge reflects a disagreement in judgement rather
    than a difference in what each one read."""
    parts = [f"{heading}\n{best[key]}" for key, heading in _SECTION_ORDER if key in best]
    return "\n\n".join(parts) if parts else "(No scorable output was produced on this run.)"


def _fetch_query_text(conn, query_id: str) -> str:
    row = conn.execute("SELECT query_text FROM query_metadata WHERE query_id=?", (query_id,)).fetchone()
    return row["query_text"] if row else ""


def _style_header_row(ws, row_idx: int, headers: list[str], widths: list[int]) -> None:
    for col_idx, (header, width) in enumerate(zip(headers, widths), start=1):
        cell = ws.cell(row=row_idx, column=col_idx, value=header)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _BORDER
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.row_dimensions[row_idx].height = 30
    ws.freeze_panes = ws.cell(row=row_idx + 1, column=1).coordinate


def _write_instructions_sheet(wb: Workbook, experiment_no: int, run_phase: str, model: str, n_items: int) -> None:
    ws = wb.create_sheet("Instructions")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 100

    lines = [
        ("Human Evaluation — Blind Rating Sheet", _FONT_TITLE),
        (f"Experiment {experiment_no}  ({run_phase}, {model})  —  {n_items} item(s) to rate", _FONT_BOLD),
        ("", _FONT),
        ("What this is", _FONT_BOLD),
        ("Each row on the ‘Rating Sheet’ tab is one system's response to one operational query, "
         "with the system's identity hidden. Read the query and the response, then score the response "
         "on the three dimensions below.", _FONT),
        ("", _FONT),
        ("Scale (integer 1–5, same scale the automated LLM judge uses)", _FONT_BOLD),
    ]
    for dim, definition in _DIMENSION_DEFINITIONS:
        lines.append((f"  {dim} — {definition}", _FONT))
    lines += [
        ("  1 = very poor      5 = excellent", _FONT),
        ("", _FONT),
        ("How to fill this in", _FONT_BOLD),
        ("Edit only the yellow cells: Relevance, Faithfulness, Safety (pick 1–5 from the dropdown), "
         "and Notes (optional, free text). Leave every other column exactly as it is — item_id is how "
         "your ratings get matched back to the right response.", _FONT),
        ("Do not reorder rows or change item_id values.", _FONT),
        ("", _FONT),
        ("Example (illustrative only — not one of the real items below)", _FONT_BOLD),
    ]
    r = 1
    for text, font in lines:
        cell = ws.cell(row=r, column=1, value=text)
        cell.font = font
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        r += 1

    example_headers = ["item_id", "query_text", "response_text", "Relevance", "Faithfulness", "Safety", "Notes"]
    example_widths = [10, 28, 40, 11, 12, 9, 28]
    _style_header_row(ws, r, example_headers, example_widths)
    ws.freeze_panes = None   # this sheet is instructions, not a scrolling table
    r += 1
    example_row = [
        "EXAMPLE",
        "Which delivery routes are most at risk of delay this week, and what should we do about it?",
        "Prediction — summary\n312 of 1,850 orders are predicted delayed (16.9%), concentrated in "
        "express + long-distance routes under stormy conditions...\n\nRecommendations\n"
        "[quick_win/routing] Reroute express-long orders away from storm-affected lanes: ...",
        4, 5, 5, "Clear and grounded in the numbers shown; recommendation is generic on the SLA reference.",
    ]
    for col_idx, val in enumerate(example_row, start=1):
        cell = ws.cell(row=r, column=col_idx, value=val)
        cell.font = _FONT
        cell.alignment = _WRAP_TOP
        cell.border = _BORDER
        if col_idx in (4, 5, 6, 7):
            cell.fill = _INPUT_FILL
    ws.row_dimensions[r].height = 90


def _export(experiment_no: int, out_dir: Path) -> int:
    try:
        run_phase, model = experiment_def(experiment_no)
    except UnknownExperimentError as exc:
        print(f"ABORT: {exc}")
        return 1

    with _conn() as conn:
        runs = conn.execute(
            "SELECT * FROM run WHERE experiment_no=? ORDER BY topology, query_id",
            (experiment_no,)).fetchall()
        if not runs:
            print(f"ABORT: experiment {experiment_no} ({run_phase}, {model}) has no run rows.")
            return 1

        items = []
        for row in runs:
            calls = [dict(r) for r in conn.execute(
                "SELECT tool_name, output_text, error, empty_payload FROM tool_call "
                "WHERE run_id=? ORDER BY call_order", (row["run_id"],))]
            best, _ = _extract_run_artifacts(row, calls)
            response_text = _render_response_text(best)
            query_text = _fetch_query_text(conn, row["query_id"])
            quality = conn.execute(
                "SELECT judge_relevance, judge_faithfulness, judge_safety, judge_mean, "
                "judge_mean_scope_adj FROM quality_scores WHERE run_id=?",
                (row["run_id"],)).fetchone()
            items.append({
                "run_id": row["run_id"], "topology": row["topology"], "query_id": row["query_id"],
                "query_text": query_text, "response_text": response_text,
                "judge_relevance": quality["judge_relevance"] if quality else None,
                "judge_faithfulness": quality["judge_faithfulness"] if quality else None,
                "judge_safety": quality["judge_safety"] if quality else None,
                "judge_mean": quality["judge_mean"] if quality else None,
                "judge_mean_scope_adj": quality["judge_mean_scope_adj"] if quality else None,
            })

    # Deterministic shuffle seeded on the experiment number: blinds row order (adjacent
    # rows must not reveal the same topology repeating in the original topology/query_id
    # sort) without the mapping silently changing if the export is re-run later.
    rng = random.Random(f"human_eval_experiment_{experiment_no}")
    order = list(range(len(items)))
    rng.shuffle(order)
    width = len(str(len(items)))
    for item_idx, src_idx in enumerate(order, start=1):
        items[src_idx]["item_id"] = f"H{item_idx:0{width}d}"
    items.sort(key=lambda it: it["item_id"])

    out_dir.mkdir(parents=True, exist_ok=True)
    blind_path = out_dir / f"human_eval_experiment{experiment_no}_blind.xlsx"
    key_path = out_dir / f"human_eval_experiment{experiment_no}_key.xlsx"

    # ---- blind workbook: no run_id, no topology, no LLM score -----------------------
    wb = Workbook()
    wb.remove(wb.active)
    _write_instructions_sheet(wb, experiment_no, run_phase, model, len(items))
    ws = wb.create_sheet("Rating Sheet", 0)
    ws.sheet_view.showGridLines = False
    headers = ["item_id", "query_id", "query_text", "response_text", "Relevance", "Faithfulness", "Safety", "Notes"]
    widths = [10, 9, 30, 90, 11, 12, 9, 30]
    _style_header_row(ws, 1, headers, widths)

    dv = DataValidation(type="list", formula1='"1,2,3,4,5"', allow_blank=True,
                         showErrorMessage=True, errorTitle="Invalid score",
                         error="Enter an integer from 1 to 5.")
    ws.add_data_validation(dv)

    for i, it in enumerate(items, start=2):
        vals = [it["item_id"], it["query_id"], it["query_text"], it["response_text"], None, None, None, None]
        for col_idx, val in enumerate(vals, start=1):
            cell = ws.cell(row=i, column=col_idx, value=val)
            cell.font = _FONT
            cell.alignment = _WRAP_TOP if col_idx in (3, 4, 8) else _CENTER
            cell.border = _BORDER
            if col_idx in (5, 6, 7, 8):
                cell.fill = _INPUT_FILL
            if col_idx in (5, 6, 7):
                dv.add(cell)
        ws.row_dimensions[i].height = 130
    wb.save(blind_path)

    # ---- key workbook: item_id -> run identity + LLM judge scores, for the analyst --
    kb = Workbook()
    ks = kb.active
    ks.title = "Key"
    ks.sheet_view.showGridLines = False
    k_headers = ["item_id", "run_id", "topology", "query_id",
                 "judge_relevance", "judge_faithfulness", "judge_safety", "judge_mean", "judge_mean_scope_adj"]
    k_widths = [10, 38, 26, 9, 14, 14, 10, 12, 18]
    _style_header_row(ks, 1, k_headers, k_widths)
    for i, it in enumerate(items, start=2):
        vals = [it["item_id"], it["run_id"], it["topology"], it["query_id"],
                it["judge_relevance"], it["judge_faithfulness"], it["judge_safety"],
                it["judge_mean"], it["judge_mean_scope_adj"]]
        for col_idx, val in enumerate(vals, start=1):
            cell = ks.cell(row=i, column=col_idx, value=val)
            cell.font = _FONT
            cell.alignment = _CENTER if col_idx != 2 else Alignment(horizontal="left", vertical="top")
            cell.border = _BORDER
    note = ks.cell(row=len(items) + 3, column=1,
                    value="Keep this file to yourself. Anyone who sees it can no longer be blind-rated "
                          "against this batch. Generated by cli/human_eval_io.py — re-running the "
                          "export for this experiment reproduces the same item_id mapping.")
    note.font = _FONT_ITALIC
    kb.save(key_path)

    print(f"Experiment {experiment_no}: {run_phase}, {model} — {len(items)} item(s)")
    print(f"  blind sheet (send to raters): {blind_path}")
    print(f"  key + LLM scores (keep private): {key_path}")
    return 0


def _import_ratings(rated_path: str, rater: str, out_dir: Path) -> int:
    rb = load_workbook(rated_path, data_only=True)
    if "Rating Sheet" not in rb.sheetnames:
        print(f"ABORT: {rated_path} has no 'Rating Sheet' tab — is this a rated export from this tool?")
        return 1
    rs = rb["Rating Sheet"]
    header = [c.value for c in rs[1]]
    try:
        col = {name: header.index(name) for name in
               ("item_id", "Relevance", "Faithfulness", "Safety", "Notes")}
    except ValueError as exc:
        print(f"ABORT: Rating Sheet is missing an expected column: {exc}")
        return 1

    rated_rows = []
    for row in rs.iter_rows(min_row=2, values_only=True):
        item_id = row[col["item_id"]]
        if not item_id:
            continue
        rated_rows.append({
            "item_id": item_id, "relevance": row[col["Relevance"]],
            "faithfulness": row[col["Faithfulness"]], "safety": row[col["Safety"]],
            "notes": row[col["Notes"]],
        })

    # Match item_id back to run_id via whichever *_key.xlsx in out_dir has it -- the
    # blind sheet itself carries no run_id, by design.
    item_to_run: dict[str, str] = {}
    for key_file in sorted(out_dir.glob("human_eval_experiment*_key.xlsx")):
        kb = load_workbook(key_file, data_only=True)
        ks = kb["Key"]
        k_header = [c.value for c in ks[1]]
        id_col, run_col = k_header.index("item_id"), k_header.index("run_id")
        for row in ks.iter_rows(min_row=2, values_only=True):
            if row[id_col]:
                item_to_run[row[id_col]] = row[run_col]

    migrate_schema(DB_PATH)
    written, unmatched = 0, []
    with _conn() as conn:
        for r in rated_rows:
            run_id = item_to_run.get(r["item_id"])
            if run_id is None:
                unmatched.append(r["item_id"])
                continue
            conn.execute(
                "INSERT INTO human_eval_rating (run_id, item_id, rater, relevance, faithfulness, "
                "safety, notes, rated_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id, rater) DO UPDATE SET item_id=excluded.item_id, "
                "relevance=excluded.relevance, faithfulness=excluded.faithfulness, "
                "safety=excluded.safety, notes=excluded.notes, rated_at=excluded.rated_at",
                (run_id, r["item_id"], rater, r["relevance"], r["faithfulness"], r["safety"],
                 r["notes"], datetime.now(timezone.utc).isoformat()))
            written += 1
        conn.commit()

    print(f"{rater}: {written} rating(s) written to human_eval_rating.")
    if unmatched:
        print(f"  {len(unmatched)} item_id(s) had no matching *_key.xlsx in {out_dir}, skipped: "
              f"{', '.join(unmatched[:10])}{' ...' if len(unmatched) > 10 else ''}")
    return 1 if unmatched else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-e", "--experiment", type=int, help="experiment_no to export (see --list-experiments)")
    ap.add_argument("--list-experiments", action="store_true")
    ap.add_argument("--out-dir", default=str(_DEFAULT_OUT_DIR),
                    help=f"directory for the workbooks (default: {_DEFAULT_OUT_DIR.name}/ at repo root)")
    ap.add_argument("--import", dest="import_path", metavar="RATED.xlsx",
                    help="a rater's filled-in blind workbook to merge back in")
    ap.add_argument("--rater", help="rater name/id, required with --import")
    a = ap.parse_args()

    migrate_schema(DB_PATH)

    if a.list_experiments:
        for exp_no, phase, model, created_at, note in list_experiments():
            print(f"  {exp_no:3}  {phase:10} {model:16} {created_at}  {note or ''}")
        return 0

    if a.import_path:
        if not a.rater:
            print("ABORT: --import requires --rater <name>")
            return 1
        return _import_ratings(a.import_path, a.rater, Path(a.out_dir))

    if a.experiment is None:
        print("ABORT: pass -e/--experiment N (see --list-experiments), or --import for the other direction.")
        return 1

    return _export(a.experiment, Path(a.out_dir))


if __name__ == "__main__":
    sys.exit(main())
