# MCP Server Design

## What Is the MCP Server?

The FastMCP server (`prediction_pipeline/prediction_server.py`) is the communication boundary between the two sub-systems. The ML prediction pipeline exposes its capabilities as callable tools; the agent layer consumes them without importing any ML code directly.

Transport: **stdio** — the agent layer spawns the server as a child process and communicates via stdin/stdout JSON-RPC. This keeps the two sub-systems process-isolated and independently runnable.

```
supply_chain_delivery_app/          prediction_pipeline/
┌─────────────────────────┐         ┌──────────────────────────────┐
│  delivery_agents.py     │  stdio  │  prediction_server.py        │
│                         │ ──────► │  FastMCP("prediction_pipeline")│
│  MCPServerStdio(        │         │                              │
│    command=python,      │         │  @mcp.tool()                 │
│    args=[prediction_    │         │  predict_delivery_delays()   │
│          server.py]     │ ◄────── │  get_delay_diagnosis()       │
│  )                      │  JSON   │  simulate_order_delays()     │
└─────────────────────────┘         └──────────────────────────────┘
```

## Three Exposed Tools

### 1. `predict_delivery_delays(file_path, csv_dir="")`

Runs the two-stage Random Forest pipeline on a CSV of orders.

- Stage 1: binary classification — delayed vs on-time
- Stage 2: severity classification — Short (1–2h), Medium (3–5h), Long (6+h) for delayed orders only

Returns JSON with two keys:
- `summary` — aggregate stats: total orders, delayed count, % delayed, severity breakdown, top regions/weather/partners
- `delayed_orders` — all delayed rows with a rule-based `delay_reason` field (the Predict agent then enriches each row with `llm_inference`)

Side effects: writes prediction CSV to `prediction_pipeline/data/processed/` and refreshes all 27 SQLite summary tables.

### 2. `get_delay_diagnosis()`

Reads all daily and historical summary tables from SQLite and returns comparison data for root-cause diagnosis. No input arguments.

Returns: overall KPIs, dimension-by-dimension comparison (daily vs historical), and high-risk pattern combinations.

Prerequisite check: returns `{"Error": "upstream_missing"}` if the prediction CSV or SQLite tables are absent — enforcing the predict → diagnose sequence.

### 3. `simulate_order_delays(scenario, filters, changes)`

What-if simulation on the predicted delayed orders CSV.

| Arg | Type | Example |
|---|---|---|
| `scenario` | Natural-language description | `"Weather turns stormy in East region"` |
| `filters` | JSON — rows to modify | `{"region": "east"}` |
| `changes` | JSON — new column values | `{"weather_condition": "stormy"}` |

Filters accepted: `region`, `delivery_mode`, `vehicle_type`, `weather_condition`, `delivery_partner`, `package_type`, `min_distance_km`.

Looks up historical severity distribution from `hist_summary_by_*` SQLite tables and reassigns severity labels proportionally. Saves simulation CSV and returns a Markdown summary + top rows.

Prerequisite check: same upstream guard as `get_delay_diagnosis`.

## Connection Setup in the Agent Layer

```python
# delivery_agents.py

_MCP_PARAMS = {
    "command": sys.executable,
    "args": [".../prediction_pipeline/prediction_server.py"]
}

pipeline_mcp = MCPServerStdio(
    name="prediction_pipeline",
    params=_MCP_PARAMS,
    tool_filter={"allowed_tool_names": [
        "predict_delivery_delays",
        "get_delay_diagnosis",
        "simulate_order_delays"
    ]},
    client_session_timeout_seconds=120,
)
```

One `MCPServerStdio` instance is shared across the three agents that need it (Predict, Diagnose, Simulate). `tool_filter` restricts what each agent can see — hardened further by each agent's prompt which pins it to its specific tool by name.

## Key Design Decisions

**Why stdio over HTTP?** Both sub-systems run on the same machine. Stdio is simpler: no port management, no authentication, no server lifecycle to manage separately. The agent SDK spawns and tears down the process automatically.

**Why a shared server instance?** A single `pipeline_mcp` object is passed to all three agents. This avoids spawning three separate child processes and keeps SQLite connection state consistent across a session.

**Why `tool_filter`?** Defence in depth. Even if the MCP server gains new tools in the future, the agent layer is locked to the three it was designed to use. Each sub-agent's prompt adds a second layer by naming its exact tool.

**Sequential execution as a first-class constraint.** `get_delay_diagnosis` and `simulate_order_delays` both call `_check_predict_ran()` on entry and return an explicit error if prediction artifacts are missing. This makes the predict → diagnose/simulate dependency machine-enforceable, not just documented — the Master Orchestrator also enforces sequencing via prompt, but the MCP layer is a hard backstop.

**120-second timeout.** The prediction pipeline runs ML inference on up to 5,000 rows and writes SQLite summaries. `client_session_timeout_seconds=120` prevents premature timeout on first-run or large files.

## Environment Variables

| Variable | Used by | Purpose |
|---|---|---|
| `SC_PREDICTION_DB_PATH` | `prediction_server.py` | Path to SQLite database |
| `SC_DELIVERY_OUTPUT_DIR` | `prediction_server.py` | Output directory for prediction CSVs |
| `SC_MCP_ENRICH_ROWS` | `daily_predict.py` | Max rows sent to LLM for enrichment (default 10) |
| `OPENAI_AGENTS_DISABLE_TRACING` | SDK | Set to `1` to avoid 10KB trace payload limit blocking requests |
