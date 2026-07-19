"""
Shared instrumentation — one capture mechanism used by run_once.py,
delivery_chat_app.py, and the experiment harness, so every consumer
produces identical records.

Two things are captured per top-level agent run:
  1. Every tool call the master makes: name, duration, payload, JSON validity,
     error (via function_middleware — this part was already working).
  2. Each sub-agent's own token usage. Sub-agents are invoked *inside* the
     wrapped tool functions in topologies/*.py, and their AgentResponse
     (with its own usage_details) is otherwise discarded after .value is
     extracted for the JSON payload. Since @tool functions take no context
     argument and FunctionInvocationContext exposes no metadata field,
     this can't be threaded through the middleware — a contextvar is used
     instead, set by the caller (run_once.py / delivery_chat_app.py) before
     the master run, and written to by record_sub_agent_usage() from
     inside each _wrap_as_tool call.
"""
import json
import time
import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from agent_framework import FunctionInvocationContext, function_middleware


# Utility parser used to label captured tool payloads as JSON/non-JSON.
def is_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except Exception:
        return False


# Usage-details normalizer: converts framework-specific token fields into the
# project's stable prompt/completion/total shape.
def extract_usage(response: Any) -> dict:
    """Normalize an AgentResponse's usage_details into a flat int dict."""
    usage = getattr(response, "usage_details", None) or {}
    return {
        "prompt_tokens":     int(usage.get("input_token_count") or 0),
        "completion_tokens": int(usage.get("output_token_count") or 0),
        "total_tokens":      int(usage.get("total_token_count") or 0),
    }

# Aggregate token usage across all sub-agents and master agent
def sum_usage(*usages: dict) -> dict:
    """Merge any number of usage dicts (as returned by extract_usage) into one."""
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for u in usages:
        for k in total:
            total[k] += u.get(k, 0)
    return total
    
# Need for contextvars.ContextVar : _wrap_as_tool's inner function (_run_sub_agent) is called by MAF internals with no way to 
# hand it "the current RunRecorder"
# A global variable would work for one run at a time but breaks the moment two things run 
# concurrently (which matters later for the concurrent topology). The fix is contextvars.ContextVar — 
# think of it as a variable that's global-looking, but each async task sees its own private 
# value rather than one shared value everyone stomps on.

_sub_agent_bucket: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "_sub_agent_bucket", default=None
)


# Bridge used by wrapped sub-agent tools (in topology code) to write token
# usage into the active recorder without changing tool function signatures.
# deep inside _wrap_as_tool, after the sub-agent finishes, 
# it calls record_sub_agent_usage(tool_name, result), which does _sub_agent_bucket.get() 
# to find that same list and appends the usage numbers to it 

def record_sub_agent_usage(tool_name: str, response: Any) -> None:
    """Call from inside a wrapped sub-agent tool right after agent.run()
    returns. No-op if no RunRecorder has set up tracking (e.g. running a
    sub-agent standalone outside a recorded master run)."""

    # Deep inside _wrap_as_tool, record_sub_agent_usage(tool_name, result) runs
    # bucket = _sub_agent_bucket.get() — this hands back that same list L. 
    # Then bucket.append({...}) mutates L directly, in place.

    # Because bucket and recorder.sub_agent_usage are the same object (not a copy), 
    # appending to one is appending to the other. So by the time your with block exits, 
    # recorder.sub_agent_usage already contains everything that got appended

    bucket = _sub_agent_bucket.get()
    if bucket is not None:
        bucket.append({"tool_name": tool_name, **extract_usage(response)})

    
# @dataclass auto-generates __init__, repr, and comparison helpers so this
# stays a lightweight structured record type rather than a manual class.
@dataclass
class ToolCallRecord:
    name: str
    started_at: float
    duration_s: float = 0.0
    payload: str = ""
    valid_json: bool = False
    error: str | None = None


# @dataclass here provides a concise state container for one top-level run.
@dataclass
class RunRecorder:
    """One instance per top-level agent run (i.e. per query, or per turn if
    you want per-turn granularity)."""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)

    # recorder = RunRecorder() runs the dataclass default, so recorder.sub_agent_usage is a 
    # fresh empty list — call it list object L. It lives at some memory address; 
    # recorder.sub_agent_usage is just a name pointing at L.
    sub_agent_usage: list[dict] = field(default_factory=list)

    # @property exposes recorder.middleware like a read-only attribute while
    # still constructing a fresh callable when accessed.
    @property
    def middleware(self):
        # @function_middleware marks _capture as framework middleware that
        # wraps each tool invocation: pre-call timing, post-call payload
        # capture, and error recording.
        @function_middleware
        async def _capture(context: FunctionInvocationContext, call_next):
            name = context.function.name
            t0 = time.perf_counter()
            record = ToolCallRecord(name=name, started_at=t0)
            self.tool_calls.append(record)
            try:
                # call_next() executes the actual tool implementation.
                await call_next()
            except Exception as e:
                record.error = str(e)
                raise
            finally:
                record.duration_s = round(time.perf_counter() - t0, 2)

            # Normalize heterogeneous tool return shapes into a text payload.
            r = context.result
            if isinstance(r, str):
                payload = r
            elif isinstance(r, (list, tuple)):
                payload = "".join(getattr(c, "text", "") for c in r)
            else:
                payload = repr(r)
            record.payload = payload
            record.valid_json = is_json(payload)
        return _capture

    # @contextmanager lets this method be used with `with ...:` syntax,
    # guaranteeing setup/teardown of the contextvar even on exceptions.
    @contextmanager
    def track_sub_agents(self):
        """Wrap the master.run(...) call(s) in this to have every
        record_sub_agent_usage() call during that scope land in
        self.sub_agent_usage."""
        token = _sub_agent_bucket.set(self.sub_agent_usage)
        try:
            yield
        finally:
            _sub_agent_bucket.reset(token)

    # Compact per-tool summary for UI/logging/reporting layers.
    def summary(self) -> list[dict]:
        return [
            {"tool_name": tc.name, "duration_s": tc.duration_s,
             "payload_chars": len(tc.payload), "valid_json": tc.valid_json,
             "error": tc.error}
            for tc in self.tool_calls
        ]

    # Aggregate token usage across all sub-agent invocations in this run.
    def total_sub_agent_usage(self) -> dict:
        total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for u in self.sub_agent_usage:
            for k in total:
                total[k] += u.get(k, 0)
        return total

    # Aggregate total time spent in all tool calls in this run.
    def total_tool_time(self) -> float:
        return round(sum(tc.duration_s for tc in self.tool_calls), 2)
    
    # Imperative alternative to track_sub_agents() for call sites where a
    # `with` block would require reindenting a large existing block (e.g.
    # deep inside an async generator). Must be paired with stop_tracking().
    def start_tracking(self):
        self._token = _sub_agent_bucket.set(self.sub_agent_usage)

    def stop_tracking(self):
        _sub_agent_bucket.reset(self._token)

