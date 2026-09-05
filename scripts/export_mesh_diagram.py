"""Export Mesh B's actual peer graph as a Mermaid diagram (T40 follow-up).

Same discipline as scripts/export_static_graph_diagram.py: builds the real Workflow object
_build_workflow() assembles for a run -- no agent is invoked, only the graph structure --
and renders it with the framework's own WorkflowViz, so the figure in the README can never
drift from what the code executes.

Mesh's structure is fixed and query-independent (the COMPLETE peer graph from
_peer_edges()), so this only needs re-running if that function changes.

Two things the rendered graph deliberately will NOT show, worth knowing before reading it:

  1. Every edge carries a condition testing whether its target was named by the sending
     node's routing decision. The graph shows which peers CAN address each other; which
     ones actually do is decided per run by each node and is the thing under measurement.
     A fully-connected diagram is therefore the starting point, not the observed
     behaviour -- read a run's `sender -> receiver` trace lines for what really happened.

  2. The three narrative peers (simulate_summary, recommend_summary, email_summary) are
     not nodes in this graph. They are invoked inside their own capability's node, see
     only that one capability's output, and never message anyone -- which is why they are
     not a hub (R17). --peers below prints them alongside the edge list for completeness.

Usage:
    uv run python scripts/export_mesh_diagram.py            # Mermaid, for the README
    uv run python scripts/export_mesh_diagram.py --peers    # edge list + shape summary
    uv run python scripts/export_mesh_diagram.py --both

Prints to stdout -- paste the fenced block into
config/prompts/coordinators/mesh/README.md.
"""
import sys
from itertools import combinations
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "supply_chain_topology_app"
sys.path.insert(0, str(APP_DIR))

from topologies.mesh import (  # noqa: E402
    ENTRY_CAPABILITY, HOP_BUDGET, MAX_RUNS_PER_CAPABILITY, NARRATIVE_PEERS, PEER_EDGES,
)


def print_mermaid() -> None:
    """Render the real built Workflow through the framework's own visualiser."""
    from agent_framework import WorkflowViz          # noqa: E402
    from topologies.mesh import _RunContext, _build_agents, _build_workflow  # noqa: E402
    from config import get_instruction               # noqa: E402

    run = _RunContext(query="", middleware=None)
    agents = _build_agents(get_instruction("entry_point", topology="mesh"))
    print(WorkflowViz(_build_workflow(agents, run)).to_mermaid())


def print_peers() -> None:
    """Edge list and shape summary, derived from PEER_EDGES itself.

    Framework-free on purpose: PEER_EDGES is plain Python, so this half runs even without
    a working venv, and it is what verifies the completeness claim the README makes.
    """
    caps = sorted(PEER_EDGES)
    directed = [(s, t) for s in caps for t in PEER_EDGES[s]]
    pairs = {tuple(sorted(p)) for p in combinations(caps, 2)}
    connected = {tuple(sorted(e)) for e in directed}
    n = len(caps)

    print(f"nodes                 {n}  ({', '.join(caps)})")
    print(f"entry                 {ENTRY_CAPABILITY}  (entry only -- not ownership)")
    print(f"directed edges        {len(directed)}   expected n(n-1) = {n * (n - 1)}")
    print(f"pairs connected       {len(connected)}/{len(pairs)}")
    print(f"complete graph        {connected == pairs}")
    print(f"all bidirectional     {all(a in PEER_EDGES[b] and b in PEER_EDGES[a] for a, b in connected)}")
    print(f"missing pairs         {sorted(pairs - connected) or 'none'}")
    print(f"hop budget            {HOP_BUDGET} shared across all peers, enforced in code")
    print(f"re-entry cap          {MAX_RUNS_PER_CAPABILITY} runs per capability")
    print()
    print("peer edges (who each node MAY address; who it DOES is decided per run):")
    for c in caps:
        print(f"   {c:10} -> {', '.join(PEER_EDGES[c])}")
    print()
    print("narrative peers (not graph nodes -- invoked inside their own capability,")
    print("see only that capability's output, message nobody):")
    for cap, key in sorted(NARRATIVE_PEERS.items()):
        print(f"   {cap:10} -> {key}")


if __name__ == "__main__":
    args = set(sys.argv[1:])
    if args & {"--peers", "--both"}:
        print_peers()
    if args & {"--both"}:
        print()
    if not args or args & {"--mermaid", "--both"}:
        print_mermaid()
