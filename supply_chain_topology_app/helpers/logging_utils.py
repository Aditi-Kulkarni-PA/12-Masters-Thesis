"""Logging helpers for the supply chain delivery app.

Contains the per-run file logger setup and the token-usage extraction
helpers used to log LLM usage from streamed SDK events.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path


def _run_log_path(log_dir: Path) -> Path:
    """Where this process's trace log belongs, and what to call it.

    A harness run is identified by the environment the experiment runner sets
    (SC_BATCH_ID, SC_EXECUTION_ORDER, SC_TOPOLOGY, SC_QUERY_ID, SC_RUN_N), so the trace
    goes beside the console log for the same run:

        log/batches/<batch_id>/traces/<order>_<topology>_<query>_n<run_n>.log

    which matches the console log execute_experiment.py writes at
    log/batches/<batch_id>/<order>_<topology>_<query>_n<run_n>.log. Naming the two
    identically is the point: a trace can be tied to its run from the filename alone.
    Until 9-Sep-26 every trace was written to log/delivery_chat_run_<timestamp>.log, the
    same name the interactive chat app uses, so 289 of 297 pilot runs recorded a log path
    carrying no topology, query or batch, and a cleanup by filename would have deleted
    real evidence.

    A run with no batch (an ad-hoc execute_topology.sh invocation) goes to
    log/batches/_unbatched/traces/. The interactive chat app sets none of these variables
    and keeps the original timestamped name at the top of log/, which is correct: it is a
    session, not a measured run.
    """
    topology = os.getenv("SC_TOPOLOGY", "").strip()
    query_id = os.getenv("SC_QUERY_ID", "").strip()
    if not (topology and query_id):
        # Not a harness run -- the chat app, or a bare import. Keep the old behaviour.
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return log_dir / f"delivery_chat_run_{ts}.log"

    batch = os.getenv("SC_BATCH_ID", "").strip() or "_unbatched"
    run_n = os.getenv("SC_RUN_N", "").strip() or "0"
    order_raw = os.getenv("SC_EXECUTION_ORDER", "").strip()
    # Zero-padded so the folder sorts in execution order rather than lexically.
    order = f"{int(order_raw):03d}_" if order_raw.isdigit() else ""
    return log_dir / "batches" / batch / "traces" / f"{order}{topology}_{query_id}_n{run_n}.log"


def run_slug(fallback: str = "") -> str:
    """This run's identity as a filename fragment, from the harness environment.

    Returns "<order>_<topology>_<query>_n<run_n>" -- the same stem the run's console log
    and trace already use -- so any per-run artefact written anywhere in the codebase can
    be tied back to its run by name alone.

    *fallback* is returned when the environment does not describe a harness run: an
    interactive session, or an ad-hoc invocation. Callers pass whatever identifier they
    were already using, so behaviour outside the harness is unchanged.
    """
    topology = os.getenv("SC_TOPOLOGY", "").strip()
    query_id = os.getenv("SC_QUERY_ID", "").strip()
    if not (topology and query_id):
        return fallback
    run_n = os.getenv("SC_RUN_N", "").strip() or "0"
    order_raw = os.getenv("SC_EXECUTION_ORDER", "").strip()
    order = f"{int(order_raw):03d}_" if order_raw.isdigit() else ""
    return f"{order}{topology}_{query_id}_n{run_n}"


def run_batch(fallback: str = "_unbatched") -> str:
    """The batch this run belongs to, for grouping per-run artefacts on disk."""
    return os.getenv("SC_BATCH_ID", "").strip() or fallback


def setup_run_logger(app_dir: Path) -> tuple[logging.Logger, Path]:
    """Create one log file for the current app process run.

    For a harness run the file lands in its batch's traces/ folder under the same name as
    the run's console log; for an interactive session it keeps the timestamped name at the
    top of log/. See _run_log_path().
    """
    log_dir = app_dir / "log"
    log_path = _run_log_path(log_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Get or create the named logger for this application
    logger = logging.getLogger("supply_chain_delivery_app")
    logger.setLevel(logging.INFO)
    
    # Clear any existing handlers to avoid duplicate log entries if this
    # function is called multiple times (e.g., during testing or reloads)
    logger.handlers.clear()
    
    # Disable propagation to parent loggers to prevent duplicate output
    # to root handlers (e.g., console loggers set up by other libraries)
    logger.propagate = False

    # Create file handler with UTF-8 encoding to support international characters
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    
    # Format log entries with timestamp, level, logger name, and message
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.info("logger.initialized path=%s", log_path)
    return logger, log_path


# ---------------------------------------------------------------------------
# Token-usage extraction from streamed SDK events
# Different SDK versions expose usage in different places/field names; these
# helpers normalize everything into one dict:
#   {"model": ..., "prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...}
# ---------------------------------------------------------------------------

def _value_from_usage(usage, *names):
    """Return the first matching numeric field from a usage object/dict.

    Tries a list of possible field names (e.g. prompt_tokens vs input_tokens)
    to stay compatible across API versions.
    """
    for name in names:
        # Try dict-style access first (for plain dict structures)
        if isinstance(usage, dict) and name in usage:
            return usage.get(name)
        # Fall back to attribute access (for object/dataclass structures)
        # This handles SDK responses wrapped in dataclass or custom objects
        if hasattr(usage, name):
            return getattr(usage, name)
    # No matching field found in this usage object
    return None


def extract_usage_from_event(event):
    """Best-effort extraction of model + token usage from a raw_response_event.

    Searches the event structures used by different SDK versions, normalizes
    token field names, computes total tokens if missing, and returns a
    consistent dict for logging — or None if the event carries no usage.
    """
    # Different SDK versions nest usage data in different locations:
    # - event.data.response.usage (older SDK)
    # - event.response.usage (newer SDK)
    # - event.data.usage (alternative)
    # - event.usage (direct)
    # Build a priority list of candidate objects to check
    data = getattr(event, "data", None)
    response = getattr(data, "response", None) if data is not None else None
    candidates = [response, data, getattr(event, "response", None), event]

    usage = None
    model = None
    # Walk through candidates in priority order until we find usage data
    for c in candidates:
        if c is None:
            continue
        # Try both dict-style and attribute access to handle different structures
        usage = c.get("usage") if isinstance(c, dict) else getattr(c, "usage", None)
        if usage is not None:
            # Found usage data; also extract model name from the same location
            model = c.get("model") if isinstance(c, dict) else getattr(c, "model", None)
            break

    # No usage data found anywhere in the event structure
    if usage is None:
        return None

    # Normalize field names: OpenAI uses "prompt_tokens", Anthropic uses "input_tokens"
    # Try both variants for each field to maximize compatibility
    prompt_tokens = _value_from_usage(usage, "prompt_tokens", "input_tokens")
    completion_tokens = _value_from_usage(usage, "completion_tokens", "output_tokens")
    total_tokens = _value_from_usage(usage, "total_tokens")
    
    # Some APIs omit total_tokens; compute it from components if possible
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens

    # Return normalized dict for consistent logging across all API providers
    return {
        "model": model or "unknown",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
