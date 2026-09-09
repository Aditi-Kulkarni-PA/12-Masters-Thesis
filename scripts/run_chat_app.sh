#!/usr/bin/env bash
# run_chat_app.sh — launch the Gradio delivery chat UI.
#
# Thin wrapper, same pattern as execute_topology.sh and run_experiment.sh: pins the venv
# to the repo, then hands off to delivery_chat_app.py.
#
# The app is the demonstration surface for the delivery capabilities, not the measurement
# harness. It runs planner-executor only (see the topology comment in
# delivery_chat_app.py for why the other eight do not fit a conversational UI), and it
# writes nothing to the run store. Use scripts/execute_topology.sh for a recorded run and
# scripts/run_experiment.sh for a batch.
#
# The prediction server does not need starting separately: pipeline_mcp is an
# MCPStdioTool, so the app spawns it as a subprocess and closes it on exit.
#
# Usage:
#   ./run_chat_app.sh                  # start the UI on http://127.0.0.1:7860
#   SC_NO_CACHE=1 ./run_chat_app.sh    # disable response cache and freshness reuse
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."   # repo root: pyproject.toml, uv.lock and .env live there

# Pin the environment to the repo, matching the other scripts (see execute_topology.sh's
# own comment for why: a shell deriving UV_PROJECT_ENVIRONMENT from $PWD would otherwise
# build a second venv under scripts/).
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"

echo "=================================================================="
echo " delivery chat app : planner_executor"
echo " starting Gradio   : a browser tab opens on http://127.0.0.1:7860"
echo " model tier        : from .env, same as every harness script"
echo "=================================================================="

uv run --env-file .env python supply_chain_topology_app/delivery_chat_app.py
