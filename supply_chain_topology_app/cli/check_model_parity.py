"""Fails if any condition sets a model generation parameter that differs from the others.

The experiment manipulates orchestration topology and holds the model constant. That only
holds if every agent in every condition is created with the same generation settings.
Today temperature=0 is correct everywhere, but it is written as a separate literal in
roughly two dozen places across the nine topology files and core/agents.py, with nothing
asserting they agree. One edited literal would silently make one condition incomparable,
and nothing in the run store would show it: config_hash covers the model name and prompt
versions, not generation parameters.

What is checked and what is not
------------------------------------------------------------------------------
GENERATION parameters must be identical across every condition -- temperature, top_p,
seed, max_tokens, and the penalty settings. These change what the model produces and are
part of the controlled substrate.

ORCHESTRATION parameters are expected to differ and are ignored -- tool_choice,
response_format, store. Whether a coordinator is forced to call a tool, and what schema it
returns, IS the condition being compared; requiring parity there would forbid the designs.

Reads source with ast rather than importing, so the check runs without credentials, an
event loop, or any network call.

Usage:
    python check_model_parity.py            # exits non-zero on a mismatch
    python check_model_parity.py --verbose  # also lists every site found
"""

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path

APP = Path(__file__).resolve().parent.parent   # cli/ -> supply_chain_topology_app/

# Files that construct agents. core/agents.py holds the shared specialist factories; the
# topology modules build their own coordinators and, for the swarm pair, their specialists.
SOURCES = sorted((APP / "topologies").glob("*.py")) + [APP / "core" / "agents.py"]

# Parameters that change what the model generates. These must agree everywhere.
GENERATION_PARAMS = frozenset({
    "temperature", "top_p", "seed", "max_tokens",
    "frequency_penalty", "presence_penalty",
})

# Parameters that are part of a condition's design and are expected to differ.
ORCHESTRATION_PARAMS = frozenset({"tool_choice", "response_format", "store"})


def _literal(node):
    """The node's value if it is a literal, else a marker naming what it was.

    A non-literal (a variable, a call) cannot be compared across files by reading source,
    so it is reported rather than silently skipped -- an unresolvable parameter is exactly
    where drift would hide.
    """
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return f"<non-literal: {type(node).__name__}>"


def collect(path: Path) -> list[tuple[int, dict]]:
    """Every default_options dict literal in one file, as (line number, parameters)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.keyword) or node.arg != "default_options":
            continue
        if not isinstance(node.value, ast.Dict):
            found.append((node.value.lineno, {"<unparsed>": "not a dict literal"}))
            continue
        params = {}
        for k, v in zip(node.value.keys, node.value.values):
            key = _literal(k) if k is not None else "<**expansion>"
            params[key] = _literal(v)
        found.append((node.value.lineno, params))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verbose", action="store_true", help="list every site found")
    args = ap.parse_args()

    # value -> the sites that set it, per generation parameter
    seen: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    sites = 0
    unresolved: list[str] = []

    for path in SOURCES:
        if not path.exists():
            continue
        for lineno, params in collect(path):
            sites += 1
            where = f"{path.relative_to(APP)}:{lineno}"
            if args.verbose:
                shown = {k: v for k, v in params.items() if k in GENERATION_PARAMS}
                print(f"  {where:56} {shown or '(no generation params)'}")
            for key, value in params.items():
                if key in ORCHESTRATION_PARAMS:
                    continue
                if key not in GENERATION_PARAMS:
                    unresolved.append(f"{where}  unrecognised parameter {key!r}={value!r}")
                    continue
                seen[key][repr(value)].append(where)

    print(f"\nScanned {sites} agent construction site(s) across {len(SOURCES)} file(s).")

    failures = []
    for key in sorted(seen):
        values = seen[key]
        if len(values) == 1:
            only = next(iter(values))
            print(f"  OK   {key:20} = {only} at {len(values[only])} site(s)")
        else:
            failures.append(key)
            print(f"  FAIL {key:20} has {len(values)} different values:")
            for value, where in sorted(values.items()):
                for w in where:
                    print(f"         {value:>10}  {w}")

    # A parameter set in some conditions but not others is drift too: the unset ones take
    # the provider default, which is not guaranteed to equal the value written elsewhere.
    for key in sorted(seen):
        total = sum(len(v) for v in seen[key].values())
        if total != sites:
            print(f"  WARN {key:20} set at {total} of {sites} sites; the rest inherit the "
                  f"provider default, which may not match")

    if unresolved:
        print("\nParameters that could not be classified:")
        for u in unresolved:
            print(f"  {u}")

    if failures:
        print(f"\nFAILED: {len(failures)} generation parameter(s) differ across conditions: "
              f"{', '.join(failures)}")
        return 1
    print("\nPASSED: every generation parameter is identical across all conditions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
