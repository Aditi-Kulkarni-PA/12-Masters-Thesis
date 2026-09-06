"""
Overnight warm-up: imports every major dependency this project actually uses, one at a
time, with a per-import timeout, logging progress as it goes. If tonight's pattern
holds (each file/extension is slow only on its first touch since some reset point,
then fast forever after), running this once lets that backlog clear unattended while
nothing else needs to happen -- so tomorrow's real run starts warm instead of cold.

Run from the repo root, in the background, so it survives closing the terminal:
    nohup .venv/bin/python3 warmup_venv.py > warmup.log 2>&1 &

Check progress any time with:
    tail -f warmup.log

Safe to leave running overnight. Delete this file once the investigation is done.
"""
import subprocess
import sys
import time

# Every top-level package this project's own import chain touches, roughly in the
# order execute_topology.py's real startup would reach them. Each runs as its own
# subprocess so one hanging import can't block the ones after it.
PACKAGES = [
    "pandas", "numpy", "matplotlib", "matplotlib.pyplot", "seaborn", "scipy",
    "scipy.stats", "scipy.interpolate", "IPython.display", "sklearn",
    "sklearn.ensemble", "sklearn.tree", "sklearn.linear_model", "sklearn.metrics",
    "sklearn.preprocessing", "sklearn.model_selection", "joblib", "chromadb",
    "sentence_transformers", "langchain_text_splitters", "openai", "anyio", "mcp",
    "mcp.server.fastmcp", "agent_framework", "agent_framework.openai",
]

PER_IMPORT_TIMEOUT_S = 300  # 5 minutes ceiling per package -- generous on purpose


def main():
    total = len(PACKAGES)
    for i, pkg in enumerate(PACKAGES, start=1):
        t0 = time.perf_counter()
        print(f"[{i}/{total}] importing {pkg} ...", flush=True)
        try:
            result = subprocess.run(
                [sys.executable, "-c", f"import {pkg}"],
                timeout=PER_IMPORT_TIMEOUT_S,
                capture_output=True,
                text=True,
            )
            elapsed = time.perf_counter() - t0
            if result.returncode == 0:
                print(f"[{i}/{total}] {pkg} OK ({elapsed:.1f}s)", flush=True)
            else:
                print(f"[{i}/{total}] {pkg} FAILED ({elapsed:.1f}s):\n{result.stderr}", flush=True)
        except subprocess.TimeoutExpired:
            elapsed = time.perf_counter() - t0
            print(f"[{i}/{total}] {pkg} TIMED OUT after {elapsed:.1f}s -- moving on", flush=True)
    print("WARMUP COMPLETE", flush=True)


if __name__ == "__main__":
    main()
