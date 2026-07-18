
"""
Supply Chain Delivery – Conversational Chat UI (Gradio).

Key logic:
    - Implements a two-column Gradio UI: left (chat, controls), right (output tabs)
    - Handles all agent orchestration, plan confirmation, and output streaming
    - Action plan confirmation: shows plan, waits for user confirmation, then executes
    - Supports quick-action buttons, file upload, and multi-tool queries
    - All outputs (chat, tables, markdown, files) are updated in a single handler yield
    - Sidecar freshness helpers ensure pipeline outputs are reused if fresh
    - Welcome/help message shown on load, after clear, and after each analysis
"""

import json
import os
import re
import sys
import hashlib
import time
from pathlib import Path
from uuid import uuid4

# Ensure this directory is importable
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from dotenv import load_dotenv, find_dotenv
load_dotenv(dotenv_path=find_dotenv(), override=True)

import pandas as pd
import gradio as gr
from agents import Runner, trace

from delivery_agents import (
    supply_chain_delivery_master_agent,
    pipeline_mcp,
    RowEnrichment,
    SimulateDelays,
    DiagnosisHighRisk,
    DiagnosisComparison,
    RecommendedAction,
    EmailAlert,
    EmailsList,
)
from helpers.post_processing import (
    process_predict,
    process_simulate,
    process_diagnosis,
    process_recommendations,
    process_emails,
)
from helpers.logging_utils import setup_run_logger, extract_usage_from_event
from helpers.app_utils import (
    brief_args,
    build_freshness_system_msg,
    save_diagnosis_sidecar,
    resolve_confirmation,
    PREDICT_SIDECAR,
    DIAG_SIDECAR,
    DISPLAY_ROWS,
    knowledge_files as _knowledge_files,
    input_files as _input_files,
)

_APP_LOGGER, _LOG_PATH = setup_run_logger(_APP_DIR)
_APP_LOGGER.info("app.startup log_path=%s", _LOG_PATH)

# ---------------------------------------------------------------------------
# In-process response cache
# Key: normalized message | orders file hash | sidecar mtimes | model name.
# Invalidates automatically when input data or sidecars change.
# ---------------------------------------------------------------------------
_response_cache: dict[str, dict] = {}
_RESPONSE_CACHE_MAX_SIZE = 50


def _response_cache_key(message: str, orders_path, predict_sidecar: Path, diag_sidecar: Path) -> str:
    """
    Build a stable cache key for a chat request.
    Generate a deterministic cache key for a chat request based on the 
    user message, input dataset contents hash, sidecar metadata timestamp, and the
    configured LLM model. The key automatically changes whenever any of these
    inputs change, ensuring cached responses remain valid.
    """
    model = os.getenv("OPENAI_MODEL", "")
    orders_hash = "none"
    if orders_path:
        _op = Path(orders_path)
        if _op.is_file():
            orders_hash = hashlib.sha256(_op.read_bytes()).hexdigest()[:16]
    predict_mtime = str(int(predict_sidecar.stat().st_mtime)) if predict_sidecar.is_file() else "0"
    diag_mtime = str(int(diag_sidecar.stat().st_mtime)) if diag_sidecar.is_file() else "0"
    raw = f"{message.lower().strip()}|{orders_hash}|{predict_mtime}|{diag_mtime}|{model}"
    return hashlib.sha256(raw.encode()).hexdigest()

def _tool_json(payload: str) -> dict:
    """Parse a sub-agent's raw tool output into a dict ({} on failure).

    The master agent does not copy sub-agent results into MasterOutput
    (re-emitting hundreds of rows added 15-30s of generation per run);
    instead the app captures each sub-agent's JSON directly from the
    tool-output stream events and parses it here.
    """
    if not payload:
        return {}
    try:
        data = json.loads(payload)
        return data if isinstance(data, dict) else {}
    except Exception:
        # Payload may not start with `{` (e.g. wrapped in markdown/plain text);
        # attempt to extract the first JSON object via regex.
        match = re.search(r"\{.*\}", payload, re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _validated_rows(data: dict, key: str, model) -> list:
    """Validate data[key] items against *model*; invalid rows are skipped."""
    rows: list = []
    for r in data.get(key) or []:
        try:
            rows.append(model(**r))
        except Exception:
            continue
    return rows


# ---------------------------------------------------------------------------
# Conversation Agent UI - Quick Menu Actions and Welcome Message
# ---------------------------------------------------------------------------

# Quick-action presets (label, query text)
_QUICK_ACTIONS = [
    ("Predict Delays", "Predict which orders are getting delayed today"),
    ("Diagnose Patterns", "Provide delay patterns and root cause diagnosis"),
    ("Simulate What-If", "Simulate delays for stormy weather in East region"),
    ("Recommend Actions", "Recommend ways to optimize delivery timelines"),
    ("Email Alerts", "Generate customer email alerts for delayed orders"),
    ("Run Full Analysis",
     "1. Predict Orders Getting Delayed\n"
     "2. Provide Delay Patterns & Root Cause Diagnosis\n"
     "3. Simulate Delays for Stormy Weather in East Region\n"
     "4. Recommend Ways to Optimize Delivery Timelines\n"
     "5. Generate Customer Email Alerts for Delayed Orders"),
]

_QUICK_ACTION_TEXTS = {text for _, text in _QUICK_ACTIONS}

# Placeholder shown in a tab while its analysis is still running
_PENDING = "<div style='color:#888;font-size:12px;padding:8px 0'>&#8987; Running analysis &mdash; results will appear here when ready...</div>"

# Welcome message to be displayed by the chatbot
_WELCOME_MSG = ("I can help with:\n"
               "- **Predicting** which orders will be delayed\n"
               "- **Diagnosing** delay patterns and root causes\n"
               "- **Simulating** what-if scenarios\n"
               "- **Recommending** actions to reduce delays\n"
               "- **Generating** customer email alerts\n\n"
               "What would you like to do?")


# ---------------------------------------------------------------------------
# Main chat handler: manages plan confirmation, agent execution, and output streaming
# ---------------------------------------------------------------------------

async def chat_handler(message: str, history: list, orders_path, pending_query: str, tab_state: dict):
    """
    Manual chatbot handler with action-plan confirmation.

    Args:
        message:       user text from the textbox
        history:       list of {"role": ..., "content": ...} dicts (chatbot state)
        orders_path:   file path(s) from the File widget
        pending_query: stashed query awaiting confirmation (from gr.State)
        tab_state:     dict preserving previous tab outputs across runs

    Yields 16-tuple: (history, textbox, pending_state,
                      predict_md, predict_df, predict_csv,
                      simulate_md, simulate_df, simulate_csv,
                      diagnosis_md, diag_hr_df, diag_comp_df,
                      recommend_md, email_md, email_csv,
                      tab_state)
    """
    if not tab_state:
        tab_state = {}

    # generate unique ID for each request - used for logging and tracing
    request_id = uuid4().hex[:12]

    # initialize start times (perf counter for durations, wall clock for
    # detecting files written during this run)
    run_start = time.perf_counter()
    run_start_ts = time.time()

    # track pending tool calls; each tuple contains (tool_name, start_time).
    # Note on logging: this stream only carries the MASTER's tool calls (the
    # five sub-agent wrappers). Inner MCP calls and @function_tools run inside
    # the sub-agents and self-log start/completion to the same log file from
    # their own code (see tools/*.py and prediction_server.py).
    pending_tool_calls: list[tuple[str, float]] = []
    tools_completed = 0
    applied_tools: set[str] = set()  # tools whose payload was already turned into tab output

    # Operator-visible soft errors (unparseable payloads, dropped rows, …).
    # Collected during the run and surfaced in the chat reply — otherwise a
    # degraded run looks successful and only the log file knows.
    run_warnings: list[str] = []

    # Raw JSON of every sub-agent tool output, captured from the stream keyed
    # by tool name (the master does not copy results; the app parses them)
    tool_payloads: dict[str, str] = {}

    # Heartbeat state: shows "master composing output..." while the master
    # generates between/after tool calls instead of a silent 15-30s gap
    compose_started: float | None = None
    compose_line_idx: int | None = None
    last_heartbeat = 0.0

    # token usage counters for the entire run (across multiple tools)
    run_prompt_tokens = 0
    run_completion_tokens = 0
    run_total_tokens = 0
    usage_events = 0

    # Keep track of which tab outputs were produced in this run, for caching
    _produced_keys: set[str] = set()

    def _tabs(show_pending: bool = False):
        """Build the 12-element tab tuple from preserved state.

        With show_pending=True, markdown slots without real output show an
        hourglass placeholder (used while an analysis run is in progress);
        slots that already hold real results are preserved.
        """
        def _md(key: str):
            # tab_state only ever holds real output (_apply_payload gates
            # every persist with _is_real)
            val = tab_state.get(key, "")
            # If we have content OR we're not showing pending placeholders, return the value
            # Otherwise return the hourglass placeholder to indicate work in progress
            if val or not show_pending:
                return val
            return _PENDING

        return (
            _md("predict_md"),
            tab_state.get("predict_df", pd.DataFrame()),
            tab_state.get("predict_csv"),
            _md("simulate_md"),
            tab_state.get("simulate_df", pd.DataFrame()),
            tab_state.get("simulate_csv"),
            _md("diagnosis_md"),
            tab_state.get("diag_hr_df", pd.DataFrame()),
            tab_state.get("diag_comp_df", pd.DataFrame()),
            _md("recommend_md"),
            _md("email_md"),
            tab_state.get("email_csv"),
        )

    # default return value. The '*' operator unpacks the 12-element tab tuple into the 16-element yield tuple
    _EMPTY = (history, "",
              "",  # clear pending
              *_tabs(),
              tab_state)

    # steps:
    # 1. Validate and normalize the input
    # 2. Handle confirmation/clarification workflows
    # 3. Check the response cache
    # 4. Build the final query
    # 5. Initialize outputs
    # 6. Update the UI to show that processing has started

    # --- Validate and normalize file input ---

    # Gradio returns uploaded files as a list even when only one file is allowed
    if isinstance(orders_path, list):
        orders_path = orders_path[0] if orders_path else None

    # Remove whitespace from the message and ignore empty messages
    message = (message or "").strip()
    if not message:
        _APP_LOGGER.info("request.empty request_id=%s", request_id)
        yield _EMPTY
        return

    # log the incoming request details
    _APP_LOGGER.info(
        "request.received request_id=%s pending=%s has_orders_file=%s message_len=%s",
        request_id,
        bool(pending_query),
        bool(orders_path),
        len(message),
    )

    # Append user message to history for context in the next step.
    # This makes it available for resolve_confirmation to analyze the full conversation flow
    history = history + [{"role": "user", "content": message}]

    # --- Resolve confirmation / plan in one step (logic in app_utils) ---
    is_quick_action = message in _QUICK_ACTION_TEXTS
    # history without current user msg (resolve_confirmation looks at prior turns to detect
    # if user is responding to a confirmation prompt vs. making a new request)
    prior_history = history[:-1]

    # Resolve whether the message requires clarification, confirmation, or can proceed directly to agent execution.
    # Returns:
    #   action: "clarify" (need more info), "confirm" (show plan, await yes/no), or "run_confirmed" (execute now)
    #   message: normalized/rewritten query
    #   reply: assistant response to show user (if clarify/confirm)
    action, message, reply = resolve_confirmation(
        message, prior_history, pending_query, is_quick_action,
    )
    _APP_LOGGER.info(
        "request.confirmation request_id=%s action=%s is_quick_action=%s",
        request_id,
        action,
        is_quick_action,
    )

    if action == "clarify":
        # User's request is too vague; ask for more details before proceeding
        _APP_LOGGER.info("request.clarify request_id=%s", request_id)
        history = history + [{"role": "assistant", "content": reply}]
        yield (history, "", "", *_tabs(), tab_state)
        return

    if action == "confirm":
        # Show action plan and wait for user confirmation ("yes" or similar)
        # Stash the query in pending_state so the next turn knows this is a confirmation flow
        _APP_LOGGER.info("request.awaiting_confirmation request_id=%s", request_id)
        history = history + [{"role": "assistant", "content": reply}]
        yield (history, "", message, *_tabs(), tab_state)  # stash query as pending
        return

    # --- Proceed with running the agent ---

    # Check response cache before doing any work. Do not rerun AI if cache is valid.
    # Cache key includes: message, orders file hash, sidecar mtimes, model name.
    # If any of these change, the cache key changes and we run a fresh analysis.
    _cache_key = _response_cache_key(message, orders_path, PREDICT_SIDECAR, DIAG_SIDECAR)
    if _cache_key in _response_cache:
        # Cache hit: restore all tab outputs from the cached entry and skip the agent run
        cached = _response_cache[_cache_key]
        _APP_LOGGER.info("run.cache_hit request_id=%s", request_id)
        # Deep copy DataFrames to prevent mutations; other values are immutable strings/paths
        tab_state.update(
            {k: (v.copy() if isinstance(v, pd.DataFrame) else v) for k, v in cached.items()}
        )
        yield (
            history + [{"role": "assistant", "content": "*(Loaded from cache — data unchanged.)*\n\n" + _WELCOME_MSG}],
            "", "",
            *_tabs(),
            tab_state,
        )
        return

    # --- Build final query ---
    # Construct the full prompt sent to the agent:
    #   1. User's message (e.g. "predict delays")
    #   2. Orders file path (if uploaded), so sub-agents can load it
    #   3. Freshness metadata (sidecar timestamps) to enable predict/diagnose output reuse
    full_query = message
    if orders_path:
        full_query += f"\n\nThe input orders data is in the file at path: {orders_path}"

    # Append freshness metadata so agents know if they can reuse prior predictions/diagnosis
    full_query += build_freshness_system_msg()

    # The plan was already shown and confirmed in the chat UI (typed "yes" or
    # clicked a quick action) — the agent must execute, not re-confirm.
    # Without this, some LLMs will re-present the plan instead of executing it,
    # creating an unnecessary extra confirmation loop.
    if action == "run_confirmed":
        full_query += (
            "\n\n[SYSTEM: PLAN CONFIRMED — the user already reviewed and confirmed "
            "the action plan for this request in the chat UI. Do NOT present a plan "
            "or ask 'Shall I proceed?' again. Execute the required tools now, one at "
            "a time, in dependency order.]"
        )
    _APP_LOGGER.info(
        "run.started request_id=%s query_len=%s has_orders_file=%s",
        request_id,
        len(full_query),
        bool(orders_path),
    )

    # --- Initialise outputs ---
    predict_text = simulate_text = diagnosis_text = recommend_text = email_alert_text = ""
    predict_df = simulate_df = pd.DataFrame()
    diagnosis_high_risk_df = diagnosis_comparison_df = pd.DataFrame()
    predict_csv_path = simulate_csv_path = email_csv_path = None

    def _is_real(text: str) -> bool:
        """True if *text* is actual output (not empty / 'not run' placeholder)."""
        return bool(text) and "not run" not in text.lower()

    def _apply_payload(tool_name: str, summary: str = "", reapply: bool = False) -> bool:
        """Turn a captured tool payload into tab output + persisted tab_state.

        Called twice per tool at most: in-stream right after the tool's output
        event (instant tab update, no master note yet) and again in the final
        pass with *reapply* for the tools whose display includes a
        master-written note (simulate/recommend/email narratives).
        Returns True if real output was produced.
        """
        # Declare all variables that will be modified in the outer scope
        nonlocal predict_text, predict_df, predict_csv_path
        nonlocal simulate_text, simulate_df, simulate_csv_path
        nonlocal diagnosis_text, diagnosis_high_risk_df, diagnosis_comparison_df
        nonlocal recommend_text, email_alert_text, email_csv_path

        # Skip if this tool was already processed (prevents double-processing)
        # unless reapply=True (used when master provides additional narrative)
        if tool_name in applied_tools and not reapply:
            return False
        
        # Retrieve the raw tool output captured during event streaming
        # tool_payload is populated during event streaming when each tool completes
        payload = tool_payloads.get(tool_name, "")
        # Attempt to parse as JSON; _tool_json returns {} on failure
        data = _tool_json(payload)
        
        # If we have a payload but parsing failed, log warning and notify operator
        if payload and not data:
            _APP_LOGGER.warning(
                "tool.payload.unparseable request_id=%s tool=%s payload_len=%s",
                request_id, tool_name, len(payload),
            )
            run_warnings.append(f"{tool_name}: output could not be parsed -- tab not updated")
        
        # Track whether this call produces any real output (not just empty placeholders)
        produced = False

        def _rows(key: str, model) -> list:
            """Validate rows and surface any drop as an operator warning.
            
            Extracts data[key], validates each row against the Pydantic model,
            and returns only valid rows. If any rows fail validation, logs a
            warning and adds it to run_warnings so the operator knows data was degraded.
            """
            raw = data.get(key) or []  # Raw list of dicts from tool output
            rows = _validated_rows(data, key, model)  # Validated Pydantic instances
            
            # If some rows were dropped due to validation errors, notify operator
            if len(rows) < len(raw):
                _APP_LOGGER.warning(
                    "tool.rows.dropped request_id=%s tool=%s key=%s dropped=%s total=%s",
                    request_id, tool_name, key, len(raw) - len(rows), len(raw),
                )
                run_warnings.append(
                    f"{tool_name}: {len(raw) - len(rows)} of {len(raw)} rows failed "
                    "validation and were dropped"
                )
            return rows

        # --- PREDICT TOOL HANDLER ---
        if tool_name == "predict_delivery_delays_tool":
            # Bail early if no JSON data was parsed from the payload
            if not data:
                return False
            
            # Extract and validate the delayed_orders array from the payload
            rows = _rows("delayed_orders", RowEnrichment)
            _APP_LOGGER.info("predict.rows request_id=%s source=tool_stream count=%s",
                             request_id, len(rows))
            
            # Format prediction rows into markdown summary + DataFrame + CSV export path
            # process_predict handles all formatting, truncation (DISPLAY_ROWS), and CSV writing
            predict_text, predict_df, predict_csv_path = process_predict(
                data.get("predict_summary", "") or "", rows, _APP_DIR, DISPLAY_ROWS)
            
            # Only persist if we got real output (not empty/placeholder)
            if _is_real(predict_text):
                # Update tab_state so these outputs persist across streaming yields
                # (tab_state is passed back in every yield and preserved between events)
                tab_state.update(predict_md=predict_text, predict_df=predict_df,
                                 predict_csv=predict_csv_path)
                # Track which keys were produced for cache storage at the end
                _produced_keys.update({"predict_md", "predict_df", "predict_csv"})
                produced = True

        # --- DIAGNOSE TOOL HANDLER ---
        elif tool_name == "diagnose_delay_patterns":
            if not data:
                return False
            
            # Extract diagnosis summary text (master-written narrative from the tool)
            diagnosis_text = data.get("diagnosis_summary", "") or ""
            
            # Process two tables: high-risk patterns (today) and historical comparison
            # process_diagnosis converts validated rows into displayable DataFrames
            diagnosis_high_risk_df, diagnosis_comparison_df = process_diagnosis(
                _rows("high_risk_patterns", DiagnosisHighRisk),
                _rows("comparison", DiagnosisComparison))
            
            # Write diagnosis text to a sidecar file so the predict agent
            # can reference it on subsequent runs via the freshness check
            # (enables predict agent to avoid re-running diagnosis if it's fresh)
            if diagnosis_text:
                save_diagnosis_sidecar(diagnosis_text)
            
            # Persist all three outputs if we got real diagnosis content
            if _is_real(diagnosis_text):
                tab_state.update(diagnosis_md=diagnosis_text,
                                 diag_hr_df=diagnosis_high_risk_df,
                                 diag_comp_df=diagnosis_comparison_df)
                _produced_keys.update({"diagnosis_md", "diag_hr_df", "diag_comp_df"})
                produced = True

        # --- SIMULATE TOOL HANDLER ---
        elif tool_name == "delay_simulations_tool":
            # Need either JSON data (from tool) or summary (from master) to proceed
            if not data and not summary:
                return False
            
            # `summary` is the master agent's narrative (arrives later in final pass with reapply=True)
            # `data` contains the structured simulation rows from the tool output
            # process_simulate merges both into a rich markdown display + table + CSV
            simulate_text, simulate_df, simulate_csv_path = process_simulate(
                summary, _rows("simulations", SimulateDelays),
                _APP_DIR, run_start_ts, DISPLAY_ROWS)
            
            if _is_real(simulate_text):
                tab_state.update(simulate_md=simulate_text, simulate_df=simulate_df,
                                 simulate_csv=simulate_csv_path)
                _produced_keys.update({"simulate_md", "simulate_df", "simulate_csv"})
                produced = True

        # --- RECOMMEND TOOL HANDLER ---
        elif tool_name == "recommendation_tool":
            # Need either JSON data (from tool) or summary (from master) to proceed
            if not data and not summary:
                return False
            
            # Merge master narrative (summary) with structured action rows (data) into markdown
            # process_recommendations formats the combined output for display
            recommend_text = process_recommendations(
                summary, _rows("recommended_actions", RecommendedAction))
            
            if _is_real(recommend_text):
                tab_state["recommend_md"] = recommend_text
                _produced_keys.add("recommend_md")
                produced = True

        # --- EMAIL TOOL HANDLER ---
        elif tool_name == "email_alert_tool":
            # Need either JSON data (from tool) or summary (from master) to proceed
            if not data and not summary:
                return False
            
            # Extract and validate email alert rows from the payload
            email_rows = _rows("content", EmailAlert)
            
            # Wrap validated rows in EmailsList for the post-processor; None if no valid rows
            # (process_emails needs the typed EmailsList wrapper for proper handling)
            email_alerts_obj = EmailsList(content=email_rows) if email_rows else None
            
            # Generate markdown display and CSV export of email templates
            email_alert_text, email_csv_path = process_emails(summary, email_alerts_obj, _APP_DIR)
            
            if _is_real(email_alert_text):
                tab_state.update(email_md=email_alert_text, email_csv=email_csv_path)
                _produced_keys.update({"email_md", "email_csv"})
                produced = True
        
        # Unknown tool name — no handler available
        else:
            return False

        # Mark this tool as applied so subsequent calls in the same run are no-ops
        # (unless reapply=True is passed, which allows re-processing with updated summary)
        applied_tools.add(tool_name)
        
        # Return True if real output was produced, False if payload was empty/invalid
        return produced

    # Status lines — plain text, no emojis (issue #6)
    status_lines = []
    if orders_path:
        status_lines.append(f"Input file: {Path(orders_path).name}")
    else:
        status_lines.append("Note: No input file selected -- using default pipeline data.")
    status_lines.append("Starting analysis...\n")

    # --- Update the UI to show that processing has started ---

    # Immediately show status in chat; show hourglass in empty tabs, preserve
    # filled ones (same _tabs helper, pending mode)
    _running_tabs = _tabs(show_pending=True)
    yield (
        history + [{"role": "assistant", "content": "\n".join(status_lines)}],
        "",
        "",  # clear pending
        *_running_tabs,
        tab_state,
    )

    # --- Run the agent in a try/except to catch errors and log them ---
    # execute the agent, stream progress updates to the UI, 
    # post-processing the final outputs, update the tabs, cache the results, and handle errors.
    try:
        # open the mcp session context for all tools in the pipeline
        async with pipeline_mcp: 

            # record every agent run as one trace span for observability
            # run streamed to allow incremental updates to the UI as the agent progresses
            with trace("Supply Chain Delivery Master"): 
                result = Runner.run_streamed(supply_chain_delivery_master_agent, full_query)

                # process every event as soon as it is received (streamed)
                async for event in result.stream_events():

                    if event.type == "raw_response_event":
                        # Handle raw_response_event for token usage logging
                        usage_payload = extract_usage_from_event(event)
                        if usage_payload:
                            pt = usage_payload.get("prompt_tokens")
                            ct = usage_payload.get("completion_tokens")
                            tt = usage_payload.get("total_tokens")
                            # Accumulate token counts across all LLM calls in this run
                            run_prompt_tokens += int(pt or 0)
                            run_completion_tokens += int(ct or 0)
                            run_total_tokens += int(tt or 0)
                            usage_events += 1
                            _APP_LOGGER.info(
                                "llm.usage request_id=%s model=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
                                request_id,
                                usage_payload.get("model", "unknown"),
                                pt,
                                ct,
                                tt,
                            )

                        # Heartbeat: the master is generating (choosing the next
                        # step or composing the final structured output). Surface
                        # it in the status log instead of leaving a silent gap.
                        # This only runs when at least one tool has completed and no tools are currently running.
                        if tools_completed > 0 and not pending_tool_calls:
                            _now = time.perf_counter()
                            if compose_started is None:
                                # First heartbeat: master just finished all tool calls and is composing final output
                                compose_started = _now
                                last_heartbeat = _now
                                status_lines.append("    -- master agent processing tool results / composing output...")
                                compose_line_idx = len(status_lines) - 1  # Remember which line to update
                                yield (
                                    history + [{"role": "assistant", "content": "\n".join(status_lines)}],
                                    "", "", *_running_tabs, tab_state,
                                )
                            elif _now - last_heartbeat >= 5 and compose_line_idx is not None:
                                # Update heartbeat every 5 seconds to show elapsed time
                                last_heartbeat = _now
                                status_lines[compose_line_idx] = (
                                    f"    -- master agent composing structured output... ({int(_now - compose_started)}s)"
                                )
                                yield (
                                    history + [{"role": "assistant", "content": "\n".join(status_lines)}],
                                    "", "", *_running_tabs, tab_state,
                                )
                        continue

                    elif event.type == "agent_updated_stream_event":
                        # Handle agent_updated_stream_event (agent switch) for logging and status updates
                        _APP_LOGGER.info(
                            "agent.updated request_id=%s agent=%s",
                            request_id,
                            event.new_agent.name,
                        )
                        status_lines.append(f"  Agent: {event.new_agent.name}")

                    elif event.type == "run_item_stream_event":
                        item = event.item
                        raw = getattr(item, "raw_item", None)

                        if item.type == "tool_call_item":
                            # log the tool name, start time, and brief arguments for status updates
                            tool_name = getattr(raw, "name", "")
                            args = getattr(raw, "arguments", None)
                            pending_tool_calls.append((tool_name, time.perf_counter()))
                            compose_started = None  # a new tool run interrupts the composing heartbeat
                            _APP_LOGGER.info(
                                "tool.call.started request_id=%s tool=%s args=%s",
                                request_id,
                                tool_name,
                                brief_args(args),
                            )
                            status_lines.append(f"    -- calling {tool_name}\n    {brief_args(args)}")

                        elif item.type == "tool_call_output_item":
                            # Tool call just completed; match it to the pending call to compute duration.
                            # Assumption: tools complete in FIFO order (same as they were called).
                            tool_name = "unknown"
                            duration_ms = None
                            if pending_tool_calls:
                                # Pop the first pending call (FIFO) and compute its duration
                                tool_name, started_at = pending_tool_calls.pop(0)
                                duration_ms = int((time.perf_counter() - started_at) * 1000)
                            _APP_LOGGER.info(
                                "tool.call.completed request_id=%s tool=%s duration_ms=%s",
                                request_id,
                                tool_name,
                                duration_ms,
                            )
                            tools_completed += 1
                            compose_started = None
                            status_lines.append("    -- output received")

                            # Capture the sub-agent's raw output so the app can
                            # parse rows/summaries itself (the master does not
                            # copy tool results into MasterOutput to avoid 15-30s of re-emission overhead)
                            _out = getattr(item, "output", None) or getattr(raw, "output", None)
                            if _out and tool_name != "unknown":
                                tool_payloads[tool_name] = str(_out)

                                # PROGRESSIVE TAB UPDATE: parse and display this tool's output immediately
                                # so the UI updates as each sub-agent finishes
                                if _apply_payload(tool_name):
                                    _running_tabs = _tabs(show_pending=True)
                                    status_lines.append(f"    -- {tool_name} results shown in tab")

                    # Update the UI while 
                    # preserving existing tab outputs if they have real content, 
                    # else show hourglass for tabs that have not yet produced output
                    assistant_text = "\n".join(status_lines)
                    yield (
                        history + [{"role": "assistant", "content": assistant_text}],
                        "",
                        "",  # clear pending
                        *_running_tabs,
                        tab_state,
                    )
                    # continue for loop until event streaming is complete

                # ---- Final output processing (deterministic, in post_processing.py) ----
                # predict/diagnose tabs were already filled in-stream as each
                # sub-agent finished. Here we read the master's slim output and
                # re-apply the tools whose display includes a master-written
                # note (simulate/recommend/email narratives).
                final_output = result.final_output
                chat_reply = ""
                mo = None  # MasterOutput object
                if isinstance(final_output, str):
                    # no structured output
                    chat_reply = final_output.strip()
                elif final_output is not None:
                    # Normal case: structured MasterOutput with optional chat_response field
                    mo = final_output
                    chat_reply = (getattr(mo, "chat_response", "") or "").strip()

                # Extract master-written narrative summaries from the final structured output
                sim_summary   = ((getattr(mo, "simulate_summary", "") or "") if mo else "").strip()
                rec_summary   = ((getattr(mo, "recommendation_summary", "") or "") if mo else "").strip()
                email_summary = ((getattr(mo, "email_alert_summary", "") or "") if mo else "").strip()

                # Apply tool payloads to tabs (final pass):
                #   - predict/diagnose: no-op if already applied in-stream (no reapply needed)
                #   - simulate/recommend/email: reapply with master's narrative if present
                #     (combines master's summary with tool's structured rows for richer display)
                _apply_payload("predict_delivery_delays_tool")   # no-op if applied in-stream
                _apply_payload("diagnose_delay_patterns")
                _apply_payload("delay_simulations_tool", summary=sim_summary, reapply=bool(sim_summary))
                _apply_payload("recommendation_tool", summary=rec_summary, reapply=bool(rec_summary))
                _apply_payload("email_alert_tool", summary=email_summary, reapply=bool(email_summary))

                _APP_LOGGER.info(
                    "run.outputs request_id=%s predict=%s diagnosis=%s simulate=%s recommend=%s email=%s",
                    request_id,
                    bool(predict_text),
                    bool(diagnosis_text),
                    bool(simulate_text),
                    bool(recommend_text),
                    bool(email_alert_text),
                )

                # Determines reply style: tab-summary message for analysis runs,
                # or the agent's direct chat answer for conversational queries
                ran_analysis = any(_is_real(t) for t in (
                    predict_text, diagnosis_text, simulate_text,
                    recommend_text, email_alert_text,
                ))

                # Final assistant message — plain text, no emojis (#6).
                # Conversational turn: show the agent's direct answer.
                # Analysis turn: show the tab summary + welcome prompt.
                if chat_reply and not ran_analysis:
                    status_lines.append("\n" + chat_reply)
                else:
                    summary_parts = ["\n---", "**Analysis complete.**\n"]
                    if _is_real(predict_text):
                        summary_parts.append("- Predictions       -->  Predict tab")
                    if _is_real(diagnosis_text):
                        summary_parts.append("- Diagnosis         -->  Diagnosis tab")
                    if _is_real(simulate_text):
                        summary_parts.append("- Simulation        -->  Simulation tab")
                    if _is_real(recommend_text):
                        summary_parts.append("- Recommendations   -->  Recommendation tab")
                    if _is_real(email_alert_text) and "no email" not in email_alert_text.lower():
                        summary_parts.append("- Email Alerts      -->  Email tab")

                    status_lines.extend(summary_parts)
                    # Append welcome prompt for next interaction
                    status_lines.append("\n" + _WELCOME_MSG)

                # Surface soft errors — without this a degraded run looks
                # successful and only the log file knows something went wrong
                if run_warnings:
                    status_lines.append("\n---\n**Warnings** -- some steps degraded:")
                    # Use dict.fromkeys() to deduplicate while preserving order (Python 3.7+ guarantee)
                    for w in dict.fromkeys(run_warnings):  # dedupe, keep order
                        status_lines.append(f"- {w}")
                    status_lines.append(f"Operator: details in log file `{_LOG_PATH.name}`")
                    _APP_LOGGER.warning(
                        "run.warnings request_id=%s count=%s", request_id, len(run_warnings),
                    )
                assistant_text = "\n".join(status_lines)

                # Log a short preview of each output for post-run debugging
                for _label, _txt in [
                    ("predict",   predict_text),
                    ("simulate",  simulate_text),
                    ("diagnosis", diagnosis_text),
                    ("recommend", recommend_text),
                    ("email",     email_alert_text),
                ]:
                    _APP_LOGGER.info(
                        "debug.agent_text request_id=%s agent=%s is_real=%s preview=%r",
                        request_id, _label, _is_real(_txt), _txt[:200],
                    )
                # (tab_state persistence happens inside _apply_payload)
                _APP_LOGGER.info(
                    "run.completed request_id=%s duration_ms=%s",
                    request_id,
                    int((time.perf_counter() - run_start) * 1000),
                )
                if usage_events > 0:
                    _APP_LOGGER.info(
                        "run.usage.summary request_id=%s usage_events=%s prompt_tokens_total=%s completion_tokens_total=%s total_tokens_total=%s",
                        request_id,
                        usage_events,
                        run_prompt_tokens,
                        run_completion_tokens,
                        run_total_tokens,
                    )
                else:
                    _APP_LOGGER.info(
                        "run.usage.summary request_id=%s usage_events=0 usage_source=unavailable",
                        request_id,
                    )

                # Store successful run outputs to response cache.
                # Only cache outputs that were actually produced in this run (_produced_keys).
                if _produced_keys:
                    _cached_entry: dict = {}
                    for _k in _produced_keys:
                        _v = tab_state.get(_k)
                        # Deep copy DataFrames to prevent cache mutations; strings/paths are immutable
                        _cached_entry[_k] = _v.copy() if isinstance(_v, pd.DataFrame) else _v
                    # Simple eviction strategy: clear entire cache when it reaches max size
                    if len(_response_cache) >= _RESPONSE_CACHE_MAX_SIZE:
                        _response_cache.clear()
                        _APP_LOGGER.info("run.response_cache_evicted request_id=%s", request_id)
                    _response_cache[_cache_key] = _cached_entry
                    _APP_LOGGER.info(
                        "run.response_cache_stored request_id=%s keys=%s",
                        request_id,
                        sorted(_produced_keys),
                    )

                yield (
                    history + [{"role": "assistant", "content": assistant_text}],
                    "",
                    "",  # clear pending
                    *_tabs(),
                    tab_state,
                )

    except Exception as e:
        _APP_LOGGER.exception(
            "run.failed request_id=%s duration_ms=%s error=%s",
            request_id,
            int((time.perf_counter() - run_start) * 1000),
            str(e),
        )
        error_msg = (
            f"Error: {str(e)}\n"
            f"(Operator: full traceback in log file `{_LOG_PATH.name}`)"
        )
        status_lines.append(error_msg)
        assistant_text = "\n".join(status_lines)
        yield (
            history + [{"role": "assistant", "content": assistant_text}],
            "",
            "",  # clear pending
            *_tabs(),
            tab_state,
        )


# ---------------------------------------------------------------------------
# Gradio UI: chatbot, textbox, quick actions, file upload, and output tabs
# ---------------------------------------------------------------------------

custom_css = """
    .content-textbox { font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important; font-size: 14px !important; line-height: 1.5 !important; }
    .content-textbox textarea { overflow-y: auto !important; min-height: 6em; max-height: 320px; }
    .input-textbox textarea { font-size: 13px !important; }
    .file-upload, .file-download { border: 1px solid #e0e0e0; border-radius: 4px; padding: 4px 8px; max-height: 70px; overflow: visible; font-size: 14px !important; font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important; }
    .file-upload *, .file-download * { font-size: inherit !important; }
    .table-container { min-height: 160px; }
    .output-table { font-size: 14px !important; font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important; }
    .output-table table, .output-table th, .output-table td { font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important; font-size: 12px !important; line-height: 1.5 !important; }
    .summary-markdown { border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px 16px; font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important; font-size: 14px !important; line-height: 1.6 !important; background: #fafafa; }
    .summary-markdown h3 { margin-top: 0.8em; margin-bottom: 0.4em; }
    .summary-markdown p { margin: 0.4em 0; }
    .summary-markdown ul { margin: 0.3em 0 0.3em 1.2em; padding: 0; }
    .summary-markdown li { margin: 0.15em 0; }
    .summary-label p { font-size: 13px !important; font-weight: 600 !important; color: #374151 !important; margin: 0 0 4px 0 !important; }
    .quick-btn { font-size: 11px !important; padding: 5px 8px !important; }
    .page-title h1 { font-size: 20px !important; font-weight: 700 !important; margin: 2px 0 0 0 !important; line-height: 1.2 !important; }
    .page-title { overflow: hidden !important; }
    .chatbot-box { height: 370px !important; max-height: 370px !important; overflow: hidden !important; }
    .chatbot-box > div { overflow-y: auto !important; }
    .chatbot-box .message-wrap, .chatbot-box .bot, .chatbot-box .user,
    .chatbot-box .message, .chatbot-box .markdown { font-size: 12px !important; line-height: 1.4 !important; }
    .chatbot-box, .chatbot-box * { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif !important; font-size: 12px !important; line-height: 1.4 !important; }
    .chatbot-box p, .chatbot-box li, .chatbot-box span, .chatbot-box div { font-size: 12px !important; margin: 2px 0 !important; }
    .chatbot-box strong { font-weight: 700 !important; }
    .input-textbox { margin-top: 4px !important; width: 100% !important; }
    .input-textbox textarea { font-size: 14px !important; }
"""

with gr.Blocks() as ui:
    ui.queue(default_concurrency_limit=5)
    gr.Markdown(
        "# Supply Chain Last-Mile Delivery - AI Control Plane",
        elem_classes=["page-title"],
    )

    with gr.Row():
        # ---- LEFT COLUMN: Chat + Controls ----
        with gr.Column(scale=1):
            chatbot = gr.Chatbot(
                label="Agent Conversation",
                height=370,
                buttons=["copy"],
                elem_classes=["chatbot-box"],
                value=[{"role": "assistant", "content": _WELCOME_MSG}],
            )
            msg_input = gr.Textbox(
                placeholder="Ask about delivery delays, simulations, recommendations...",
                lines=3,
                show_label=False,
                container=False,
                elem_classes=["input-textbox"],
            )
            with gr.Row():
                submit_btn = gr.Button("Submit", variant="primary")
                clear_btn = gr.Button("Clear Chat", variant="secondary")
            orders_file = gr.File(
                label="Upload Orders CSV / Excel",
                file_count="multiple",
                type="filepath",
                file_types=[".csv", ".xls", ".xlsx"],
                value=_input_files,
                elem_classes=["file-upload"],
            )
            gr.Markdown("**Quick Actions**", elem_classes=["summary-label"])
            qa_btns = []
            with gr.Row():
                for label, _ in _QUICK_ACTIONS[:3]:
                    qa_btns.append(gr.Button(label, size="sm", elem_classes=["quick-btn"]))
            with gr.Row():
                for label, _ in _QUICK_ACTIONS[3:]:
                    qa_btns.append(gr.Button(label, size="sm", elem_classes=["quick-btn"]))

        # ---- RIGHT COLUMN: Output Tabs ----
        with gr.Column(scale=2):
            with gr.Tab("Predict Agent"):
                gr.Markdown("Daily Delivery Delay Prediction Summary", elem_classes=["summary-label"])
                predict_out = gr.Markdown(value="", elem_classes=["summary-markdown"])
                with gr.Column(elem_classes=["table-container"]):
                    predict_table_out = gr.Dataframe(
                        label="Predicted Daily Delivery Delays with Severity and Reasoning",
                        value=pd.DataFrame(),
                        interactive=False,
                        elem_classes=["output-table"],
                    )
                    predict_csv_file = gr.File(
                        label="Download Prediction CSV",
                        type="filepath",
                        elem_classes=["file-download"],
                    )
            with gr.Tab("Diagnosis Agent"):
                gr.Markdown("Last 1 Month + Current Daily Data -- Delay Patterns Diagnosis Summary", elem_classes=["summary-label"])
                diagnose_out = gr.Markdown(value="", elem_classes=["summary-markdown"])
                diagnose_high_risk_table_out = gr.Dataframe(
                    label="High-Risk Delay Pattern Combinations (Today)",
                    value=pd.DataFrame(),
                    interactive=False,
                    elem_classes=["output-table"],
                )
                diagnose_comparison_table_out = gr.Dataframe(
                    label="Today vs Historical - Delay Rate Comparison",
                    value=pd.DataFrame(),
                    interactive=False,
                    elem_classes=["output-table"],
                )
            with gr.Tab("Simulation / What-if Agent"):
                gr.Markdown("Simulations / What-if Outcomes", elem_classes=["summary-label"])
                simulate_out = gr.Markdown(value="", elem_classes=["summary-markdown"])
                with gr.Column(elem_classes=["table-container"]):
                    simulate_table_out = gr.Dataframe(
                        label="Simulated Orders with Delay Hours",
                        value=pd.DataFrame(),
                        interactive=False,
                        elem_classes=["output-table"],
                    )
                    simulate_csv_file = gr.File(
                        label="Download Simulation CSV",
                        type="filepath",
                        elem_classes=["file-download"],
                    )
            with gr.Tab("Recommendation Agent"):
                gr.Markdown("Recommendations", elem_classes=["summary-label"])
                recommend_out = gr.Markdown(value="", elem_classes=["summary-markdown"])
                rag_sources_file = gr.File(
                    label="RAG Sources",
                    value=_knowledge_files,
                    file_count="multiple",
                    interactive=False,
                    elem_classes=["file-download"],
                )
            with gr.Tab("Email / Alerts Agent"):
                gr.Markdown("Email Alert Templates & Summary", elem_classes=["summary-label"])
                email_out = gr.Markdown(value="", elem_classes=["summary-markdown"])
                email_csv_file = gr.File(
                    label="Download Prediction CSV (with Email Templates)",
                    file_count="single",
                    interactive=False,
                    elem_classes=["file-download"],
                )

    # ---- Hidden state for pending confirmation & tab persistence ----
    pending_state = gr.State("")
    tab_state = gr.State({})

    # ---- Outputs list (16 elements) ----
    outputs = [
        chatbot,                             # 0  updated chat history
        msg_input,                           # 1  clear textbox after submit
        pending_state,                       # 2  pending query state
        predict_out,                         # 3  predict summary markdown
        predict_table_out,                   # 4  predict table DataFrame
        predict_csv_file,                    # 5  predict CSV file path
        simulate_out,                        # 6  simulate summary markdown
        simulate_table_out,                  # 7  simulate table DataFrame
        simulate_csv_file,                   # 8  simulate CSV file path
        diagnose_out,                        # 9  diagnosis summary markdown
        diagnose_high_risk_table_out,        # 10 diagnosis high-risk table DataFrame
        diagnose_comparison_table_out,       # 11 diagnosis comparison table DataFrame
        recommend_out,                       # 12 recommendation summary markdown
        email_out,                           # 13 email summary markdown
        email_csv_file,                      # 14 email CSV file path
        tab_state,                           # 15 preserved tab outputs
    ]

    # ---- Wiring ----
    _handler_inputs = [msg_input, chatbot, orders_file, pending_state, tab_state]

    submit_btn.click(
        fn=chat_handler,
        inputs=_handler_inputs,
        outputs=outputs,
    )
    msg_input.submit(
        fn=chat_handler,
        inputs=_handler_inputs,
        outputs=outputs,
    )
    clear_btn.click(
        # Reset all 16 outputs to initial state:
        #   0: chatbot history (reset to welcome message)
        #   1: msg_input (clear textbox)
        #   2: pending_state (clear pending query)
        #   3-14: tab outputs (markdown, dataframes, file paths - all cleared)
        #   15: tab_state (clear preserved state dict)
        fn=lambda: ([{"role": "assistant", "content": _WELCOME_MSG}], "", "",
                     "", pd.DataFrame(), None,
                     "", pd.DataFrame(), None,
                     "", pd.DataFrame(), pd.DataFrame(),
                     "", "", None,
                     {}),
        outputs=outputs,
    )

    # Quick-action buttons: populate textbox then auto-submit.
    # The .click().then() chain ensures the textbox updates before chat_handler runs,
    # so the handler sees the preset text. The lambda captures preset_text via default
    # argument to avoid late binding issues in the loop.
    for btn, (_, preset_text) in zip(qa_btns, _QUICK_ACTIONS):
        btn.click(fn=lambda t=preset_text: t, outputs=[msg_input]).then(
            fn=chat_handler,
            inputs=_handler_inputs,
            outputs=outputs,
        )


if __name__ == "__main__":
    ui.launch(
        theme=gr.themes.Default(primary_hue="purple"),
        css=custom_css,
        inbrowser=True,
        allowed_paths=[str((_APP_DIR / "output").resolve())],
    )
