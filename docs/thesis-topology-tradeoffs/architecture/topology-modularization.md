# Topology Modularization — from one hardcoded master to a pluggable registry

[← Documentation index](../README.md) · [Execution flow](execution-flow.md)

Before this study could compare nine orchestration conditions, the substrate had to stop
being one file that only knew how to build one of them. This document is the record of
that change — what the code looked like before, what it looks like now, and why the split
landed where it did. It is infrastructure history, not a topology's own behaviour;
[topology-reference.md](../topologies/topology-reference.md) and
[prompt-modularization.md](../topologies/prompt-modularization.md) cover what the nine
conditions actually do once this substrate exists under them.

---

## 1. Before: one file, one hardcoded orchestrator

The capstone's MAF replica (`delivery_agents.py`, 402 lines, commit `767e7b3`) did
everything in a single module: the LLM client (`_configure_llm_backend()`, inline OpenAI
vs. LM Studio branching), the MCP server connection (`pipeline_mcp`, constructed once at
import time), all five domain agents plus the fallback and formatting agents, and —
at the bottom of the file — one Python object:

```python
supply_chain_delivery_master_agent = Agent(...)
```

There was no parameter that selected a different coordination pattern, because none
existed to select. The file was not "planner-executor with an option to be something
else" — it was the single orchestrator the app had, ported verbatim from the OpenAI SDK
capstone.

---

## 2. What changed, and why it split where it did

Comparing nine conditions meant nine coordinators had to exist side by side, each free
to decide *what runs and when* differently, while calling the identical five capabilities
built with the identical client. That requirement decided the split — not a general
tidiness pass:

| Extracted to | From | Because |
|---|---|---|
| `core/clients.py` | inline `_configure_llm_backend()` | nine coordinators need the same client-construction logic; a client built nine separate times can drift, silently, exactly the risk `check_model_parity.py` exists to catch (decision D20) |
| `core/mcp_tools.py` | inline `pipeline_mcp` | one MCP server process, shared by reference, not re-spawned per topology |
| `core/agents.py` | inline `Agent(...)` calls | each domain agent became a `build_*_agent(client, ...)` **factory**, not a one-off construction — the same function call, given the same client, produces an equivalent agent for any topology that asks |
| `topologies/registry.py` + nine `topologies/*.py` modules | the single `supply_chain_delivery_master_agent` | a name-to-builder table (`TopologySpec`, `REGISTRY`, `resolve()`) that `cli/execute_topology.py` and the delivery app both resolve against, so a topology is chosen by string, not by which file happens to be imported |

Commits, in order: `86f7d14` moved the client and MCP config out first; `8382a27`
removed `delivery_agents.py` entirely once `core/agents.py` existed to replace it; `614c9d7`
landed all nine topology modules and `topologies/registry.py` together, including a
rewrite of `planner_executor.py` itself — the original hardcoded master became this
registry's first entry, not a special case sitting outside it. `planner_executor.py`
still exports the plain module-level object
(`supply_chain_delivery_master_agent = build_master()`) alongside the factory, which is
why `delivery_chat_app.py`'s import of that name never had to change across this whole
migration.

---

## 3. What this bought, and what it deliberately did not do

This is decision **D23** in [decision-log.md](../decisions/decision-log.md), and it is
downstream of an earlier one: D15 already commits this study to nine **independent**
topology modules over shared capabilities, rejecting a single parameterised orchestrator
on the grounds that a parameterised orchestrator would encode the comparison's own
conclusion into its shared control flow. This document is the mechanism that made D15
possible to satisfy — extracting the client, tools and domain agents into functions
nine independent modules could all call, so "independent module" did not have to mean
"reimplement the client and the five agents nine times."

**What it did not do:** make the client or the domain agents configurable *per
topology*. Every `topologies/*.py` module imports the same `core.clients.chat_client`
and the same `core.agents.*` objects — the factories exist so construction happens once,
correctly, not so each topology can quietly diverge. `check_model_parity.py` asserts this
before every batch; drift there would be a build defect, not a finding, under this
study's own classification rule.

---

## 4. Why this is a thesis document, not a capstone one

[`docs/supply-chain-app/`](../../supply-chain-app/) is the capstone's own frozen record —
it describes `delivery_agents.py` as it existed at the time, and correctly does not
mention `core/`, `topologies/`, or this migration, because none of it existed yet when
those documents were written. This split happened *after* the capstone, specifically to
make the topology comparison possible, so it belongs in the thesis documentation that
explains how the comparison is built — not retrofitted into the capstone's history as if
it had always been there.

---

[← Documentation index](../README.md) · [Execution flow](execution-flow.md)
