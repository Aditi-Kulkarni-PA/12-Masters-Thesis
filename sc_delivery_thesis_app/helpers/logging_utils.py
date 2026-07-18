"""Logging helpers for the supply chain delivery app.

Contains the per-run file logger setup and the token-usage extraction
helpers used to log LLM usage from streamed SDK events.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


def setup_run_logger(app_dir: Path) -> tuple[logging.Logger, Path]:
    """Create one timestamped log file for the current app process run."""
    log_dir = app_dir / "log"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique filename using current timestamp to separate runs
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"delivery_chat_run_{ts}.log"

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
