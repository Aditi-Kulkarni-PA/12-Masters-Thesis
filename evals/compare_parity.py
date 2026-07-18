"""
Parity comparison: baseline (OpenAI SDK) vs MAF replica judge scores.

Usage:
    uv run python evals/compare_parity.py                          # latest run per stack
    uv run python evals/compare_parity.py --tolerance 0.5
    uv run python evals/compare_parity.py --baseline <file> --maf <file>

Reads judge_scores_*.json files from evals/reports/ (each is stamped with its
stack). Prints a per-agent, per-metric delta table and an overall PASS/FAIL
against the tolerance. Exit code 0 = parity holds, 1 = exceeded tolerance.
"""

import argparse
import json
import sys
from pathlib import Path

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
METRICS = ["relevance", "faithfulness", "safety", "mean"]


def _latest_for_stack(stack: str) -> Path | None:
    candidates = []
    for f in sorted(REPORTS_DIR.glob("judge_scores_*.json")):
        if f.name == "judge_scores_latest.json":
            continue
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        if data.get("stack") == stack:
            candidates.append(f)
    return candidates[-1] if candidates else None


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare judge scores across stacks")
    ap.add_argument("--baseline", type=Path, help="baseline judge_scores JSON (default: latest)")
    ap.add_argument("--maf", type=Path, help="MAF judge_scores JSON (default: latest)")
    ap.add_argument("--tolerance", type=float, default=0.5,
                    help="max acceptable |delta| per metric on the 1-5 judge scale (default 0.5)")
    ap.add_argument("--ragas-tolerance", type=float, default=0.05,
                    help="max acceptable |delta| per RAGAS metric on the 0-1 scale (default 0.05)")
    args = ap.parse_args()

    b_path = args.baseline or _latest_for_stack("baseline")
    m_path = args.maf or _latest_for_stack("maf")
    if not b_path or not m_path:
        missing = "baseline" if not b_path else "maf"
        sys.exit(f"No stack-stamped judge_scores file found for '{missing}'. "
                 f"Run: uv run python evals/run_evals.py --stack {missing}")

    base = json.loads(b_path.read_text())
    maf = json.loads(m_path.read_text())

    print(f"baseline: {b_path.name}  (generated {base.get('generated')})")
    print(f"maf     : {m_path.name}  (generated {maf.get('generated')})")
    print(f"tolerance: +/-{args.tolerance} per metric (1-5 judge scale)\n")

    agents = sorted(set(base["scores"]) | set(maf["scores"]))
    header = f"{'agent':38s}{'metric':14s}{'baseline':>9s}{'maf':>7s}{'delta':>8s}  flag"
    print(header)
    print("-" * len(header))

    worst = 0.0
    failures = []
    for agent in agents:
        b_s, m_s = base["scores"].get(agent), maf["scores"].get(agent)
        if b_s is None or m_s is None:
            print(f"{agent:38s}{'MISSING IN ' + ('MAF' if m_s is None else 'BASELINE'):>14s}")
            failures.append((agent, "missing"))
            continue
        for metric in METRICS:
            bv, mv = b_s.get(metric), m_s.get(metric)
            if bv is None or mv is None:
                continue
            delta = mv - bv
            worst = max(worst, abs(delta))
            flag = "" if abs(delta) <= args.tolerance else "  <-- EXCEEDS"
            if flag:
                failures.append((agent, metric))
            print(f"{agent:38s}{metric:14s}{bv:9.2f}{mv:7.2f}{delta:+8.2f}{flag}")
        print()

    # ---- RAGAS comparison (0-1 scale, finer-grained than the 1-5 judge) ----
    b_ragas, m_ragas = base.get("ragas", {}), maf.get("ragas", {})
    ragas_worst = 0.0
    if b_ragas or m_ragas:
        print("\nRAGAS metrics (0-1 scale, tolerance +/-%.2f)" % args.ragas_tolerance)
        print("-" * len(header))
        for agent in sorted(set(b_ragas) | set(m_ragas)):
            b_s, m_s = b_ragas.get(agent), m_ragas.get(agent)
            if b_s is None or m_s is None:
                print(f"{agent:38s}{'MISSING IN ' + ('MAF' if m_s is None else 'BASELINE'):>14s}")
                failures.append((agent, "ragas missing"))
                continue
            for metric in sorted(set(b_s) | set(m_s)):
                bv, mv = b_s.get(metric), m_s.get(metric)
                if bv is None or mv is None:
                    continue
                delta = mv - bv
                ragas_worst = max(ragas_worst, abs(delta))
                flag = "" if abs(delta) <= args.ragas_tolerance else "  <-- EXCEEDS"
                if flag:
                    failures.append((agent, f"ragas:{metric}"))
                note = " (lower=better)" if "hallucination" in metric else ""
                print(f"{agent:38s}{metric:22s}{bv:9.3f}{mv:7.3f}{delta:+8.3f}{flag}{note}")
    else:
        print("\nWARNING: no RAGAS scores found in one or both files — "
              "run the full suite (RAGAS comes from the rag eval) before calling parity.")

    print("\n" + "=" * len(header))
    if failures:
        print(f"PARITY: FAIL — {len(failures)} metric(s) exceed tolerance "
              f"(judge worst |delta| = {worst:.2f}, ragas worst |delta| = {ragas_worst:.3f})")
        sys.exit(1)
    print(f"PARITY: PASS — judge within +/-{args.tolerance} (worst {worst:.2f}), "
          f"RAGAS within +/-{args.ragas_tolerance} (worst {ragas_worst:.3f})")


if __name__ == "__main__":
    main()
