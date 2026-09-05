"""
Shared utilities for the delivery chat app.

Contains: tool-call argument formatting, sidecar freshness detection,
diagnosis sidecar persistence, directory-scanning helpers,
action-plan building (intent detection → dependency-aware step list),
and confirmation / pending-query resolution for the chat handler.
"""

import json
import os
import re
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

_APP_DIR = Path(__file__).resolve().parent.parent  # helpers/ → app root

PREDICT_SIDECAR = _APP_DIR / "output" / "daily_delivery_delay_prediction_meta.json"
DIAG_SIDECAR = _APP_DIR / "output" / "diagnosis_meta.json"
FRESHNESS_TTL = 3600  # 1 hour
DISPLAY_ROWS: int = int(os.getenv("SC_MCP_DISPLAY_ROWS", 50))


# ---------------------------------------------------------------------------
# Tool-call argument formatting (for progress/status log)
# ---------------------------------------------------------------------------

def brief_args(raw_args, *, max_val: int = 80, max_total: int = 120) -> str:
    """Return a short representation of tool-call arguments for the status log."""
    s = str(raw_args or "")
    try:
        # Attempt to parse as JSON to produce key=value format
        parsed = json.loads(s)
        parts = []
        for k, v in parsed.items():
            v_str = str(v)
            # Truncate individual values that are too long (e.g. file paths, large strings)
            if len(v_str) > max_val:
                v_str = v_str[: max_val - 3] + "..."
            parts.append(f"{k}={v_str}")
        result = "(" + ", ".join(parts) + ")"
    except Exception:
        # If parsing fails, just use the raw string representation
        result = s
    # Enforce overall length limit to prevent status log overflow
    if len(result) > max_total:
        result = result[: max_total - 3] + "..."
    return result


# ---------------------------------------------------------------------------
# Sidecar freshness helpers
# ---------------------------------------------------------------------------

def is_file_fresh(path: Path, ttl: int = FRESHNESS_TTL) -> bool:
    """Return True if *path* exists and was modified less than *ttl* seconds ago."""
    return path.is_file() and (time.time() - path.stat().st_mtime) < ttl


def build_freshness_system_msg() -> str:
    """Return a SYSTEM tag describing prediction/diagnosis freshness."""
    # Check modification times of both sidecar files against the TTL threshold
    p_fresh = is_file_fresh(PREDICT_SIDECAR)
    d_fresh = is_file_fresh(DIAG_SIDECAR)
    
    # Best case: both outputs are fresh → all downstream tools can skip re-running base analysis
    if p_fresh and d_fresh:
        return (
            "\n\n[SYSTEM: Prediction AND Diagnosis pipeline outputs are FRESH (generated <1h ago). "
            "All tools can proceed WITHOUT re-running predict_delivery_delays_tool or diagnose_delay_patterns_tool.]"
        )
    # Partial freshness: predict is fresh but diagnosis is stale → only diagnosis needs to run
    if p_fresh:
        return (
            "\n\n[SYSTEM: Prediction pipeline output is FRESH (generated <1h ago). "
            "Diagnosis output is NOT FRESH. "
            "Run diagnose_delay_patterns_tool before recommendation_tool. "
            "Predict does NOT need to be re-run.]"
        )
    # Worst case: prediction is stale → must run predict first (it's a prerequisite for all others)
    return (
        "\n\n[SYSTEM: Prediction pipeline output is NOT FRESH. "
        "Run predict_delivery_delays_tool first as a pre-requisite before calling "
        "diagnose_delay_patterns_tool or any tool that depends on prediction data.]"
    )


def save_diagnosis_sidecar(diagnosis_text: str) -> None:
    """Write diagnosis sidecar JSON so the next run can detect freshness."""
    DIAG_SIDECAR.parent.mkdir(parents=True, exist_ok=True)
    # Store only the first 500 chars to keep sidecar small (full diagnosis is in the tab)
    DIAG_SIDECAR.write_text(json.dumps({"diagnosis_summary": diagnosis_text[:500]}))


# ---------------------------------------------------------------------------
# Directory scanning helpers
# ---------------------------------------------------------------------------

def list_dir_files(directory: Path) -> list[str]:
    """Return resolved string paths for all files in *directory*, or [] if missing."""
    if directory.is_dir():
        return [str(p.resolve()) for p in directory.iterdir() if p.is_file()]
    return []


knowledge_files = list_dir_files(_APP_DIR / "knowledge")
input_files = list_dir_files(_APP_DIR / "input")


# ═══════════════════════════════════════════════════════════════════════════
# Action-plan builder — intent detection + dependency-aware step ordering
# ═══════════════════════════════════════════════════════════════════════════

# Canonical analysis steps. Dict order = execution/display order.
_STEP_LABELS: dict[str, str] = {
    "predict":   "Predict delivery delays",
    "diagnose":  "Diagnose delay patterns",
    "simulate":  "Simulate what-if scenarios",
    "recommend": "Generate optimization recommendations",
    "email":     "Generate customer email alerts",
}
_STEP_KEYS = list(_STEP_LABELS)

# Prerequisite steps for each tool. A prerequisite is skipped (marked
# "-- skip") when its sidecar output is still fresh; a step the user
# directly asked for always runs.
TOOL_DEPS: dict[str, list[str]] = {
    "predict":   [],
    "diagnose":  ["predict"],
    "simulate":  ["predict"],
    "recommend": ["predict", "diagnose"],
    "email":     ["predict"],
}

# Sidecar file used for the freshness check of each skippable step.
_STEP_SIDECARS = {"predict": PREDICT_SIDECAR, "diagnose": DIAG_SIDECAR}

# Scenario parameter phrases recognised in the user's query, used to enrich
# the simulate step label (e.g. "Simulate what-if scenarios (stormy weather,
# east region)"). Mirrors the valid values accepted by the simulation tool
# (see delay_simulation.md), including the same informal-wording synonyms.
# Order = display order: weather, region, delivery mode, vehicle.
_SIM_PARAM_PATTERNS: list[tuple[str, str]] = [
    (r"\bstorm\w*|\bsevere\b|\bextreme\b|\bcyclone\w*|\bthunder\w*", "stormy weather"),
    (r"\brain\w*|\bmonsoon\w*", "rainy weather"),
    (r"\bfog\w*|\bmist\w*", "foggy weather"),
    (r"\bheat\w*|\bhot\b", "hot weather"),
    (r"\bcold\b|\bwinter\b", "cold weather"),
    (r"\b(clear|normal|good)\s+weather\b", "clear weather"),
    (r"\bcentral\b", "central region"),
    (r"\beast\b", "east region"),
    (r"\bnorth\b", "north region"),
    (r"\bsouth\b", "south region"),
    (r"\bwest\b", "west region"),
    (r"\bexpress\b", "express mode"),
    (r"\bsame.?day\b", "same day mode"),
    (r"\btwo.?day\b", "two day mode"),
    (r"\bstandard\s+(mode|delivery)\b", "standard mode"),
    (r"\bev\s+bike\b", "ev bike"),
    (r"\bev\s+van\b", "ev van"),
    (r"\bbike\b", "bike"),
    (r"\bscooter\b", "scooter"),
    (r"\btruck\b", "truck"),
]


def _detect_sim_params(query: str) -> list[str]:
    """Return scenario parameter phrases found in *query*, in display order.

    'ev bike'/'ev van' are matched before plain 'bike' and suppress the
    duplicate (a query containing 'ev bike' also regex-matches r'\\bbike\\b').
    """
    q = query.lower()
    # Scan patterns in order (display order: weather → region → mode → vehicle)
    found = [label for pattern, label in _SIM_PARAM_PATTERNS if re.search(pattern, q)]
    
    # Deduplicate EV bike/bike collision: "ev bike" matches both the 'ev bike'
    # pattern AND the generic 'bike' pattern; keep only the more specific one.
    if "ev bike" in found and "bike" in found:
        found.remove("bike")
    return found

# Keyword patterns → tool key (matches TOOL_DEPS keys).
TOOL_KEYWORDS: list[tuple[str, str]] = [
    (r"predict|delay|late|sla|on.?time|delivery performance|meet.*target"
     r"|kpi|metric|how.*deliver|missed|overdue|backlog|behind schedule",
     "predict"),
    (r"diagnos|pattern|root cause|why delays|compare historical"
     r"|trend|breakdown|analysis|insight|driver|factor",
     "diagnose"),
    (r"simulat|what.if|weather change|scenario|hypothetical|if we",
     "simulate"),
    (r"recommend|optimiz|improve|reduce delays|action|suggestion"
     r"|fix|mitigat|resolve|what should",
     "recommend"),
    (r"email|alert|notify customers|customer communication|customer alert"
     r"|send.*message|inform customer|outreach",
     "email"),
]

# Composite intents: certain phrases imply multiple tool keys together
_COMPOSITE_INTENTS: list[tuple[str, list[str]]] = [
    (r"sla|on.?time|adherence|meet.*target|kpi|delivery performance|performance review",
     ["predict", "diagnose", "recommend"]),
]

# Conversational messages that should NOT trigger plan/clarification
_GREETING_RE = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|bye|good morning|good evening"
    r"|how are you|what can you do|help|who are you)[\s\?\!\.]*$",
    re.IGNORECASE,
)

# Full-pipeline triggers
_FULL_PIPELINE_RE = re.compile(
    r"full pipeline|run all|full analysis|dashboard", re.IGNORECASE,
)


def _detect_tools(query: str) -> list[str]:
    """Return deduplicated tool keys matched from the query, in canonical order."""
    q = query.lower()
    matched: set[str] = set()
    
    # Match individual tool keywords (e.g. "predict" triggers predict tool)
    for pattern, key in TOOL_KEYWORDS:
        if re.search(pattern, q):
            matched.add(key)
    
    # Merge composite intents: phrases that imply multiple tools together
    # (e.g. "SLA report" implies predict + diagnose + recommend)
    for ci_pattern, ci_keys in _COMPOSITE_INTENTS:
        if re.search(ci_pattern, q):
            matched.update(ci_keys)
    
    # Return tools in canonical execution order (predict → diagnose → simulate → recommend → email)
    return sorted(matched, key=_STEP_KEYS.index)


def _merge_steps(tool_keys: list[str], query: str = "") -> list[str]:
    """Merge the requested tools + their prerequisites into an ordered plan.

    Directly requested steps always run. Prerequisite-only steps are marked
    "-- skip (data is fresh)" when their sidecar output is still fresh.
    The simulate step label is enriched with scenario parameters detected in
    *query* (weather / region / mode / vehicle).
    """
    # Build the complete set of steps needed: directly requested + their prerequisites
    requested = set(tool_keys)  # Steps the user explicitly asked for
    needed = set(requested)     # All steps including prerequisites
    for key in requested:
        # Add transitive prerequisites (e.g. recommend depends on [predict, diagnose])
        needed.update(TOOL_DEPS[key])

    plan: list[str] = []
    # Iterate in canonical execution order to ensure dependencies run first
    for key in _STEP_KEYS:
        if key not in needed:
            continue  # Skip steps not needed for this query
        
        # Start with the canonical label (e.g. "Predict delivery delays")
        label = _STEP_LABELS[key]
        
        # Enrich simulate label with detected parameters (e.g. "stormy weather, east region")
        if key == "simulate" and key in requested and query:
            params = _detect_sim_params(query)
            if params:
                label += f" ({', '.join(params)})"
        
        # Mark prerequisite-only steps as skippable if their output is fresh
        # (steps the user directly requested always run, even if fresh)
        sidecar = _STEP_SIDECARS.get(key)
        if key not in requested and sidecar is not None and is_file_fresh(sidecar):
            label += " -- skip (data is fresh)"
        
        plan.append(label)
    return plan


def build_action_plan(query: str) -> list[str] | None:
    """Build an ordered action plan from a user query.

    Returns:
        None       – conversational greeting; let the agent respond naturally
        []         – no tools recognised; caller should ask for clarification
        [steps…]   – ordered plan to confirm before executing
    """
    # Short-circuit for greetings/help messages → no plan needed, agent responds conversationally
    if _GREETING_RE.match(query.strip()):
        return None

    # Full pipeline trigger → return all 5 tools in dependency order
    if _FULL_PIPELINE_RE.search(query):
        return _merge_steps(list(TOOL_DEPS), query)

    # Normal case: detect which tools the user wants
    tool_keys = _detect_tools(query)
    
    # No tools matched → caller should ask for clarification
    if not tool_keys:
        return []

    # Build plan: merge requested tools + prerequisites, mark skippable steps
    return _merge_steps(tool_keys, query)


# ═══════════════════════════════════════════════════════════════════════════
# Confirmation / pending-query resolution
# ═══════════════════════════════════════════════════════════════════════════

CONFIRM_RE = re.compile(
    r"^(yes|y|go|go ahead|proceed|sure|ok|okay|confirm|do it|run it|let'?s go|yep|yup)[\.\!\s]*$",
    re.IGNORECASE,
)

# Informational questions are routed straight to the master agent, which
# answers conversationally (chat_response) or decides itself whether tools
# are needed — instead of being forced through the keyword plan builder.
_QUESTION_RE = re.compile(
    r"^(what|which|why|how|when|where|who|whose|is|are|was|were|do|does|did"
    r"|can|could|will|would|should|shall|tell me|show me|explain|describe"
    r"|summarize|compare)\b",
    re.IGNORECASE,
)


def _is_question(message: str) -> bool:
    """True if the message reads as a question rather than an action command."""
    msg = message.strip()
    return bool(_QUESTION_RE.match(msg)) or msg.endswith("?")


# Standard clarification prompt shown when no tools are matched
CLARIFICATION_MSG = ("I'm not sure which analysis to run for that. "
                     "Could you tell me more? For example:\n"
                     "- **Predict delays** – check which orders may be late\n"
                     "- **Diagnose patterns** – find root causes for delays\n"
                     "- **Simulate scenarios** – test what-if changes\n"
                     "- **Recommend actions** – get optimisation suggestions\n"
                     "- **Email alerts** – draft customer notifications\n\n"
                     "What would you like me to do?")


def format_plan_text(plan: list[str]) -> str:
    """Format an action-plan step list into a confirmation prompt."""
    lines = ["Here's my plan:"]
    for i, step in enumerate(plan, 1):
        lines.append(f"{i}. {step}")
    lines.append("\nShall I proceed?")
    return "\n".join(lines)


def resolve_confirmation(
    message: str,
    history: list[dict],
    pending_query: str,
    is_quick_action: bool,
) -> tuple[str, str, str | None]:
    """Decide how to handle the current user message w.r.t. pending queries.

    Returns (action, resolved_message, assistant_reply):
        action:
            "run"           – execute resolved_message through the agent
            "run_confirmed" – like "run", but the user has ALREADY confirmed a
                              plan (typed yes / clicked a quick action); the
                              handler tells the agent NOT to re-confirm
            "clarify"       – show assistant_reply and stop
            "confirm"       – show assistant_reply (plan) and stash resolved_message
            "greet"         – let the agent respond naturally (no plan needed)
        resolved_message:
            the query to run (for "run"/"run_confirmed"), or the original message
        assistant_reply:
            text to show (for "clarify"/"confirm"), or None
    """
    # Check if the current message is a confirmation word (yes, ok, proceed, etc.)
    is_confirm = bool(CONFIRM_RE.match(message))

    # --- Recover lost pending query from chat history ---
    # Scenario: The agent itself asked "Shall I proceed?" in its chat_response
    # (not via the app's plan confirmation flow), so pending_query is empty.
    # Walk back through history to find the original user intent that triggered
    # the plan, skipping over intermediate "yes" messages to avoid infinite loops.
    if not pending_query and is_confirm and len(history) >= 2:
        # Check if the last assistant message was a plan confirmation prompt
        last_asst = history[-1]
        if (last_asst.get("role") == "assistant"
                and "Shall I proceed?" in last_asst.get("content", "")):
            # Walk backwards to find the user's original query (skip prior "yes" replies)
            for prev in reversed(history[:-1]):
                if (prev.get("role") == "user"
                        and not CONFIRM_RE.match(prev.get("content", "").strip())):
                    pending_query = prev["content"]
                    break

    # --- Resolve pending confirmation ---
    # User typed "yes" after seeing a plan → execute the stashed query
    if pending_query and is_confirm:
        return "run_confirmed", pending_query, None
    # Note: any other message while a plan was pending simply abandons that plan
    # (user changed their mind or started a new request)

    # --- Quick actions skip the plan step (the button click IS the confirmation) ---
    # Quick action buttons pre-populate the textbox with a known query; treat it
    # as pre-confirmed so we don't show the same plan the user just clicked.
    if is_quick_action:
        return "run_confirmed", message, None

    # --- Informational questions go straight to the agent (conversational) ---
    # Questions like "What is X?" or "Why did Y happen?" don't trigger the
    # action plan builder; let the agent answer naturally or decide if tools are needed.
    if _is_question(message):
        return "run", message, None

    # --- Build action plan for new queries ---
    plan = build_action_plan(message)

    # Greeting or help request → no plan needed, agent responds conversationally
    if plan is None:
        return "greet", message, None
    
    # No tools matched → ask user to clarify their intent
    if len(plan) == 0:
        return "clarify", message, CLARIFICATION_MSG
    
    # Normal case: show the plan and wait for confirmation
    return "confirm", message, format_plan_text(plan)
