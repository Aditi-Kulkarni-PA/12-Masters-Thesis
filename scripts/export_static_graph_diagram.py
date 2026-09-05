"""Export the Static-Graph DAG's actual structure as a Mermaid diagram (T39 follow-up).

Builds the same Workflow object _build_workflow() assembles for a real run -- no
specialists are invoked, only the graph structure -- and renders it with the
framework's own WorkflowViz, so the figure can never drift from what the code executes.
The structure is fixed (derived from TRUE_DEPENDENCIES, not the query), so this only
needs to be re-run when that table changes.

Usage:
    uv run python scripts/export_static_graph_diagram.py
Prints Mermaid source to stdout -- paste the fenced block into
config/prompts/coordinators/static_graph_dag/README.md.
"""
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "supply_chain_topology_app"
sys.path.insert(0, str(APP_DIR))

from agent_framework import WorkflowViz
from topologies.static_graph_dag import _build_workflow, _RunContext

workflow = _build_workflow(_RunContext("", None))
print(WorkflowViz(workflow).to_mermaid())
