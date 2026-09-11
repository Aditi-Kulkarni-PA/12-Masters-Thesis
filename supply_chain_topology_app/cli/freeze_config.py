"""
Config freeze manifest (T53): record everything a run's result depends on, and detect drift.

Writes a manifest of the frozen substrate -- model and generation settings, prompt
versions, the RAG corpus and its built index, the prediction models, and the frozen query
set -- so a later experiment can be shown to have run against the same substrate as an
earlier one, rather than assumed to have.

The RAG corpus is included not because retrieval is under study but because it is not.
The same corpus and the same index feed every topology in every experiment; if either is
rebuilt between the pilot and the main experiment, the recommend capability changes
underneath a comparison that is supposed to hold it fixed. A checksum turns that from an
assumption into a check.

Usage:
    python supply_chain_topology_app/cli/freeze_config.py --write     # create/replace
    python supply_chain_topology_app/cli/freeze_config.py --check     # compare, exit 1 on drift
    python supply_chain_topology_app/cli/freeze_config.py             # same as --check
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _APP_DIR.parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

MANIFEST_PATH = _APP_DIR / "config" / "freeze_manifest.json"

# What the manifest covers, as (label, path). A directory is hashed over every file it
# contains, in sorted order, so a rebuilt index changes the digest even when the file
# names stay the same.
_TARGETS: tuple[tuple[str, Path], ...] = (
    ("rag_corpus",        _APP_DIR / "knowledge"),
    ("rag_index",         _APP_DIR / "vectorstore"),
    ("prompts",           _APP_DIR / "config" / "prompts"),
    ("query_set",         _APP_DIR / "measurement" / "query_set_v1.xlsx"),
    ("prediction_models", _REPO_ROOT / "prediction_pipeline" / "models"),
)

# Generation settings that must not move between experiments. Sourced from the same place
# the runs read them, not re-parsed from .env, so the manifest records what the runs
# actually used.
_ENV_KEYS = ("OPENAI_MODEL", "OPENAI_MODEL_MINI", "SC_DEV_PATH_FALLBACK")


# Build artefacts and editor leftovers are excluded from every digest. They change
# without the substrate changing -- a .pyc is rewritten whenever the interpreter feels
# like it -- so hashing them would report drift on a substrate that had not moved, which
# is worse than not checking at all: a check that cries wolf stops being read.
_IGNORED_DIRS = {"__pycache__", ".ipynb_checkpoints", ".git"}
_IGNORED_SUFFIXES = {".pyc", ".pyo"}


def _is_ignored(rel: Path) -> bool:
    return (any(part in _IGNORED_DIRS for part in rel.parts)
            or rel.suffix in _IGNORED_SUFFIXES
            or rel.name.startswith(".~lock."))


def _digest(path: Path) -> dict:
    """sha256 over a file, or over every non-ignored file in a directory tree, sorted.

    An unreadable file is recorded rather than raised on: the manifest is meant to be
    runnable anywhere, and one locked file should not stop the other checksums from being
    taken. Unreadable files are surfaced in the result so they cannot pass as verified.
    """
    if not path.exists():
        return {"present": False, "sha256": None, "files": 0, "bytes": 0, "unreadable": []}
    h = hashlib.sha256()
    files = 0
    total = 0
    unreadable: list[str] = []

    if path.is_file():
        targets = [(path, Path(path.name))]
    else:
        targets = [(f, f.relative_to(path))
                   for f in sorted(p for p in path.rglob("*") if p.is_file())]
        targets = [(f, rel) for f, rel in targets if not _is_ignored(rel)]

    for f, rel in targets:
        try:
            data = f.read_bytes()
        except OSError as exc:
            unreadable.append(f"{rel}: {exc.strerror or exc}")
            continue
        # The name is hashed as well as the bytes, so a renamed file is a change.
        h.update(str(rel).encode())
        h.update(data)
        files += 1
        total += len(data)

    return {"present": True, "sha256": h.hexdigest(), "files": files, "bytes": total,
            "unreadable": unreadable}


def build() -> dict:
    """The manifest as it would be recorded right now."""
    try:
        from core.clients import MODEL, MODEL_MINI
        models = {"MODEL": MODEL, "MODEL_MINI": MODEL_MINI}
    except Exception as exc:                       # import must not break --check
        models = {"error": f"could not import core.clients: {exc}"}

    return {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "models": models,
        "env": {k: os.getenv(k) for k in _ENV_KEYS},
        "targets": {label: _digest(path) for label, path in _TARGETS},
    }


def _compare(frozen: dict, current: dict) -> list[str]:
    """Human-readable drift lines. Empty list means the substrate is unchanged."""
    drift = []
    for key in ("models", "env"):
        for k, v in frozen.get(key, {}).items():
            now = current.get(key, {}).get(k)
            if now != v:
                drift.append(f"{key}.{k}: frozen {v!r} -> now {now!r}")
    for label, spec in frozen.get("targets", {}).items():
        now = current.get("targets", {}).get(label, {})
        if spec.get("sha256") != now.get("sha256"):
            drift.append(
                f"{label}: digest changed "
                f"({spec.get('files')} files/{spec.get('bytes')} bytes -> "
                f"{now.get('files')} files/{now.get('bytes')} bytes)")
    return drift


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="record the current substrate as the frozen one")
    ap.add_argument("--check", action="store_true",
                    help="compare the current substrate against the manifest (the default)")
    args = ap.parse_args()

    current = build()

    if args.write:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        if MANIFEST_PATH.exists():
            prior = json.loads(MANIFEST_PATH.read_text())
            drift = _compare(prior, current)
            if drift:
                print("Replacing a manifest that no longer matches the substrate:")
                for d in drift:
                    print(f"  {d}")
                print()
        MANIFEST_PATH.write_text(json.dumps(current, indent=2) + "\n")
        print(f"Freeze manifest written to {MANIFEST_PATH.relative_to(_REPO_ROOT)}")
        for label, spec in current["targets"].items():
            state = (f"{spec['sha256'][:12]}  {spec['files']} file(s)"
                     if spec["present"] else "ABSENT")
            print(f"  {label:18} {state}")
            for u in spec.get("unreadable", []):
                print(f"                     ! unreadable, not covered: {u}")
        return 0

    if not MANIFEST_PATH.exists():
        print(f"No freeze manifest at {MANIFEST_PATH.relative_to(_REPO_ROOT)}.\n"
              f"  Create it with: python supply_chain_topology_app/cli/freeze_config.py --write")
        return 1

    frozen = json.loads(MANIFEST_PATH.read_text())
    drift = _compare(frozen, current)
    print(f"Frozen at : {frozen.get('created_at')}")
    for label, spec in current["targets"].items():
        for u in spec.get("unreadable", []):
            print(f"  note: {label} has an unreadable file, not covered by the digest: {u}")
    if not drift:
        print("Substrate matches the freeze manifest. Nothing has moved.")
        return 0
    print(f"DRIFT: {len(drift)} item(s) differ from the frozen substrate.\n")
    for d in drift:
        print(f"  {d}")
    print("\nRuns made now are not directly comparable with runs made against the frozen\n"
          "substrate. Either restore it, or record the change and treat the two sets as\n"
          "separate experiments.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
