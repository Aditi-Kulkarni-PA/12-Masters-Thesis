""" 
    MCP server for predict + diagnosis (lives in prediction_pipeline/): shared by sub-agents
"""
from pathlib import Path
import sys 
from agent_framework import MCPStdioTool

_PREDICTION_SERVER = str(
    Path(__file__).resolve().parent.parent.parent / "prediction_pipeline" / "prediction_server.py"
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
pipeline_mcp = MCPStdioTool(
    name="prediction_pipeline",
    command=_PYTHON,
    args=[_PREDICTION_SERVER],
    allowed_tools=PIPELINE_TOOL_NAMES,   # MAF kwarg (was: tool_filter)
    request_timeout=120,                 # MAF kwarg (was: client_session_timeout_seconds)
)
