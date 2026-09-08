"""Classify what each non-executing run did instead of executing.

Fills run.behaviour_class, run.clarification_appropriate and run.behaviour_rationale
for every run whose status is 'no_execution'. Runs separately from the harness so a
measured run never depends on a classifier, and separately from scoring so it can be
re-run without re-scoring.

The judge is gpt-4.1-mini at temperature 0, the same fixed model the quality rubric
uses. Each call sees the query, its required capabilities and the run's output, and
returns a class plus a one-line rationale. Both are stored.

A run whose judgement fails is left NULL and reported, rather than guessed at: a wrong
label feeds a published rate, and NULL is visible while a plausible guess is not.

Run from the repo root, with --env-file so the judge gets its API key -- the same
invocation the other scripts use:

    uv run --env-file .env python supply_chain_topology_app/classify_behaviour.py
    uv run --env-file .env python supply_chain_topology_app/classify_behaviour.py --dry-run
    uv run --env-file .env python supply_chain_topology_app/classify_behaviour.py --run-id <id>

--dry-run still calls the judge; it only skips the database write. There is no offline
mode, because the classification is the model's judgement and nothing else stands in for
it. Twelve runs is roughly a few cents on gpt-4.1-mini.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from measurement.behaviour import classify
from measurement.run_store_schema import DB_PATH, migrate_schema

_JUDGE_MODEL = "gpt-4.1-mini"


def _judge(prompt: str) -> str:
    """One judge call. Imported lazily so --dry-run needs no API key."""
    from openai import OpenAI
    response = OpenAI().chat.completions.create(
        model=_JUDGE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


def classify_all(db_path: str = DB_PATH, dry_run: bool = False,
                 run_id: str | None = None) -> dict:
    migrate_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    where = "AND r.run_id = ?" if run_id else ""
    params = (run_id,) if run_id else ()
    rows = conn.execute(f"""
        SELECT r.run_id, r.model, r.topology, r.query_id, r.final_answer,
               m.query_text, m.implied_tools_json
        FROM run r LEFT JOIN query_metadata m ON m.query_id = r.query_id
        WHERE r.run_status = 'no_execution' {where}
        ORDER BY r.model, r.topology
    """, params).fetchall()

    summary = {"total": len(rows), "classified": 0, "failed": 0}
    for row in rows:
        required = json.loads(row["implied_tools_json"] or "[]")
        label = f"{row['model']} / {row['topology']} / {row['query_id']}"
        try:
            result = classify(row["final_answer"], query_text=row["query_text"] or "",
                              required=required, judge=_judge)
        except Exception as exc:
            summary["failed"] += 1
            print(f"  [FAILED] {label}: {exc}")
            continue

        summary["classified"] += 1
        flag = result["clarification_appropriate"]
        print(f"  {label}")
        print(f"     {result['behaviour_class']}"
              f"{'' if flag is None else f'  (appropriate={flag})'}")
        print(f"     {result['rationale']}")

        if not dry_run:
            conn.execute(
                "UPDATE run SET behaviour_class = ?, clarification_appropriate = ?, "
                "behaviour_rationale = ? WHERE run_id = ?",
                (result["behaviour_class"], flag, result["rationale"], row["run_id"]),
            )
    if not dry_run:
        conn.commit()
    conn.close()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--dry-run", action="store_true",
                        help="call the judge and print the labels, but write nothing "
                             "to the store. Still makes one API call per run.")
    parser.add_argument("--run-id", default=None, help="classify one run only")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"ERROR: run store not found at {args.db}.")
        return 1
    try:
        s = classify_all(args.db, dry_run=args.dry_run, run_id=args.run_id)
    except sqlite3.Error as exc:
        print(f"ERROR: the run store could not be updated: {exc}")
        return 1

    print()
    print(f"  runs to classify : {s['total']}")
    print(f"  classified       : {s['classified']}{' (dry run, nothing written)' if args.dry_run else ''}")
    if s["failed"]:
        print(f"  judge failed on  : {s['failed']} (left NULL)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
