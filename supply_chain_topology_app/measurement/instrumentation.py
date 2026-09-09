"""
Shared instrumentation — one capture mechanism used by execute_topology.py,
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
     instead, set by the caller (execute_topology.py / delivery_chat_app.py) before
     the master run, and written to by record_sub_agent_usage() from
     inside each _wrap_as_tool call.

A third case (Dynamic-Graph, 29-Aug-26): a participant/manager agent.run() call driven
entirely by framework-internal code (MagenticAgentExecutor / StandardMagenticManager),
never passing through any tool call or wrapped-tool function this app controls at all.
Neither of the two mechanisms above can see it -- RunRecorder.agent_completion_middleware()
is the third: an @agent_middleware hook (wraps the whole .run() call, not a tool
invocation) attached to such an agent at construction, recording usage the same way and,
for a participant, also injecting a synthetic tool_call row carrying its own structured
.value under its canonical tool name -- exactly what a wrapped @tool function's return
value already provides everywhere else.
"""
import itertools
import json
import time
import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from agent_framework import AgentContext, FunctionInvocationContext, agent_middleware, function_middleware
from pydantic import BaseModel

from measurement.dependencies import canonical


# Capture-mechanism version (T112 / Risk Log R48, 30-Aug-26). Stamped onto every run
# row by run_store_writer.write_run() as run.capture_version, so analysis code can
# select "runs captured the same way" without inferring it from started_at or from
# config_hash (which hashes model + prompt versions and churns for reasons unrelated
# to instrumentation). Bump this int whenever the CAPTURE MECHANISM changes in a way
# that alters what gets recorded for an existing topology -- not for a new topology
# being added (nothing existing changes) and not for a prompt/model change (that is
# config_hash's job). Runs written before this column existed have capture_version
# NULL and must be excluded from analysis rather than assumed to be version 1: NULL
# means "unknown", not "oldest".
#
# History:
#   1 (30-Aug-26) -- first stamped version. Captures three prior, undated mechanism
#     changes that are already live in this module but were never distinguishable in
#     the store: (a) record_sub_agent_usage() via the _sub_agent_bucket contextvar,
#     (b) run_agent_as_tool_call()'s synthetic FunctionInvocationContext for
#     code-scheduled topologies, (c) agent_completion_middleware() for Magentic
#     participants (Dynamic-Graph) -- confirmed from run_store.db to have started
#     landing on runs from 29-Aug-26 14:15 onward; every dynamic_graph run before that
#     records zero specialist tokens. Runs from before 29-Aug-26 14:15 predate a
#     mechanism this version relies on and must be treated as unversioned (NULL), same
#     as any other pre-column row -- do not backfill them to version 1.
#   2 (30-Aug-26) -- per-tool usage is now correlated to its tool_call by call_id
#     (_current_call_id contextvar) instead of by (tool_name, completion order).
#     Bumped because this changes what gets recorded for EXISTING topologies, which is
#     this constant's stated trigger: any run where one tool name appeared twice with
#     starts and completions in different orders had those two calls' per-tool token
#     figures swapped under version 1. Run totals were unaffected either way, so
#     version-1 rows remain valid for every total/aggregate figure and only their
#     PER-TOOL costs are suspect -- and only on runs with a repeated tool name, which
#     no condition built before Mesh B produces concurrently. See Risk Log R50 / T114.
CAPTURE_VERSION = 2


# Utility parser used to label captured tool payloads as JSON/non-JSON.
def is_json(s: str) -> bool:
    """True if the payload STARTS with a complete JSON document.

    Raw MCP tools return a JSON object followed by a second, text-wrapped copy, so
    json.loads() on the whole string raises "Extra data" and every monolith tool call
    was recorded valid_json=False. That is not a quality signal -- it is the return
    shape of the transport -- and it silently disabled empty-payload and fabricated-
    narrative detection for any condition calling raw tools. raw_decode reads the
    leading document and ignores what follows.
    """
    if not s:
        return False
    try:
        json.loads(s)
        return True
    except Exception:
        pass
    try:
        json.JSONDecoder().raw_decode(s.lstrip())
        return True
    except Exception:
        return False


# Usage-details normalizer: converts framework-specific token fields into the
# project's stable prompt/completion/total shape.
# Keys a provider may use for the cached portion of the prompt. The field is not part of
# the flat usage_details surface, so it is looked for under several spellings and, when
# present, under the nested details object the Responses API uses. Capturing it is what
# lets cost separate cached prompt tokens from fresh ones -- see the note in
# extract_usage below for why that matters.
_CACHED_TOKEN_KEYS = (
    "cached_token_count",
    "cached_tokens",
    "input_cached_token_count",
    "prompt_cached_token_count",
)


def _cached_tokens(usage: Any) -> int:
    """Cached prompt tokens for one response, or 0 when the provider reports none."""
    if not usage:
        return 0
    for key in _CACHED_TOKEN_KEYS:
        value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
        if value:
            return int(value)
    # Responses API nests it: usage.input_tokens_details.cached_tokens
    for parent in ("input_token_details", "input_tokens_details", "prompt_tokens_details"):
        nested = usage.get(parent) if isinstance(usage, dict) else getattr(usage, parent, None)
        if nested:
            for key in _CACHED_TOKEN_KEYS:
                value = (nested.get(key) if isinstance(nested, dict)
                         else getattr(nested, key, None))
                if value:
                    return int(value)
    return 0


def extract_usage(response: Any) -> dict:
    """Normalize an AgentResponse's usage_details into a flat int dict.

    cached_tokens is the portion of prompt_tokens the provider served from its prompt
    cache. It is captured because cached prompt tokens are billed at a fraction of the
    fresh input rate, so charging every prompt token at the full rate overstates cost.
    The overstatement grows with the size of the repeated prompt prefix and with the
    input price, which is why it is largest at the most expensive tier.

    A provider that reports no cached count yields 0, which prices the run exactly as
    before. Nothing here depends on the field existing.
    """
    usage = getattr(response, "usage_details", None) or {}
    return {
        "prompt_tokens":     int(usage.get("input_token_count") or 0),
        "completion_tokens": int(usage.get("output_token_count") or 0),
        "total_tokens":      int(usage.get("total_token_count") or 0),
        "cached_tokens":     _cached_tokens(usage),
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

# Second bridge, same reason as _sub_agent_bucket: topology code that needs the live
# RunRecorder ITSELF (not just its usage bucket) -- currently only Dynamic-Graph, whose
# participant/manager agents are constructed before the recorder's tool-call-wrapping
# middleware can be attached the normal way, and need agent_completion_middleware() from
# the same recorder instance execute_topology.py is already tracking against.
_active_recorder: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "_active_recorder", default=None
)

# Third bridge (T114 / Risk Log R50, 30-Aug-26). Identifies WHICH tool_call the code
# currently executing belongs to, so a sub-agent's usage can be correlated to its own
# tool_call row by identity instead of by (tool_name, completion order).
#
# Why identity is needed: recorder.tool_calls is in START order (middleware fires on
# entry) while recorder.sub_agent_usage is in COMPLETION order. Pairing them by name
# and position is correct only while, for any single tool name, starts and completions
# happen in the same order. Two CONCURRENT calls of the SAME tool break that, and each
# call is then credited with the other's tokens. Verified by simulation before the fix:
# two concurrent diagnose calls costing 200 and 999 tokens paired as 999 and 200.
# Run totals stay correct (a sum is order-independent), so the error is invisible in
# every summary figure while every per-call cost is wrong -- the same silent-corruption
# shape as the positional-zip bug this file's writer already documents.
#
# No condition built before Mesh B could trigger it: none invokes the same specialist
# twice concurrently. Mesh B can, and duplicate peer invocation is an EXPECTED cost of
# a mesh (the literature's O(n^2) coordination cost), i.e. exactly the thing it must
# measure accurately. Set by RunRecorder.middleware._capture, read by
# record_sub_agent_usage; each concurrent branch gets its own contextvar copy, so
# nesting to any depth stays correctly scoped.
_current_call_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "_current_call_id", default=None
)

# Monotonic source of those ids. Process-wide rather than per-recorder because it only
# has to be unique WITHIN one run, and a plain counter avoids any chance of two records
# colliding on id() reuse after garbage collection.
_call_id_seq = itertools.count(1)


def get_active_recorder() -> Any | None:
    """The RunRecorder tracking the current master.run() call, or None outside one
    (e.g. a standalone/dev call). See _active_recorder above."""
    return _active_recorder.get()


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
        usage = extract_usage(response) if response is not None else {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }
        # call_id ties this usage to the tool_call it came from, by identity rather
        # than by name+order (T114 / R50 -- see _current_call_id above). None when a
        # sub-agent is run outside any tool_call (Sequential's planning call), which
        # the writer handles by falling back to the original name-queue pairing.
        bucket.append({"tool_name": tool_name, "messages": _message_count(response),
                       "call_id": _current_call_id.get(), **usage})


def _message_count(response: Any) -> int:
    """How many messages the sub-agent's own run produced.

    A specialist's prompt_tokens grow with the number of internal round-trips it made,
    because each one re-sends the conversation so far. Token count alone cannot tell a
    single large prompt from several ordinary ones repeated -- and that is exactly the
    open question in R36, where one simulate call cost 67,857 prompt tokens against a
    normal 7,047 with the same output, duration and completion tokens. Recording the
    message count makes the two cases distinguishable at the point they happen.
    """
    return len(getattr(response, "messages", None) or [])


def serialize_agent_value(result: Any) -> str:
    """Serialise a sub-agent AgentResponse's .value the one way this codebase does it
    everywhere a sub-agent's structured output must become a string: full JSON for a
    Pydantic model, str() for anything else (e.g. None, or a plain scalar)."""
    if isinstance(result.value, BaseModel):
        return result.value.model_dump_json()
    return str(result.value)


async def run_sub_agent(agent, tool_name: str, task: str) -> Any:
    """Run one domain sub-agent on `task` and record its token usage.

    The single implementation of "run an agent, record its usage", used wherever an
    agent is invoked outside a top-level Agent's own tool-calling loop (which
    function_middleware already captures): topologies/planner_executor.py's
    `_invoke_sub_agent`, wrapped as a @tool for the master, and topologies/sequential.py's
    capability-planning call, which is not a tool call at all.

    Returns the raw AgentResponse so callers can read .value directly -- serialising it
    into a string, when a caller needs one, is the separate serialize_agent_value()
    above, not folded in here, because not every caller wants a string back.

    Re-raises on any failure, after still recording a zeroed usage entry -- keeps
    sub_agent_usage aligned with tool_calls (a step that failed still needs a usage row, or
    any later apportionment logic that divides total usage across N calls miscounts N).
    """
    try:
        result = await agent.run(task)
    except Exception:
        record_sub_agent_usage(tool_name, None)
        raise
    record_sub_agent_usage(tool_name, result)
    u = extract_usage(result)
    print(f"     - {tool_name} agent: {u['prompt_tokens']:,} prompt / "
          f"{u['completion_tokens']:,} completion tokens over "
          f"{_message_count(result)} message(s)", flush=True)
    return result


async def run_agent_as_tool_call(agent, tool_name: str, task: str, middleware) -> Any:
    """Run a sub-agent and record it as a tool_call row, for topologies that invoke
    specialists from code instead of through an orchestrating Agent's tool loop.

    `middleware` is designed to wrap whatever call an Agent's own function-calling loop
    makes. A code-scheduled topology has no such loop, so this constructs a real
    FunctionInvocationContext and invokes the recorder's middleware directly — producing
    tool_call rows under the same canonical names, with the same timings, as conditions
    whose coordinator does the calling. Without it those conditions would record no tool
    calls at all and could not be compared with the rest.

    `_capture` reads only context.function.name, .arguments and .result, so a duck-typed
    stand-in for `function` is sufficient; nothing here depends on framework internals
    beyond what the middleware itself already relies on.

    Returns the sub-agent's AgentResponse. Exceptions propagate, matching how every
    other topology's failures reach the harness.
    """
    holder: dict = {}

    async def _invoke():
        result = await run_sub_agent(agent, tool_name, task)
        holder["result"] = result
        return result

    if not middleware:
        return await _invoke()   # no recorder attached (standalone/dev call)

    ctx = FunctionInvocationContext(
        function=SimpleNamespace(name=tool_name),
        arguments={"request": task},
    )

    async def _call_next():
        ctx.result = serialize_agent_value(await _invoke())

    # Compose the whole list, innermost last, the way an Agent's own function-calling
    # loop composes middleware. Only middleware[0] used to run, which is invisible while
    # the harness passes a single recorder but silently drops one of the two the Gradio
    # app passes (its UI-event capture alongside recorder.middleware).
    chain = _call_next
    for mw in reversed(middleware):
        def _link(_mw=mw, _next=chain):
            async def _call():
                await _mw(ctx, _next)
            return _call
        chain = _link()

    await chain()
    return holder["result"]


# @dataclass auto-generates __init__, repr, and comparison helpers so this
# stays a lightweight structured record type rather than a manual class.
@dataclass
class ToolCallRecord:
    name: str
    started_at: float
    call_id: int = 0            # identity for usage correlation (T114 / R50)
    ended_at: float = 0.0       # raw perf_counter, independent of duration_s rounding
    duration_s: float = 0.0
    input_chars: int = 0        # size of what the master forwarded input
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
            args = dict(context.arguments) if context.arguments else {}
            # Sum EVERY argument, not just "request". Reading only "request" silently
            # under-reported any tool taking more than one argument -- observed 23-Aug-26
            # run dbd1ad39, where Planner-Executor's recommendation_tool gained a second
            # `diagnosis_summary` parameter carrying ~15k chars of diagnosis and was still
            # recorded as 487 input chars, i.e. the request line alone. That understates
            # the coordinator's real payload and makes input_chars incomparable both
            # across tools and across topologies (Monolith passes the same diagnosis as a
            # raw-tool argument). Falls back to str(args) for a non-dict arguments object.
            if isinstance(args, dict):
                input_text = "".join(str(v) for v in args.values())
            else:
                input_text = str(args)
            t0 = time.perf_counter()
            record = ToolCallRecord(name=name, started_at=t0, input_chars=len(str(input_text)),
                                    call_id=next(_call_id_seq))
            self.tool_calls.append(record)
            print(f"  >> tool_start {name} (input={record.input_chars:,} chars)", flush=True)
            # Scope this call's id for anything running underneath it, so a sub-agent's
            # usage correlates to THIS tool_call and not to a same-named sibling running
            # concurrently (T114 / R50). reset() in finally keeps nesting correct: an
            # inner call restores the outer call's id on the way out.
            id_token = _current_call_id.set(record.call_id)
            try:
                # call_next() executes the actual tool implementation.
                await call_next()
            except Exception as e:
                record.ended_at = time.perf_counter()
                record.error = str(e)
                record.duration_s = round(record.ended_at - t0, 2)
                print(f"  << tool_end   {name} FAILED after {record.duration_s:.2f}s: {e}", flush=True)
                raise
            finally:
                _current_call_id.reset(id_token)
            record.ended_at = time.perf_counter()
            record.duration_s = round(record.ended_at - t0, 2)

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
            print(f"  << tool_end   {name} done in {record.duration_s:.2f}s "
                  f"(output={len(payload):,} chars, valid_json={record.valid_json})", flush=True)
        return _capture

    def agent_completion_middleware(self, tool_name: str, *, capture_artifact: bool):
        """@agent_middleware hook for an agent whose .run() calls are made by
        framework-internal code (MagenticAgentExecutor / StandardMagenticManager), not
        by this app's own tool-wrapping -- see the module docstring's third case.
        Attach at construction, same as self.middleware, so it fires on every call
        regardless of who makes it.

        Records usage every call, same shape record_sub_agent_usage() always writes.
        capture_artifact=True (a domain participant) additionally replaces any earlier
        tool_call row(s) sharing tool_name's canonical capability with a fresh one
        carrying this call's own serialized .value -- the participant's real structured
        answer, exactly what a wrapped @tool function's return value already is
        everywhere else, keyed so score_topology_run.py's existing extraction finds it
        with no changes on its side. Replacing rather than appending keeps exactly one
        row per capability: the participant's own internal raw pipeline-tool call
        (captured separately by self.middleware, kept for the console/timeline) would
        otherwise canonicalize to the same capability and silently outscore this one
        under score_topology_run.py's longest-payload tie-break, since a raw data dump
        is routinely longer than a synthesised narrative -- confirmed 29-Aug-26 reading
        that tie-break, not guessed. capture_artifact=False (the Magentic manager's own
        reasoning turns) records usage only, under a name that matches no real tool call
        so split_usage() buckets it as orchestration cost, not specialist cost -- same
        convention as Sequential's _COORDINATOR_USAGE.
        """
        cap = canonical(tool_name)

        @agent_middleware
        async def _capture(context: AgentContext, call_next):
            t0 = time.perf_counter()
            await call_next()
            response = context.result
            record_sub_agent_usage(tool_name, response)
            if capture_artifact and response is not None:
                self.tool_calls = [tc for tc in self.tool_calls if canonical(tc.name) != cap]
                t1 = time.perf_counter()
                self.tool_calls.append(ToolCallRecord(
                    name=tool_name, started_at=t0, ended_at=t1,
                    duration_s=round(t1 - t0, 2), input_chars=0,
                    payload=serialize_agent_value(response),
                    valid_json=isinstance(getattr(response, "value", None), BaseModel),
                ))

        return _capture

    # @contextmanager lets this method be used with `with ...:` syntax,
    # guaranteeing setup/teardown of the contextvar even on exceptions.
    @contextmanager
    def track_sub_agents(self):
        """Wrap the master.run(...) call(s) in this to have every
        record_sub_agent_usage() call during that scope land in
        self.sub_agent_usage, and get_active_recorder() (inside that scope) return
        this recorder."""
        token = _sub_agent_bucket.set(self.sub_agent_usage)
        recorder_token = _active_recorder.set(self)
        try:
            yield
        finally:
            _sub_agent_bucket.reset(token)
            _active_recorder.reset(recorder_token)

    # Compact per-tool summary for UI/logging/reporting layers.
    def summary(self) -> list[dict]:
        return [
            {"tool_name": tc.name, "duration_s": tc.duration_s,
             "input_chars": tc.input_chars, "payload_chars": len(tc.payload),
             "valid_json": tc.valid_json,
             "error": tc.error,
             "call_id": tc.call_id,   # usage correlation key (T114 / R50)
             # Offsets from the first tool start. Absolute perf_counter values are
             # meaningless across processes; relative ones are all the dependency
             # check needs, and they survive serialisation into the run store.
             #
             # ended_offset_s is derived from tc.ended_at, NOT started_offset + duration_s:
             # duration_s is rounded to 2dp for display, and adding a pre-rounded number to
             # a 3dp-precise started_offset_s produced a spurious few-millisecond gap that
             # the dependency checker (measurement/dependencies.py) read as a real
             # premature-start violation. Confirmed against run de135917 (29-Aug-26):
             # predict's rounded end printed as 40.36s while simulate's independently
             # measured start was 40.359s -- one millisecond apart, not a real violation,
             # but 40.359 < 40.36 tripped the check. ended_at removes the mismatch by
             # measuring both ends of every comparison the same way.
             "started_offset_s": round(tc.started_at - self._first_start(), 3),
             "ended_offset_s": round(tc.ended_at - self._first_start(), 3)}
            for tc in self.tool_calls
        ]

    def _first_start(self) -> float:
        """perf_counter of the earliest tool start, or 0.0 when nothing ran."""
        return min((tc.started_at for tc in self.tool_calls), default=0.0)

    # Aggregate token usage across all sub-agent invocations in this run.
    def total_sub_agent_usage(self) -> dict:
        total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for u in self.sub_agent_usage:
            for k in total:
                total[k] += u.get(k, 0)
        return total

    def split_usage(self) -> tuple[dict, dict]:
        """Separate specialist usage from orchestration usage.

        An entry whose tool_name matches no captured tool call is not a specialist —
        it is a coordinator turn a topology drove itself and recorded here because it
        never reaches the harness's `responses` list. Sequential produces one per
        planned step, and they outweigh its specialist cost several times over, so
        summing sub_agent_usage as if every entry were a specialist misattributes the
        bulk of that condition's tokens.

        Returns (specialist_usage, orchestration_usage), both in the flat
        prompt/completion/total shape. Topologies making no such turns get zeros for
        the second.
        """
        tool_names = {tc.name for tc in self.tool_calls}
        specialist = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        orchestration = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for u in self.sub_agent_usage:
            bucket = specialist if u.get("tool_name") in tool_names else orchestration
            for k in bucket:
                bucket[k] += u.get(k, 0)
        return specialist, orchestration

    # Aggregate total time spent in all tool calls in this run.
    def total_tool_time(self) -> float:
        return round(sum(tc.duration_s for tc in self.tool_calls), 2)

    def tool_wall_span_s(self) -> float:
        """Wall-clock span covered by tool execution: last end minus first start.

        Differs from summing durations as soon as tools run concurrently — the sum
        double-counts overlap and can exceed the run's own wall time. Use this when
        apportioning run time between orchestrator and tools.
        """
        if not self.tool_calls:
            return 0.0
        first_start = min(tc.started_at for tc in self.tool_calls)
        last_end = max(tc.ended_at for tc in self.tool_calls)
        return round(max(0.0, last_end - first_start), 2)
    
    # Imperative alternative to track_sub_agents() for call sites where a
    # `with` block would require reindenting a large existing block (e.g.
    # deep inside an async generator). Must be paired with stop_tracking().
    def start_tracking(self):
        self._token = _sub_agent_bucket.set(self.sub_agent_usage)
        self._recorder_token = _active_recorder.set(self)

    def stop_tracking(self):
        _sub_agent_bucket.reset(self._token)
        _active_recorder.reset(self._recorder_token)

