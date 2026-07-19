"""
Single source of truth for dual-stack eval settings.

run_evals.py (launches pytest with the right env) and conftest.py (configures
sys.path/env inside the pytest session) both import from here — the mapping
of stack name -> app dir, and the env vars each eval run needs, are defined
exactly once.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# stack name -> app directory (relative to PROJECT_ROOT)
STACK_APP_DIRS = {
    "baseline": "supply_chain_delivery_app",  # OpenAI Agents SDK — frozen reference, never edited again
    "maf":      "sc_delivery_thesis_app",     # Microsoft Agent Framework — actual baseline going forward
}

OPENAI_MODEL = "gpt-5.4"
OPENAI_MODEL_MINI = "gpt-4.1-mini"
SC_EMAIL_MAX_ROWS = "3"


def mcp_env(eval_db_path: str, model_dir: str, api_key: str) -> dict:
    """Env vars injected into the MCP prediction-pipeline subprocess.
    Currently identical across stacks; a function (not a bare dict) so a
    future stack — e.g. a GLM-backed variant — can override specific keys."""
    return {
        "SC_PREDICTION_DB_PATH":   eval_db_path,
        "SC_PREDICTION_MODEL_DIR": model_dir,
        "OPENAI_API_KEY":          api_key,
        "OPENAI_MODEL":            OPENAI_MODEL,
        "OPENAI_MODEL_MINI":       OPENAI_MODEL_MINI,
    }