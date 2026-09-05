"""Single source of truth for which topologies exist and how each is built.

Every topology name used anywhere — CLI argument, run-store `run.topology` column,
prompts/coordinators/<name>/ directory — comes from here, so the three can never
disagree. Previously the name was a literal in two places (`execute_topology.py` imported the
planner-executor agent directly and separately passed topology="planner_executor" to
write_run), which meant a run could in principle record a topology it did not execute.

BUILT holds the topologies whose orchestration code exists today. The remaining names
are declared with builder=None so the registry documents the full experimental design
while `resolve()` fails loudly rather than silently falling back to planner-executor.
"""
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class TopologySpec:
    name: str                       # canonical name: CLI, run.topology, coordinator dir
    coordinator_key: str            # prompt file stem inside coordinators/<name>/
    builder: Optional[Callable]     # returns the agent/graph to execute; None = not built
    note: str = ""

    @property
    def is_built(self) -> bool:
        return self.builder is not None


def _build_monolith():
    from topologies.monolith import build_master
    return build_master()


def _build_planner_executor():
    # Imported lazily: constructing the agent pulls in the MCP tool stack, which we
    # do not want to pay for when the registry is only being inspected.
    from topologies.planner_executor import build_master
    return build_master()


def _build_sequential():
    # Imported lazily for the same reason as the other topologies above.
    from topologies.sequential import build_master
    return build_master()


def _build_static_graph_dag():
    # Imported lazily for the same reason as the other topologies above.
    from topologies.static_graph_dag import build_master
    return build_master()


def _build_dynamic_graph():
    # Imported lazily for the same reason as the other topologies above.
    from topologies.dynamic_graph import build_master
    return build_master()


def _build_static_graph_routed():
    # Imported lazily for the same reason as the other topologies above.
    from topologies.static_graph_routed import build_master
    return build_master()


def _build_mesh():
    # Imported lazily for the same reason as the other topologies above.
    from topologies.mesh import build_master
    return build_master()


def _build_swarm():
    # Imported lazily for the same reason as the other topologies above.
    from topologies.swarm import build_master
    return build_master()


def _build_swarm_constrained_adaptive():
    # Imported lazily for the same reason as the other topologies above.
    from topologies.swarm_constrained_adaptive import build_master
    return build_master()


REGISTRY: dict[str, TopologySpec] = {
    "planner_executor": TopologySpec(
        "planner_executor", "master", _build_planner_executor,
        "Master plans and delegates to 5 agent-as-tools. T32 — built."),
    "monolith": TopologySpec(
        "monolith", "master", _build_monolith,
        "One LLM context, raw tools attached directly, domain prompts inlined. T37 — built."),
    "sequential": TopologySpec(
        "sequential", "coordinator", _build_sequential,
        "LLM plans the ordered capability list; a master carrying only those tools runs "
        "them one forced tool_choice turn each. T38 — built, not yet smoke-tested."),
    "static_graph_dag": TopologySpec(
        "static_graph_dag", "coordinator", _build_static_graph_dag,
        "MAF WorkflowBuilder over TRUE_DEPENDENCIES (fan-out/fan-in); a tools-off "
        "triage call gates the graph (refuse/inform/proceed only — scope and order "
        "stay fixed in code), a tools-off aggregator closes. T39 — built."),
    "swarm": TopologySpec(
        "swarm", "master", _build_swarm,
        "Pure emergent agent architecture, rebuilt 30-Aug-26 (T99). A seed call names "
        "only the capabilities that can start with nothing posted yet "
        "(WavePlan.needed_capabilities, wave 1 only, structured, no free-text "
        "resolver). Every capability after that is requested directly by whichever "
        "specialist produces the output it depends on, via request_specialist -- no "
        "dependency table is consulted anywhere in this module; timing is entirely "
        "agent-decided. Ready-in-a-wave specialists run concurrently (asyncio.gather + "
        "run_agent_as_tool_call); every specialist MUST post to a shared, "
        "Pydantic-typed blackboard (write-compliance measured, not guaranteed). "
        "MasterOutput assembled in code from the blackboard's own notes, no covering "
        "aggregator agent (R17). Four live runs on the same query (Q10, run_store "
        "run_n 1-4) exercise this exact mechanism: n=1-2 show premature-start "
        "dependency violations, n=3-4 show diagnose never requesting recommend -- "
        "recorded as findings for this condition (see "
        "topologies/swarm_constrained_adaptive.py and "
        "plan/Thesis_Project_Tracker.xlsx, Topology Comparison rows 11-12), not "
        "defects awaiting a further prompt fix."),
    "swarm_constrained_adaptive": TopologySpec(
        "swarm_constrained_adaptive", "master", _build_swarm_constrained_adaptive,
        "Split from plain Swarm 30-Aug-26 (T99) after the four runs described in "
        "swarm's own note above. Same specialist-construction shape and blackboard as "
        "plain Swarm; the seed call lists EVERY capability the request needs "
        "(WavePlan.needed_capabilities, any order, not just wave 1). Each wave, code "
        "checks that set against measurement/dependencies.py's own TRUE_DEPENDENCIES "
        "table (the same table used to JUDGE every other topology's dependency order) "
        "and starts whatever is actually ready -- WHEN a capability runs is a code "
        "decision here, not agent-decided. Ready capabilities in a wave run "
        "concurrently (asyncio.gather + run_agent_as_tool_call -- MAF's "
        "ConcurrentBuilder was considered but its result-correlation/middleware "
        "attachment were not verified against a live run in time for this build); "
        "every specialist MUST post to the blackboard (write-compliance measured) and "
        "MAY request a genuinely unforeseen specialist via request_specialist -- "
        "untested, zero occurrences across the runs observed. MasterOutput assembled "
        "in code, no covering aggregator agent (R17). Three live runs (Q10 run_n=5, "
        "Q6, Q5) confirm clean scheduling with no dependency violations and nothing "
        "missing -- n=3, preliminary. An optional, alternative agent-construction path "
        "(swarm_codegen.py: an LLM writes each agent's Python module from fixed "
        "ground-truth facts) was built standalone and never wired into either Swarm "
        "variant; deleted 30-Aug-26 as unused."),
    "mesh": TopologySpec(
        "mesh", "entry_point", _build_mesh,
        "Mesh B — concurrent peer-to-peer messaging, no coordinator and no owner. A "
        "WorkflowBuilder COMPLETE peer graph (n(n-1) = 20 directed edges, every pair "
        "connected both ways, deliberately NOT derived from TRUE_DEPENDENCIES — that "
        "would have been the DAG's own skeleton and would have made lateral routing "
        "unmeasurable by construction); each node runs its capability, decides for itself which "
        "peers to address, and messages them — the receiver acts on its own account and "
        "never replies, so nobody retains responsibility. Conditional edges make routing "
        "agent-decided while the framework schedules addressed peers concurrently, on "
        "the same engine as static_graph_dag: compiled edges vs agent-decided messaging, "
        "everything else held constant. Three narrative peers meet the same output "
        "contract without any agent gaining a global view; MasterOutput is assembled in "
        "code after the graph goes idle. Fixed entry at predict (entry, not ownership), "
        "shared hop budget and a re-entry cap enforced in code because the graph is "
        "deliberately cyclic. Deliberately NOT agent-as-tools (which MAF's own docs "
        "classify as delegation, where the caller retains responsibility) and NOT "
        "HandoffBuilder (sole ownership, forbids multiple tool calls) — that variant is "
        "tracked separately as Mesh A. T40 — built, not yet smoke-tested."),
    "dynamic_graph": TopologySpec(
        "dynamic_graph", "coordinator", _build_dynamic_graph,
        "MagenticBuilder ledger-driven; a tools-off triage call gates the workflow "
        "(refuse/inform/proceed only, no plan stated — the ledger decides scope and "
        "order), a tools-off aggregator converts the ledger's plain-text final answer "
        "into MasterOutput. T87 (optional) — built."),
    "static_graph_routed": TopologySpec(
        "static_graph_routed", "coordinator", _build_static_graph_routed,
        "MAF WorkflowBuilder over TRUE_DEPENDENCIES, same fan-out/fan-in shape as "
        "static_graph_dag (all five nodes always fire); a tools-off router call gates "
        "the workflow (refuse/inform/proceed AND scope+order selection), each node "
        "skips internally when its capability was not selected, a tools-off aggregator "
        "writes MasterOutput from whichever capabilities actually ran. T108 — built, "
        "promoted to CORE 29-Aug-26 on evidence from its first 3 live runs (router "
        "selection matched implied_tools_json exactly on Q6/Q5/Q10)."),
}

# Names accepted by run.topology in the run store — keep in sync with run_store_schema.py
VALID_TOPOLOGIES = tuple(REGISTRY)


def resolve(name: str) -> TopologySpec:
    """Return the spec for *name*, or raise with an actionable message."""
    if name not in REGISTRY:
        raise ValueError(
            f"Unknown topology {name!r}. Valid: {', '.join(VALID_TOPOLOGIES)}"
        )
    spec = REGISTRY[name]
    if not spec.is_built:
        raise NotImplementedError(
            f"Topology {name!r} has prompts at coordinators/{name}/ but no orchestration "
            f"code yet ({spec.note}). Built topologies: "
            f"{', '.join(n for n, s in REGISTRY.items() if s.is_built)}"
        )
    return spec
