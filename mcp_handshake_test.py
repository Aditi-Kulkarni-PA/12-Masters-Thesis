"""
Minimal MCP handshake test -- bypasses agent_framework/execute_topology.py entirely.
Talks to prediction_server.py using nothing but the raw `mcp` client library, to isolate
whether the initialize() hang is in the mcp<->server layer itself or in agent_framework's
MCPStdioTool wrapper around it.

Run from the repo root:
    .venv/bin/python3 mcp_handshake_test.py

Delete this file once the investigation is done -- it is a throwaway diagnostic, not
part of the app.
"""
import asyncio
import logging
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Wire-level detail on the client side -- shows every frame sent/received so we can see
# whether the request even leaves this process, and whether anything comes back at all.
logging.basicConfig(
    level=logging.DEBUG,
    stream=sys.stderr,
    format="%(asctime)s CLIENT %(name)s %(levelname)s %(message)s",
)

_SERVER = os.path.join(os.path.dirname(__file__), "prediction_pipeline", "prediction_server.py")


async def main():
    server_env = os.environ.copy()
    # Force debug logging on the SERVER side too (prediction_server.py hardcodes
    # WARNING for the "mcp" logger; this env var + the logging.basicConfig call
    # server-side would need the same override -- see instructions to also comment
    # out prediction_server.py's own setLevel(WARNING) line for this diagnostic run).
    server_env["PYTHONUNBUFFERED"] = "1"
    params = StdioServerParameters(
        command=sys.executable,
        args=[_SERVER],
        env=server_env,
    )
    print(f"spawning: {sys.executable} {_SERVER}", flush=True)
    async with stdio_client(params) as (read, write):
        print("pipes open, creating session...", flush=True)
        async with ClientSession(read, write) as session:
            print("session created, calling initialize() (30s timeout)...", flush=True)
            result = await asyncio.wait_for(session.initialize(), timeout=30)
            print("SUCCESS:", result, flush=True)
            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
