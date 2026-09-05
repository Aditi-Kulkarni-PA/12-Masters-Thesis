"""
Monolith — CORE CONDITION 1 (T37).

ONE LLM context does all the work. It calls the same raw pipeline tools every other
condition ultimately calls, but there are no sub-agents between it and them.

What is held constant against Planner-Executor
----------------------------------------------
The tools. `predict_delivery_delays` runs a trained two-stage RandomForest over the raw
orders file; `get_delay_diagnosis` and `simulate_order_delays` query the historical
baseline tables; `recommend_actions` retrieves service-level knowledge. No language model
can perform any of that. Planner-Executor's five sub-agents each call these very same
tools (`core/agents.py`: every one is `Agent(..., tools=[pipeline_mcp])`). Removing them
here would not make the condition "more monolithic" — it would make the model fabricate
its data while every other condition computes it, and the comparison would collapse into
"did it invent plausible numbers".

What actually varies
--------------------
The number of LLM contexts, and how instruction and tool output are partitioned across
them:

                        Monolith            Planner-Executor
  LLM contexts          1                   6 (master + 5 specialists)
  System prompt         ~49,200 chars       master ~21,400; the five domain
                        (all 5 domain       prompts (~31,000) live in SEPARATE
                        prompts inlined)    contexts
  Tool payloads         all ~68,500 chars   split across 5 specialist contexts;
                        accumulate in the   the master sees only structured
                        SAME context        returns

So the single context ends up carrying roughly 117,000 characters where Planner-Executor's
largest single context carries about 31,000. That gap is the independent variable — context
dilution, not tool deprivation — and it is what the run measures.

Held identical to every other condition: the model, `temperature=0`, the raw tools, and
the domain prompt text (included verbatim, not summarised).

Response schema is NOT MasterOutput as originally built (fixed 23-Aug-26). MasterOutput
has no field for a specialist's own structured result because six of the seven
conditions never need one -- the app captures each specialist's structured output
directly from the tool-call stream, which for Planner-Executor/Swarm IS the wrapped
sub-agent's own return value. Monolith has no such capture point: it calls the same raw
tools, which return deliberately unenriched data (empty llm_insights, no
simulate_delay_reason, no predict_summary -- see core/schemas.py MonolithOutput's
docstring for the full trace). Giving monolith MasterOutput's four narrative fields and
nowhere else to write that analytical work meant the work was silently skipped, which
was NOT the "one context vs six contexts" comparison this file describes above -- it was
one condition doing less work, not the same work more cheaply. MonolithOutput extends
MasterOutput with one field per specialist's own result type (the identical models
Planner-Executor's specialists already produce), so the schema is what actually differs
by necessity (no separate capture point to rely on), while everything else stays equal.
"""

import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from dotenv import load_dotenv, find_dotenv
load_dotenv(dotenv_path=find_dotenv(), override=False)

from agent_framework import Agent

from config import get_instruction
from core.clients import chat_client
from core.mcp_tools import pipeline_mcp
from core.schemas import MonolithOutput
from tools import recommend_actions, fetch_delayed_orders_for_email

TOPOLOGY = "monolith"


def build_master() -> Agent:
    """Construct the single-context monolith agent.

    A factory, not a module-level object, for the same reason Planner-Executor uses one:
    the coordinator prompt is read at call time from
    prompts/coordinators/monolith/master.md, so the topology stays selectable per run
    instead of being frozen at import.

    Note what is NOT here: no `_wrap_as_tool`, and no `fallback_advisor_tool`. Planner-
    Executor exposes the fallback advisor as an agent-as-tool, which is a second LLM
    context. Attaching it here would destroy the one property this condition exists to
    isolate. The fallback advisor's *instructions* are inlined into master.md instead —
    the same treatment the five domain prompts get, and for the same reason: with no
    sub-agents to delegate to, the single prompt is the only place that expertise can
    live.
    """
    return Agent(
        name="Supply Chain Last-Mile Delivery Optimization Expert Agent",
        instructions=get_instruction("master", topology=TOPOLOGY),
        client=chat_client,
        # Raw tools, attached directly. pipeline_mcp exposes predict_delivery_delays,
        # get_delay_diagnosis and simulate_order_delays; the other two are local
        # function tools. These are the identical objects Planner-Executor's specialists
        # call one level down.
        tools=[
            pipeline_mcp,
            recommend_actions,
            fetch_delayed_orders_for_email,
        ],
        default_options={"tool_choice": "auto", "response_format": MonolithOutput, "temperature": 0},
    )
