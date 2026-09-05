"""
Prompt version hashing (T14) — content hash per instruction, so run records can
distinguish "before fix" from "after fix" results when a prompt is edited mid-project.

Hashes the FULLY EXPANDED instruction string, not the raw .md bytes. The previous
version hashed only config/prompts/agents/*.md, so an edit to anything under shared/
was invisible in the run manifest — and shared/ is exactly where the cross-topology
controls live (dependency_discovery, self_check, output_contract, chatbot_behavior).
A change to one of those alters every condition's behaviour while leaving every
recorded hash untouched, which would make two incomparable runs look identical.
Hashing post-expansion means the hash moves whenever any include in the chain moves.
"""
import hashlib

from config.load_config import get_instruction


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def get_prompt_versions(
    agent_names: list[str],
    topology: str | None = None,
    coordinator_key: str | None = None,
) -> dict[str, str | None]:
    """Return {name: 8-char hash of the expanded instruction}.

    *agent_names* are topology-neutral domain agents. When *topology* and
    *coordinator_key* are given, the coordinator's expanded prompt is hashed too and
    recorded under "coordinator:<topology>/<key>" so the manifest shows which
    orchestration prompt actually ran.
    """
    versions: dict[str, str | None] = {}
    for name in agent_names:
        try:
            versions[name] = _hash(get_instruction(name))
        except FileNotFoundError:
            versions[name] = None

    if topology and coordinator_key:
        label = f"coordinator:{topology}/{coordinator_key}"
        try:
            versions[label] = _hash(get_instruction(coordinator_key, topology=topology))
        except FileNotFoundError:
            versions[label] = None

    return versions
