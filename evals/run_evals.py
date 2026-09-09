"""
CLI runner for supply chain delivery evals.

Usage:
    uv run python evals/run_evals.py --stack baseline                    # full suite, OpenAI SDK app
    uv run python evals/run_evals.py --stack maf                         # full suite, MAF replica
    uv run python evals/run_evals.py --stack maf --agent recommend       # single agent
    uv run python evals/run_evals.py --help

Writes a JSON report to evals/reports/<timestamp>_<stack>.json.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

EVALS_DIR  = Path(__file__).resolve().parent
REPORTS_DIR = EVALS_DIR / "reports"

# --stack choice -> app directory the evals import delivery_agents from
from env_settings import STACK_APP_DIRS as _STACKS, PROJECT_ROOT

_AGENT_FILES = {
    "predict":   "test_eval_predict.py",
    "diagnose":  "test_eval_diagnose.py",
    "simulate":  "test_eval_simulate.py",
    "recommend": "test_eval_recommend.py",
    "email":     "test_eval_email.py",
    "rag":       "test_eval_rag.py",
}


def _build_pytest_args(agent: str | None, extra: list[str]) -> list[str]:
    base = [
        "uv", "run", "pytest",
        "--tb=short",
        "-v",
        "--json-report",
        "--json-report-indent=2",
    ]

    if agent:
        if agent not in _AGENT_FILES:
            print(f"Unknown agent '{agent}'. Choose from: {', '.join(_AGENT_FILES)}")
            sys.exit(1)
        base.append(str(EVALS_DIR / _AGENT_FILES[agent]))
    else:
        base.append(str(EVALS_DIR))

    base += extra
    return base


def _run(args: list[str], stack: str) -> dict:
    app_dir = _STACKS[stack]
    # Fail with an explanation rather than a confusing import error deep inside pytest.
    # The baseline app was removed from this repo on 9-Sep-26 and lives in the capstone project.
    if not (PROJECT_ROOT / app_dir).exists():
        raise SystemExit(
            f"Cannot run --stack {stack}: the app directory '{app_dir}' is not in this repository.\n"
            f"The OpenAI Agents SDK baseline was removed on 9-Sep-26 and now lives in the capstone "
            f"project. Copy it back under '{app_dir}', or run --stack maf, which is the stack the "
            f"topology experiment uses.")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{datetime.now().strftime('%Y%m%dT%H%M%S')}_{stack}.json"

    cmd = args + [f"--json-report-file={report_path}"]
    print(f"Stack: {stack}  (app dir: {app_dir})")
    print(f"Running: {' '.join(cmd)}\n")

    env = {**os.environ, "SC_EVAL_APP_DIR": app_dir}
    proc = subprocess.run(cmd, cwd=EVALS_DIR.parent, env=env)

    if report_path.exists():
        with report_path.open() as f:
            report = json.load(f)
        # Stamp the stack into the report so every JSON is self-documenting
        report["stack"] = stack
        report["app_dir"] = app_dir
        with report_path.open("w") as f:
            json.dump(report, f, indent=2)
        _print_summary(report, report_path)
    else:
        print(f"No JSON report written (pytest-json-report may not be installed).")
        report = {}

    return {"exit_code": proc.returncode, "report_path": str(report_path)}


def _print_summary(report: dict, path: Path):
    summary = report.get("summary", {})
    total   = summary.get("total", 0)
    passed  = summary.get("passed", 0)
    failed  = summary.get("failed", 0)
    skipped = summary.get("skipped", 0)
    duration = report.get("duration", 0)

    print("\n" + "=" * 60)
    print(f"EVAL RESULTS  —  {path.name}  [stack: {report.get('stack', '?')}]")
    print("=" * 60)
    print(f"  Total:   {total}")
    print(f"  Passed:  {passed}")
    print(f"  Failed:  {failed}")
    print(f"  Skipped: {skipped}")
    print(f"  Duration: {duration:.1f}s")

    if failed:
        print("\nFailed tests:")
        for test in report.get("tests", []):
            if test.get("outcome") == "failed":
                print(f"  FAIL  {test['nodeid']}")
                longrepr = test.get("call", {}).get("longrepr", "")
                if longrepr:
                    for line in str(longrepr).splitlines()[-5:]:
                        print(f"         {line}")

    status = "PASS" if failed == 0 else "FAIL"
    print(f"\nOverall: {status}")
    print("=" * 60)
    print(f"Full report: {path}")


def main():
    parser = argparse.ArgumentParser(description="Run supply chain delivery agent evals")
    parser.add_argument("--stack", choices=list(_STACKS.keys()), required=True,
                        help="Which app to evaluate: 'baseline' (OpenAI SDK) or 'maf' (Agent Framework replica)")
    parser.add_argument("--agent", choices=list(_AGENT_FILES.keys()),
                        help="Run evals for a single agent only")
    parser.add_argument("extra", nargs=argparse.REMAINDER,
                        help="Extra args passed directly to pytest")

    args = parser.parse_args()
    pytest_args = _build_pytest_args(args.agent, args.extra)
    result = _run(pytest_args, args.stack)
    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
