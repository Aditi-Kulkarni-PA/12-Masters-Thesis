# Topology Reference — the nine conditions

[← Documentation index](../README.md)

The nine orchestration conditions compared by this study. They differ in **who decides
what runs and when**, not in what capabilities exist: all nine draw on the same five
tool-backed capabilities, the same prompts, the same data and the same model.

---

## 1. What is held constant

| Held fixed across all nine | Where it is defined |
|---|---|
| The five capabilities | `core/agents.py`, `core/tool_descriptions.py` |
| Domain prompt text per capability | `config/prompts/agents/*.md` |
| Model and generation settings | `core/clients.py`; asserted by `check_model_parity.py` |
| Tools and the MCP server | `core/mcp_tools.py`, `tools/` |
| Query set | `query_metadata` table, frozen |
| Output contract | `config/prompts/shared/output_contract.md` |
| Evaluation rubric | `score_topology_run.py` |

What varies is the coordination policy alone. Any instruction a condition needs in order
to implement its own coordination is documented and reviewed, and generation-parameter
parity is checked before every batch.

`core/agents.py` and `core/clients.py` are factory functions, not one-off construction —
how they came to be shared across nine independent topology modules, replacing a single
hardcoded master, is in
[architecture/topology-modularization.md](../architecture/topology-modularization.md).

---

## 2. The five capabilities and their true dependencies

Dependency here means *this tool consumes that tool's output as an argument*. It is a
fact about the tool signature, not an orchestration choice, and it is the same table used
to judge dependency order in every condition (`measurement/dependencies.py`).

| Capability | Requires |
|---|---|
| `predict_delivery_delays_tool` | — |
| `diagnose_delay_patterns_tool` | predict |
| `delay_simulations_tool` | predict |
| `email_alert_tool` | predict |
| `recommendation_tool` | predict, diagnose |

---

## 3. The nine conditions

Listed in the order used in every report and table.

|  # | Condition                      | Who/what decides execution?                                    | Scope of execution                                                                   | Execution order                                 | Context handling                                                                                                                                                | Module                          |
| -: | ------------------------------ | -------------------------------------------------------------- | ------------------------------------------------------------------------------------ | ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- |
|  1 | **Monolith**                   | Single agent                                                   | Agent implicitly determines required capabilities (tools)                            | Single agent context                            | All reasoning, tool calls, and results remain in one continuous agent context                                                                                   | `monolith.py`                   |
|  2 | **Sequential**                 | Planner                                                        | Planner determines the complete task sequence                                        | Fixed sequence — one tool per turn              | Each agent receives the relevant output/context from the preceding agent                                                                                        | `sequential.py`                 |
|  3 | **Planner–Executor**           | Planner                                                        | Planner produces a task list for the executor, handling concurrency and dependencies | Executor follows planned order                  | Planner context is converted into executor tasks; execution results are accumulated through the execution flow                                                  | `planner_executor.py`           |
|  4 | **Static Graph DAG**           | Code-defined graph                                             | All nodes in the fixed graph, each time                                              | Dependency levels and graph-defined parallelism | Context is passed explicitly along graph edges; each node receives its upstream inputs rather than a continuously shared conversation                           | `static_graph_dag.py`           |
|  5 | **Static Graph Routed**        | Intent router                                                  | Router selects relevant paths in the fixed graph                                     | Graph-defined order for selected paths          | Context follows the selected graph paths; each node receives the inputs/results supplied by its upstream nodes                                                  | `static_graph_routed.py`        |
|  6 | **Dynamic Graph**              | Manager                                                        | Manager selects the next capability at runtime, one at each step                     | Re-decided after each step                      | Manager reconstructs a ledger/context for each turn; accumulated ledger is included in subsequent manager prompts                                               | `dynamic_graph.py`              |
|  7 | **Mesh**                       | Peer agents                                                    | Each agent decides whether and where to hand off                                     | Peer-to-peer, on receipt                        | Accumulated peer results are threaded into the context passed to subsequent peer tasks                                                                          | `mesh.py`                       |
|  8 | **Swarm**                      | Seed plan + participating specialists                          | Specialists dynamically execute assigned capabilities                                | Decided by participating specialists            | Each specialist receives its assigned task/context; task context is established when the task is created rather than through a continuously shared conversation | `swarm.py`                      |
|  9 | **Swarm Constrained Adaptive** | Seed plan + participating specialists + dependency rule checks | Seed plan identifies required capabilities; code enforces dependencies               | Adaptive within dependency constraints          | Each specialist receives its assigned task/context; context is passed through the shared task state as execution progresses                                     | `swarm_constrained_adaptive.py` |


### The two controlled contrasts

Two pairs differ in exactly one variable, which is what makes them interpretable:

| Pair | Variable isolated |
|---|---|
| Static Graph DAG vs Static Graph Routed | intent gating — same graph, with and without a router |
| Swarm vs Swarm Constrained Adaptive | code-enforced timing — same agents, blackboard and tools; only *when* a ready capability starts differs |

Because only one design variable changes within each pair, differences in outcomes can be attributed more directly to that variable, whereas comparisons across all nine conditions involve multiple mechanisms changing simultaneously..

---

## 4. Condition-specific limits

Each is a deliberate bound, not tuning. They are documented because they shape what a
condition can do and therefore what its measures mean.

| Condition | Limit | Why it exists |
|---|---|---|
| Mesh | `MAX_RUNS_PER_CAPABILITY = 2` | a topology with no coordinator can re-address a peer indefinitely; duplicate work is a measured cost, not an error to hide |
| Swarm | `MAX_RUNS_PER_CAPABILITY = 2`, `MAX_WAVES = 5` | same reasoning, plus a backstop on total waves |
| Swarm Constrained Adaptive | as Swarm | identical bounds so the pair stays comparable |
| Sequential | `max_function_calls = 1` | required, not tuning: it is what forces one tool per turn, which is the condition being modelled |

`MAX_WAVES` and Mesh's hop budget are **different units** and must not be compared.

---

## 5. Reading a condition's measures

Two conditions need interpretive care, and both are documented in
[`../validation/known-limitations.md`](../validation/known-limitations.md):

- **Monolith** attaches raw tools rather than wrapping them as sub-agents, so its
  orchestration and specialist token shares are `0.000` and its critical path is near
  zero. These are correct: its work is not separable into coordination and execution.
- **Swarm and Swarm Constrained Adaptive** have no coordinator turn that writes a
  narrative summary, so the three narrative fields are empty by construction and are
  excluded from the quality blend rather than scored zero.

---

## 6. How they actually behave

The mechanics that only became visible from building and running these conditions —
context passing, framework behaviours confirmed from source, turn handling — are in
[how-they-actually-work.md](how-they-actually-work.md). Read it before interpreting any
per-condition measure.

The "Domain prompt text" and "Output contract" rows in §1 name the files; how those files
actually reach all nine conditions — the `@include` mechanism, the full partial diff per
topology, and the two controlled contrasts confirmed at the prompt level, not just in
code — is in [prompt-modularization.md](prompt-modularization.md).

## 7. Per-condition detail

Each module's own docstring is the authoritative description of its mechanism, including
the design history and any correction made to it. They are long and deliberately so.

| Condition | Source |
|---|---|
| Monolith | `topologies/monolith.py` |
| Sequential | `topologies/sequential.py` |
| Planner-Executor | `topologies/planner_executor.py` |
| Static Graph DAG | `topologies/static_graph_dag.py` |
| Static Graph Routed | `topologies/static_graph_routed.py` |
| Dynamic Graph | `topologies/dynamic_graph.py` |
| Mesh | `topologies/mesh.py` |
| Swarm | `topologies/swarm.py` |
| Swarm Constrained Adaptive | `topologies/swarm_constrained_adaptive.py` |

---

[← Documentation index](../README.md)
