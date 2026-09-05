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
wherever needed. One level deep — includes are not recursive.

There are no special cases: even the master agent's security/behaviour
layering is expressed as includes at the top of master_expert.md
(`@security_guardrails`, then `@chatbot_behavior`), so the precedence order
is visible in the prompt file itself rather than hidden in this loader.
"""

import re
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
# shared/ is checked first so shared prompt names always win over agent names.
_SEARCH_DIRS = (_PROMPTS_DIR / "shared", _PROMPTS_DIR / "agents")

# Lines containing only "@name" are include directives
_INCLUDE_RE = re.compile(r"^@([A-Za-z0-9_]+)[ \t]*$", re.MULTILINE)


def _read_prompt(agent_key: str) -> str:
    """Return the raw text of the .md prompt file for *agent_key*."""
    for directory in _SEARCH_DIRS:
        path = directory / f"{agent_key}.md"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(
        f"Prompt file not found for '{agent_key}' in agents/ or shared/"
    )


def _expand_includes(text: str) -> str:
    """Replace `@name` lines with the content of that prompt file (one level)."""
    def _substitute(match: re.Match) -> str:
        try:
            return _read_prompt(match.group(1))
        except FileNotFoundError:
            return match.group(0)  # leave the directive visible if the file is missing
    return _INCLUDE_RE.sub(_substitute, text)


def get_instruction(agent_key: str) -> str:
    """
    Public API: return the full instruction prompt for *agent_key*.

    Used as Agent(instructions=get_instruction(...)) in delivery_agents.py.
    Uniform for every agent: read the .md file, expand `@name` includes.
    """
    return _expand_includes(_read_prompt(agent_key))
