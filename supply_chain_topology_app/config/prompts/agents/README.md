# 00_shared_domain_agents/ — the frozen domain layer

Two kinds of file, both byte-identical across every topology.

## 1. Instruction files (copied verbatim from the live app)

`predict_delivery_delays.md`, `diagnose_delay_patterns.md`, `delay_simulation.md`,
`recommendation.md`, `email_alert.md`, `fallback_advisor.md`

These are the frozen substrate. **Never** edit them per topology — a `diff` between
two conditions' copies showing any difference is a bug, not a design choice. Every
topology's specialists get these; that is why no coordinator ever needs the domain
instructions restated into its own prompt.

## 2. Schema files (GENERATED — do not hand-edit)

`predict_output_schema.md`, `diagnose_output_schema.md`, `simulate_output_schema.md`,
`recommendation_output_schema.md`, `email_output_schema.md`

Produced from `core/schemas.py` by `outputs/gen_schema_files.py`, which walks the
Pydantic classes with `ast` and emits each `Field(description=...)` string verbatim.
Regenerate rather than edit; drift between these files and `core/schemas.py` is then
impossible by construction rather than prevented by discipline.

**Only Swarm includes these.** Every other condition's domain agents are pre-registered
in `core/agents.py` with `response_format=<Schema>`, so the framework enforces the
structure and restating it in a prompt would be dead tokens against a measured
variable. Swarm's specialists are described at runtime with no bound schema, so it
needs them. The files sit here rather than in `05_swarm/` because they are generated
from the same frozen source as the instruction files beside them — the other
conditions simply ignore them.

## BLOCKER: composing these requires a recursive include loader

`config/load_config.py` expands `@name` directives **one level only** — its own
docstring says so, and `_INCLUDE_RE.sub()` does not rescan what it substitutes. Two
consequences, verified by simulating the loader against this tree:

1. **Monolith is already broken today, independent of Swarm.** Its master inlines
   `@predict_delivery_delays` and `@diagnose_delay_patterns`; each of those contains
   `@field_glossary`, which lands at level 2 and is never expanded. The literal string
   `@field_glossary` reaches the model, twice, and the glossary those prompts depend on
   is silently absent.
2. **Swarm's `result_expectations.md` cannot compose the domain files** — nesting the
   ten `@` directives inside it leaks all ten as raw text.

Both are fixed by the same change, which makes expansion recursive with a depth cap:

```python
_MAX_INCLUDE_DEPTH = 5   # new module-level constant

def _expand_includes(text: str, _depth: int = 0) -> str:
    """Replace `@name` lines with the content of that prompt file (recursively)."""
    if _depth >= _MAX_INCLUDE_DEPTH:
        return text                      # cycle / runaway guard
    def _substitute(match: re.Match) -> str:
        try:
            return _expand_includes(_read_prompt(match.group(1)), _depth + 1)
        except FileNotFoundError:
            return match.group(0)        # leave the directive visible if missing
    return _INCLUDE_RE.sub(_substitute, text)
```

The module docstring's "One level deep — includes are not recursive" also needs
updating. Until this lands, both problems above are live. Tracked as **R24**.
