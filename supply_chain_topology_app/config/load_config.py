"""
Load agent instructions from Markdown prompt files.

Prompt files live under  config/prompts/  in two subdirectories:
  - agents/   — per-agent instruction files
  - shared/   — cross-cutting prompts (security_guardrails, chatbot_behavior,
                format_summary)

Loading is WYSIWYG: the full text of the agent's .md file IS the instruction
prompt — every section written in the file reaches the model verbatim.
One mechanism on top: include directives. A line containing only `@name` is
replaced with the content of that prompt file (e.g. `@field_glossary` pulls
in shared/field_glossary.md). Shared content is written once and included
wherever needed. Includes are expanded recursively, so an included file may
itself contain includes (e.g. a coordinator pulls in an agent prompt, which
pulls in @field_glossary). Expansion stops at _MAX_INCLUDE_DEPTH as a guard
against cycles; a directive left unexpanded at that depth stays visible in
the text rather than failing silently.

There are no special cases: even the master agent's security/behaviour
layering is expressed as includes at the top of master_expert.md
(`@security_guardrails`, then `@chatbot_behavior`), so the precedence order
is visible in the prompt file itself rather than hidden in this loader.
"""

import re
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_COORDINATORS_DIR = _PROMPTS_DIR / "coordinators"


def _search_dirs(topology: str | None = None) -> tuple[Path, ...]:
    """Directories searched for a prompt key, in precedence order.

    shared/ is checked FIRST so a topology can never shadow a shared file. That is
    deliberate: shared/ holds the cross-topology controls (dependency_discovery,
    self_check, output_contract...) whose whole value is being byte-identical
    everywhere. Letting coordinators/<topology>/ override one would silently break
    the comparison it exists to make possible.

    coordinators/<topology>/ is appended last, so a topology's own files
    (master.md, aggregator.md, and any topology-specific partials such as swarm's
    deliverable_contract.md) resolve only for that topology.
    """
    dirs = [_PROMPTS_DIR / "shared", _PROMPTS_DIR / "agents"]
    if topology:
        dirs.append(_COORDINATORS_DIR / topology)
    return tuple(dirs)

# Lines containing only "@name" are include directives
_INCLUDE_RE = re.compile(r"^@([A-Za-z0-9_]+)[ \t]*$", re.MULTILINE)

# Renders a capability list from the single source (shared/capability_details.md) with
# each callable name adjacent to its own description. Kept as a directive rather than
# four files of duplicated text: one source on disk, correct adjacency in the prompt.
_CAPABILITIES_RE = re.compile(r"^@@capabilities:([a-z_]+)[ \t]*$", re.MULTILINE)


def _expand_capabilities(text: str) -> str:
    def _sub(m):
        from core.tool_descriptions import render_flat
        return render_flat(f"{m.group(1)}_capabilities.md").rstrip()
    return _CAPABILITIES_RE.sub(_sub, text)

# Maximum include nesting depth. Guards against cycles (a.md includes b.md
# includes a.md) turning into infinite recursion. Current deepest real chain is
# coordinator -> agent prompt -> @field_glossary = 3, so 5 leaves headroom.
_MAX_INCLUDE_DEPTH = 5


def _read_prompt(agent_key: str, topology: str | None = None) -> str:
    """Return the raw text of the .md prompt file for *agent_key*."""
    dirs = _search_dirs(topology)
    for directory in dirs:
        path = directory / f"{agent_key}.md"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    searched = ", ".join(str(d.relative_to(_PROMPTS_DIR)) for d in dirs)
    raise FileNotFoundError(
        f"Prompt file not found for '{agent_key}' (searched: {searched})"
    )


def _expand_includes(text: str, topology: str | None = None, _depth: int = 0) -> str:
    """Replace `@name` lines with the content of that prompt file, recursively.

    Recursion lets an included file carry its own includes — e.g. a coordinator
    prompt pulls in an agent prompt, which in turn pulls in @field_glossary.
    Without it those inner directives reach the model as literal '@name' text
    and the content they name is silently missing.

    *topology* is threaded through so a coordinator's own partials resolve at every
    nesting level, not just the outermost one.
    """
    if _depth >= _MAX_INCLUDE_DEPTH:
        return text  # depth guard: leave remaining directives visible, don't recurse

    def _substitute(match: re.Match) -> str:
        try:
            return _expand_includes(
                _read_prompt(match.group(1), topology), topology, _depth + 1
            )
        except FileNotFoundError:
            return match.group(0)  # leave the directive visible if the file is missing
    return _expand_capabilities(_INCLUDE_RE.sub(_substitute, text))


def get_instruction(agent_key: str, topology: str | None = None) -> str:
    """
    Public API: return the full instruction prompt for *agent_key*.

    Used as Agent(instructions=get_instruction(...)) in core/agents.py.
    Uniform for every agent: read the .md file, expand `@name` includes.

    Pass *topology* when loading a coordinator prompt so that
    prompts/coordinators/<topology>/ joins the search path — e.g.
    get_instruction("master", topology="planner_executor"). Domain agent prompts
    are topology-neutral and need no topology argument.
    """
    return _expand_includes(_read_prompt(agent_key, topology), topology)
