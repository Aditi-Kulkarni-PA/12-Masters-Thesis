"""Capability descriptions — READ from the prompt tree, never generated into it.

Direction of truth
------------------
`config/prompts/shared/capability_details.md` is the single hand-authored source. This
module parses it and hands the same strings to the function-calling schema, so the two
places a model can learn what a capability does — its prompt and its tool schema — are
physically the same text.

An earlier version had this backwards: the strings lived in a Python dict and a
`write_partials()` generator wrote them out as four `.md` files. That kept them in step,
but it meant the prompt tree contained machine-written content that could not be edited
where it was read, and it contradicted `load_config.py`'s WYSIWYG rule — the .md file IS
the instruction. Editing a prompt should mean editing a prompt.

    capability_details.md  --parsed here-->  CAPABILITY_DESCRIPTIONS
                                                  |
                    @capability_details            +--> TOOL_DESCRIPTIONS
                    (four tagline files)                (tool_description= in the schema)

Why the descriptions are shared at all
--------------------------------------
A coordinator sees only the description attached to each capability. Before this file
existed, those were one-line labels ("Emails to be sent to customers") that said nothing
about what the capability consumes, while the domain agent prompts DID state it. Monolith
inlines the domain prompts, and so does each Swarm specialist (both variants: each one's
instructions are built from get_instruction() for its own capability, the same domain
prompt file every other coordinator's specialist reads) -- so both knew the data
relationships; the other coordinators did not. Two conditions were reasoning with
information the others lacked, on precisely the axis under measurement.

The line these descriptions must not cross
------------------------------------------
They state INTERFACE FACTS — what each capability operates on. They must never state
ORCHESTRATION POLICY — what order to call things in, whether calls may run in parallel.

  ALLOWED   "Works from today's predicted delay output."
            A fact about the data. The coordinator must still infer that prediction has
            to happen first — that inference is the measured behaviour.

  FORBIDDEN "Call predict_delivery_delays_tool before this one."
            Hands over the orchestration answer. Risk Log R13.

`assert_no_ordering_language()` runs at import, so an edit to the .md that smuggles
ordering back in fails immediately rather than silently confounding a run.
"""

import re
from pathlib import Path

_SHARED_DIR = Path(__file__).resolve().parent.parent / "config" / "prompts" / "shared"
DETAILS_PATH = _SHARED_DIR / "capability_details.md"

# Matches:  - **predict** -- Run the two-stage ML pipeline ...
_BULLET = re.compile(r"^-\s+\*\*(?P<cap>[a-z_]+)\*\*\s+--\s+(?P<desc>.+?)\s*$", re.M)

# The capabilities the experiment is built around. Parsing must find exactly these — a
# typo'd or dropped bullet would otherwise silently give one condition a shorter roster.
EXPECTED_CAPABILITIES = ("predict", "diagnose", "simulate", "recommend", "email")


def parse_capability_details(path: Path = DETAILS_PATH) -> dict[str, str]:
    """Parse the shared details file into {capability: description}."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing — it is the source of every capability description.")
    text = path.read_text(encoding="utf-8")
    found = {m.group("cap"): m.group("desc") for m in _BULLET.finditer(text)}
    missing = [c for c in EXPECTED_CAPABILITIES if c not in found]
    extra = [c for c in found if c not in EXPECTED_CAPABILITIES]
    if missing or extra:
        raise ValueError(
            f"{path.name} must define exactly {list(EXPECTED_CAPABILITIES)}; "
            f"missing={missing} unexpected={extra}. A roster that differs between "
            f"conditions confounds the comparison.")
    return {c: found[c] for c in EXPECTED_CAPABILITIES}


CAPABILITY_DESCRIPTIONS: dict[str, str] = parse_capability_details()


# Which callable name each condition exposes for a capability. These are wiring facts —
# they must match what the function-calling schema actually registers — so they stay in
# code. The DESCRIPTIONS do not; those come from the .md above.
TOOL_NAME_BY_CAPABILITY: dict[str, str] = {
    "predict": "predict_delivery_delays_tool",
    "diagnose": "diagnose_delay_patterns_tool",
    "simulate": "delay_simulations_tool",
    "recommend": "recommendation_tool",
    "email": "email_alert_tool",
}

RAW_TOOL_NAME_BY_CAPABILITY: dict[str, str] = {
    "predict": "predict_delivery_delays",
    "diagnose": "get_delay_diagnosis",
    "simulate": "simulate_order_delays",
    "recommend": "recommend_actions",
    "email": "fetch_delayed_orders_for_email",
}

# What Planner-Executor passes as tool_description= when wrapping each sub-agent.
TOOL_DESCRIPTIONS: dict[str, str] = {
    TOOL_NAME_BY_CAPABILITY[cap]: desc for cap, desc in CAPABILITY_DESCRIPTIONS.items()
}


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
_FORBIDDEN_ORDERING_PHRASES = (
    "before", "after", "first", "then", "prior to", "must call", "call predict",
    "sequential", "in order", "one at a time",
)


def assert_no_ordering_language() -> None:
    """Fail loudly if any description states orchestration policy rather than a fact."""
    offenders = []
    for name, text in CAPABILITY_DESCRIPTIONS.items():
        lowered = text.lower()
        for phrase in _FORBIDDEN_ORDERING_PHRASES:
            if phrase in lowered:
                offenders.append(f"{name}: contains ordering phrase {phrase!r}")
    if offenders:
        raise AssertionError(
            "Capability descriptions must state what a capability consumes, never what "
            "order to call things in (Risk Log R13):\n  " + "\n  ".join(offenders))


assert_no_ordering_language()


# Variant files: a tagline (plus, where the condition has callable names, a short
# mapping) followed by `@capability_details`. Hand-authored — listed here only so the
# prompt gate can verify each one still pulls from the shared body.
CAPABILITY_VARIANTS: dict[str, tuple[str, dict | None]] = {
    # filename                       tagline                                  name map
    "tool_capabilities.md":        ("You have these specialist tools:",       TOOL_NAME_BY_CAPABILITY),
    "raw_tool_capabilities.md":    ("You have these tools:",                  RAW_TOOL_NAME_BY_CAPABILITY),
    # Addressed by role, so the label IS the capability name — no mapping needed.
    "participant_capabilities.md": ("Available participants:",                None),
    # Mesh: same capabilities as peers. Own tagline because "participants" implies a
    # roster someone dispatches from, and Mesh has no dispatcher.
    "peer_capabilities.md":        ("You can hand work directly to any of these specialists:", None),
    # Mesh B (concurrent peer-to-peer). Peers are ADDRESSED here, not handoff targets, so
    # unlike peer_capabilities.md this variant carries the callable names -- an agent that
    # cannot name the peer cannot address it. Separate tagline because the two Mesh variants
    # differ on exactly this point: handing work OVER (sole ownership moves, the sender
    # stops) versus naming a peer and continuing to run. Tagline corrected 30-Aug-26 -- the
    # earlier "return their result to you" text described the discarded agent-as-tools
    # design; the built condition's peers never reply to whoever addressed them. Currently
    # rendered but not included by any live prompt: topologies/mesh.py::_build_router()
    # builds its own per-node, self-excluding version of this same list in Python (peers
    # differ per node under a complete graph only by which one is "self"), rather than the
    # unfiltered all-5 render this variant produces.
    "peer_tool_capabilities.md":   ("You can address any of these specialists directly. Once addressed, "
                                    "each works on its own account and does not report back:", TOOL_NAME_BY_CAPABILITY),
}

VARIANT_FILES: tuple[str, ...] = tuple(CAPABILITY_VARIANTS)


def render_flat(filename: str) -> str:
    """FLAT capability list: `- <callable name> -- <description>`, one line each.

    This is the shape Planner-Executor's three clean runs used, and restoring it is the
    point. A split layout put callable names in one block and descriptions in another
    keyed by role, so `get_delay_diagnosis` and "Works from today's predicted delay
    output" were no longer on the same line. A model deciding whether it may call
    diagnose yet had to join two blocks to find the dependency — and monolith fired
    predict and diagnose together under that layout. Adjacency is where the dependency
    signal lives, not decoration.

    Rendered at LOAD time from shared/capability_details.md, so the description text
    still exists in exactly one place on disk.
    """
    tagline, names = CAPABILITY_VARIANTS[filename]
    labels = names if names is not None else {c: c for c in CAPABILITY_DESCRIPTIONS}
    lines = [tagline, ""]
    for cap, label in labels.items():
        lines.append(f"- {label} -- {CAPABILITY_DESCRIPTIONS[cap]}")
    return "\n".join(lines) + "\n"


def check_prompt_schema_agreement(shared_dir: Path = _SHARED_DIR) -> list[str]:
    """Report any way the RENDERED prompt and the function-calling schema disagree.

    Checks the rendered output, not the files: a variant file is now a single
    `@@capabilities:<name>` directive expanded at load time, so there is nothing to
    inspect on disk. What matters is what the model ends up reading.
    """
    problems: list[str] = []

    if not (shared_dir / DETAILS_PATH.name).exists():
        problems.append(f"{DETAILS_PATH.name} is missing — it is the source of every description")

    for name in CAPABILITY_VARIANTS:
        path = shared_dir / name
        if not path.exists():
            problems.append(f"{name} is missing")
            continue
        if "@@capabilities:" not in path.read_text(encoding="utf-8"):
            problems.append(
                f"{name} no longer renders from capability_details.md — its descriptions "
                f"would come from somewhere else")

    # Names in the rendered list must be the ones the wiring registers, and each must sit
    # on the SAME line as its description (adjacency is where the dependency signal is).
    for name, expected in (("tool_capabilities.md", set(TOOL_DESCRIPTIONS)),
                           ("raw_tool_capabilities.md", set(RAW_TOOL_NAME_BY_CAPABILITY.values()))):
        rendered = render_flat(name)
        listed = {l.split(" -- ")[0][2:] for l in rendered.splitlines()
                  if l.startswith("- ") and " -- " in l}
        if listed != expected:
            problems.append(
                f"{name} renders {sorted(listed)} but the wiring registers "
                f"{sorted(expected)} — the model would be told a name it cannot call")

    return problems
