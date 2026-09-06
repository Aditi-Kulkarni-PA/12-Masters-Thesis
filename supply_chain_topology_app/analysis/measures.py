"""Run-level derived measures for the topology comparison.

Turns one stored run into the set of per-run values the aggregation layer needs.
Every value here is derived from the run store. Nothing is recomputed from logs and
no run is re-executed.

Measures produced:

    capability_coverage    required capabilities executed / required capabilities.
                           Detects required work that was omitted.
    capability_precision   required capabilities executed / total capabilities
                           executed. Detects work executed without being required.
    has_violation          1 when the run contains a dependency-order violation.
    completed              1 when run_status is 'success'.
    declined               1 when the run made no capability calls. Meaningful for the
                           out-of-scope probe, where declining is the correct outcome.
    orchestration_tax      orchestration cost / total cost.

Scheduling values (scheduling_ratio, scheduling_deviation, infeasible_overlap) are
read from the run row, where backfill_scheduling.py stores them.

A measure that cannot exist for a run is None rather than 0. Coverage is undefined
when a query requires no capabilities. Precision is undefined when a run executed
none. Treating either as 0 would score a correct decline as a failure.
"""

import json

from measurement.dependencies import TRUE_DEPENDENCIES, canonical

# The five real capabilities. A tool_call row whose canonical name falls outside this
# set is a narrative continuation of another capability rather than an independently
# schedulable step, and is excluded from every capability count.
CAPABILITIES = frozenset(TRUE_DEPENDENCIES)


def executed_capabilities(tool_names: list[str]) -> set[str]:
    """The distinct capabilities a run actually executed.

    Repeated calls to one capability collapse to a single entry: the question these
    measures answer is which capabilities ran, not how many times each one ran.
    """
    return {canonical(n) for n in tool_names} & CAPABILITIES


def required_capabilities(implied_tools_json: str | None) -> set[str]:
    """The capabilities a query requires, taken from the frozen query metadata."""
    if not implied_tools_json:
        return set()
    return {canonical(n) for n in json.loads(implied_tools_json)} & CAPABILITIES


def coverage(required: set[str], executed: set[str]) -> float | None:
    """Share of required capabilities that ran. None when the query requires none."""
    if not required:
        return None
    return len(required & executed) / len(required)


def precision(required: set[str], executed: set[str]) -> float | None:
    """Share of executed capabilities that were required. None when none executed."""
    if not executed:
        return None
    return len(required & executed) / len(executed)


def cost_split(run_row: dict, specialist_cost: float) -> dict:
    """Absolute cost by group, and the concurrency measures, for one run.

    The proposal lists specialist, orchestration and master cost as raw measures in
    Section 7.2.1 alongside their token counts. Only the token shares were derived
    before, so the cost anatomy could be read as proportions but never in dollars.

    Achievable concurrency is how much overlap the dependency graph permits: the fully
    serial duration divided by the critical path. A value of 1.0 means the graph is a
    chain and permits no overlap at all. Actual concurrency is the same numerator over
    the span the run actually took, so it is what the topology realised. The gap between
    the two is unused parallelism.
    """
    def val(key):
        return run_row.get(key) or 0

    critical = run_row.get("critical_path_s")
    span = run_row.get("actual_span_s")
    serial = run_row.get("fully_serial_s")

    return {
        "specialist_cost_usd": specialist_cost or None,
        "orchestration_cost_usd": run_row.get("orchestration_cost_usd"),
        "master_cost_usd": run_row.get("master_cost_usd"),
        "critical_path_s": critical,
        "actual_span_s": span,
        "coordinator_idle_s": run_row.get("coordinator_idle_s"),
        "achievable_concurrency": (serial / critical) if (serial and critical) else None,
        "actual_concurrency": (serial / span) if (serial and span) else None,
    }


def token_split(run_row: dict, specialist_prompt: int, specialist_completion: int) -> dict:
    """Prompt and generated token counts for one run, with the coordination split.

    A run's total is the sum of three groups: the specialist calls recorded in
    tool_call, the orchestration turns a coordinator makes on its own, and the master
    turns. Only the combined total is stored on the run row, so the prompt and generated
    halves are rebuilt here from the three groups. The reconstruction reproduces the
    stored grand_total_tokens exactly.

    Generated tokens are reported separately from prompt tokens because they are priced
    several times higher. Two runs with the same total can therefore differ
    substantially in cost, and a single total figure hides that.
    """
    def val(key):
        return run_row.get(key) or 0

    prompt = val("master_prompt_tokens") + val("orchestration_prompt_tokens") + specialist_prompt
    generated = (val("master_completion_tokens") + val("orchestration_completion_tokens")
                 + specialist_completion)
    total = prompt + generated

    specialist_total = specialist_prompt + specialist_completion
    orchestration_total = val("orchestration_total_tokens")
    master_total = val("master_total_tokens")

    return {
        "total_tokens": total or None,
        "generated_tokens": generated or None,
        "prompt_tokens": prompt or None,
        # Absolute counts per group, alongside the shares below. The proposal lists
        # both the tokens and the cost for each group as raw measures (Section 7.2.1).
        "specialist_tokens": specialist_total or None,
        "orchestration_tokens": orchestration_total or None,
        "master_tokens": master_total or None,
        # Cost anatomy: what share of the run's tokens each group consumed. The
        # orchestration share is the token form of the orchestration tax.
        "specialist_token_share": (specialist_total / total) if total else None,
        "orchestration_token_share": (orchestration_total / total) if total else None,
        "master_token_share": (master_total / total) if total else None,
    }


def derive(run_row: dict, tool_names: list[str], implied_tools_json: str | None,
           specialist_prompt: int = 0, specialist_completion: int = 0,
           specialist_cost: float = 0.0) -> dict:
    """Build the full set of derived measures for one run.

    *run_row* is the stored run record. *tool_names* are its tool_call names in call
    order. *implied_tools_json* is the query's frozen required-capability list.
    *specialist_prompt* and *specialist_completion* are that run's tool_call token sums,
    and *specialist_cost* its tool_call cost sum.
    """
    required = required_capabilities(implied_tools_json)
    executed = executed_capabilities(tool_names)

    violations = json.loads(run_row.get("dependency_violations_json") or "[]")
    completed = run_row.get("run_status") == "success"
    did_not_complete = run_row.get("run_status") == "failed"

    # Scope-adjusted quality for a run that produced nothing is 0, not absent
    # (proposal Section 7.2.3.3). Leaving it absent would drop the run from the quality
    # average and let failing outright score better than completing poorly.
    scope_adj = run_row.get("judge_mean_scope_adj")
    if did_not_complete:
        scope_adj = 0.0

    # Orchestration tax compares coordination spend against the run's total spend.
    # None when either figure is absent, which is the case for topologies that make no
    # separately recorded coordination turns.
    total_cost = run_row.get("grand_total_cost_usd")
    orch_cost = run_row.get("orchestration_cost_usd")
    orch_tax = (orch_cost / total_cost) if (orch_cost is not None and total_cost) else None

    return {
        "run_id": run_row["run_id"],
        "model": run_row["model"],
        "topology": run_row["topology"],
        "query_id": run_row["query_id"],
        "run_n": run_row["run_n"],
        "batch_id": run_row.get("batch_id"),

        # Reliability
        "capability_coverage": coverage(required, executed),
        "capability_precision": precision(required, executed),
        "has_violation": 1 if violations else 0,
        "completed": 1 if completed else 0,
        # Declining requires the run to have finished and chosen to execute nothing. A
        # run that crashed also executed nothing, and counting it as a decline would
        # credit a failure as correct restraint.
        "declined": 1 if (not executed and not did_not_complete) else 0,

        # Cost, tokens and latency
        "cost_usd": run_row.get("grand_total_cost_usd"),
        "wall_time_s": run_row.get("wall_time_s"),
        "orchestration_tax": orch_tax,
        **token_split(run_row, specialist_prompt, specialist_completion),
        **cost_split(run_row, specialist_cost),

        # Scheduling, read from the columns backfill_scheduling.py maintains
        "scheduling_ratio": run_row.get("scheduling_ratio"),
        "scheduling_deviation": run_row.get("scheduling_deviation"),
        "infeasible_overlap": run_row.get("infeasible_overlap"),

        # Quality
        "judge_mean": run_row.get("judge_mean"),
        "judge_mean_scope_adj": scope_adj,

        # Kept for traceability when a cell needs checking by hand
        "required_capabilities": sorted(required),
        "executed_capabilities": sorted(executed),
    }
