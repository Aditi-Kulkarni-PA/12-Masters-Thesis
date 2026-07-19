"""
Supply Chain Delivery – Agent definitions.

Key logic:
    - Imports all Pydantic models for agent tool I/O (predict, diagnose, simulate, recommend, email)
    - Imports MCP server for prediction/diagnosis tools (pipeline_mcp)
    - Each sub-agent is an Agent with its own prompt, model, and tool wiring
    - Master orchestrator agent (supply_chain_delivery_master_agent) coordinates all tools and output
    - All tool calls are strongly typed and validated via Pydantic
    - Model selection, tool_choice, and output_type are set for each agent
    - Fallback and formatting agents handle edge cases and summary formatting
"""
from agent_framework import Agent
from config import get_instruction
from core.mcp_tools import pipeline_mcp
from core.schemas import (
    DeliveryDelayPredictionResult, DelayDiagnosisResult,
    SimulationsList, RecommendedActionsList, EmailsList,
)

from tools import recommend_actions, fetch_delayed_orders_for_email
from core.mcp_tools import pipeline_mcp
from core.clients import chat_client, chat_client_mini

# ---------------------------------------------------------------------------
# 1. Predict delivery delays — agent/tool definition (schemas in core/schemas.py)
# ---------------------------------------------------------------------------

# Predictive agent: forces tool-backed output into a strict schema.
def build_predict_agent(client) -> Agent:
    return Agent(
        name="Predict Delivery Delays",
        client=client,
        instructions=get_instruction("predict_delivery_delays"),
        tools=[pipeline_mcp],
        # Keep outputs deterministic and structured for downstream orchestration.
        default_options={"temperature": 0, "tool_choice": "required",
                          "response_format": DeliveryDelayPredictionResult},
    )

# ---------------------------------------------------------------------------
# 2. Diagnose delay patterns — agent/tool definition (schemas in core/schemas.py)
# ---------------------------------------------------------------------------

# Diagnosis agent: inspects likely delay drivers and pattern-level insights.
def build_diagnose_agent(client) -> Agent:
    return Agent(
        name="Diagnose & Analyse Delay Patterns",
        client=client,
        instructions=get_instruction("diagnose_delay_patterns"),
        tools=[pipeline_mcp],
        # Require tool usage so analysis is grounded in pipeline data.
        default_options={"temperature": 0, "tool_choice": "required",
                          "response_format": DelayDiagnosisResult},
    )
# ---------------------------------------------------------------------------
# 3. Delay simulation — agent/tool definition (schemas in core/schemas.py)
# ---------------------------------------------------------------------------

# Simulation agent: evaluates what-if scenarios using the same pipeline backend.
def build_simulate_agent(client) -> Agent:
    return Agent(
        name="Simulate & Analyse Delay Prediction",
        client=client,
        instructions=get_instruction("delay_simulation"),
        tools=[pipeline_mcp],
        # Structured list output keeps scenario comparison machine-readable.
        default_options={"temperature": 0, "tool_choice": "required",
                          "response_format": SimulationsList},
    )

# ---------------------------------------------------------------------------
# 4. Recommendation — agent/tool definition (schemas in core/schemas.py)
# ---------------------------------------------------------------------------

# Recommendation agent: converts findings into actionable interventions.
def build_recommend_agent(client) -> Agent:
    return Agent(
        name="Recommendation Expert Agent to Optimize Order Delivery",
        client=client,
        instructions=get_instruction("recommendation"),
        tools=[recommend_actions],
        # Enforce tool-mediated recommendations and typed response payload.
        default_options={"temperature": 0, "tool_choice": "required",
                          "response_format": RecommendedActionsList},
    )

# ---------------------------------------------------------------------------
# 5. Email alert — agent/tool definition (schemas in core/schemas.py)
# ---------------------------------------------------------------------------

# Email agent: generates stakeholder-ready alert messages for delayed orders.
def build_email_agent(client) -> Agent:
    return Agent(
        name="Email Alert Agent",
        client=client,
        instructions=get_instruction("email_alert"),
        tools=[fetch_delayed_orders_for_email],
        # Typed output enables direct rendering/sending in the UI pipeline.
        default_options={"temperature": 0, "tool_choice": "required",
                          "response_format": EmailsList},
    )

# ---------------------------------------------------------------------------
# 7. Fallback agent — handles out-of-scope prompts or generic advisory requests
# ---------------------------------------------------------------------------

# Fallback agent: handles out-of-scope prompts or generic advisory requests.
def build_fallback_agent(client) -> Agent:
    return Agent(name="Fallback Supply Chain Optimization Agent",
                 client=client, instructions=get_instruction("fallback_advisor"))

# ---------------------------------------------------------------------------
# 8-Optional. Format summary agent — lightweight formatter (uses MODEL_MINI)
# ---------------------------------------------------------------------------

# Formatter agent: rewrites summaries for readability with low creativity.
def build_format_summary_agent(client) -> Agent:
    return Agent(name="Summary Formatting Specialist", client=client,
                 instructions=get_instruction("format_summary"),
                 default_options={"temperature": 0})

# Instantiate all agents with the primary chat client (or mini client for formatting).
predict_delivery_delays_agent = build_predict_agent(chat_client)
diagnose_delay_patterns_agent = build_diagnose_agent(chat_client)
delay_simulation_agent        = build_simulate_agent(chat_client)
recommendation_agent          = build_recommend_agent(chat_client)
email_alert_agent             = build_email_agent(chat_client)
fallback_advisor_agent        = build_fallback_agent(chat_client)
format_summary_agent          = build_format_summary_agent(chat_client_mini)