"""
    MCP server for predict + diagnosis (lives in prediction_pipeline/): shared by sub-agents
"""
import os
from pathlib import Path
import sys
from agent_framework import MCPStdioTool
from core.paths import PIPELINE_DIR

_PREDICTION_SERVER = str(
    PIPELINE_DIR / "prediction_server.py"
)
_PYTHON = sys.executable

# The only pipeline tools agents may call via MCP. Also imported by the chat
# app for per-tool timing logs, so the list is defined exactly once.
PIPELINE_TOOL_NAMES = [
    "predict_delivery_delays",
    "get_delay_diagnosis",
    "simulate_order_delays",
]

# One MCP server instance shared by the pipeline sub-agents.
# tool_filter restricts agents to only the pipeline tools, blocking
# everything else that may exist on the server now or in the future.
# Each sub-agent's prompt further pins it to its specific tool by name.
#
# env=os.environ.copy() is required, not cosmetic: MCPStdioTool with no env= leaves
# the underlying mcp package to spawn the child with only HOME/LOGNAME/PATH/SHELL/
# TERM/USER (its own hardcoded default allowlist -- not a passthrough of the parent's
# environment). prediction_server.py re-loads .env itself so most SC_* config
# survives that regardless, but locale/encoding vars (LANG, LC_ALL, PYTHONUTF8) do
# not come from .env and were absent -- diagnosed 5-Sep-26 as the cause of the MCP
# `initialize` handshake hanging for the full 120s request_timeout and then failing
# with McpError, even though running prediction_server.py directly in a normal shell
# (full environment) started it cleanly every time.
pipeline_mcp = MCPStdioTool(
    name="prediction_pipeline",
    command=_PYTHON,
    args=[_PREDICTION_SERVER],
    env=os.environ.copy(),
    allowed_tools=PIPELINE_TOOL_NAMES,   # MAF kwarg (was: tool_filter)
    request_timeout=120,                 # MAF kwarg (was: client_session_timeout_seconds)
)
