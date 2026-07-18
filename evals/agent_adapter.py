"""Framework-agnostic agent runner for evals: OpenAI SDK or MAF."""
from typing import Any

class EvalRunResult:
    def __init__(self, final_output: Any, called_tools: list[str], tool_outputs: dict[str, str]):
        self.final_output = final_output
        self.called_tools = called_tools
        self.tool_outputs = tool_outputs

async def run_agent(agent, agent_input: str) -> EvalRunResult:
    try:
        from agent_framework import Agent as MafAgent
        is_maf = isinstance(agent, MafAgent)
    except ImportError:
        is_maf = False
    return await _run_maf(agent, agent_input) if is_maf else await _run_sdk(agent, agent_input)

async def _run_sdk(agent, agent_input: str) -> EvalRunResult:
    from agents import Runner
    result = await Runner.run(agent, agent_input)
    called, outputs = [], {}
    items = list(result.new_items)
    for i, item in enumerate(items):
        if getattr(item, "type", "") == "tool_call_item":
            name = getattr(getattr(item, "raw_item", None), "name", "")
            if name:
                called.append(name)
                # tool_call_output_item follows its call item
                for j in range(i + 1, len(items)):
                    if getattr(items[j], "type", "") == "tool_call_output_item":
                        outputs[name] = str(getattr(items[j], "output", ""))
                        break
    return EvalRunResult(result.final_output, called, outputs)

async def _run_maf(agent, agent_input: str) -> EvalRunResult:
    import time
    from agent_framework import FunctionInvocationContext, function_middleware

    called, outputs = [], {}

    @function_middleware
    async def capture(context: FunctionInvocationContext, call_next):
        name = context.function.name
        called.append(name)
        await call_next()
        r = context.result
        if isinstance(r, str):
            outputs[name] = r
        elif isinstance(r, (list, tuple)):
            outputs[name] = "".join(getattr(c, "text", "") for c in r)
        else:
            outputs[name] = repr(r)

    result = await agent.run(agent_input, middleware=[capture])
    return EvalRunResult(result.value, called, outputs)