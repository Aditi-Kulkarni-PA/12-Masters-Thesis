# Constrained Adaptive Swarm

Split from plain Swarm 30-Aug-26 (T99). Written fresh rather than copied from Swarm's
own README, which describes the abandoned dispatcher design and a since-deleted
codegen path -- see `coordinators/swarm/README.md` for that condition's own history.

## What this is

Same emergent agent-spawning mechanism as plain Swarm, with one difference: WHEN a
needed capability runs is decided by code, not by a specialist's own free-text
`request_specialist` call.

- A seed call (`master.md`) decomposes the request into the full set of capabilities
  needed (`WavePlan.needed_capabilities`) -- scope only, no ordering.
- Each wave, `topologies/swarm_constrained_adaptive.py`'s `_prerequisites_met()` checks
  that set against `measurement/dependencies.py`'s `TRUE_DEPENDENCIES` table -- the same
  table used to score dependency violations for every other topology -- against what has
  actually posted to the shared blackboard, and starts whatever is ready.
- Capabilities ready in the same wave run concurrently (`asyncio.gather`).
- Every specialist is dynamically constructed, per wave, by `_build_specialist()` --
  no fixed roster, no pre-existing agent objects.
- `request_specialist` remains available on every specialist, narrowed to capabilities
  outside the seed call's original scope decision -- genuinely emergent additions, not
  the routine, already-known dependency chain.

## Why this exists as a separate topology, not a patch to Swarm

Two live runs of the shared free-text mechanism (Q10, runs n=3 and n=4 in the run
store) failed on the same handoff: `diagnose` never called `request_specialist` for
`recommend`, confirmed from the per-agent instruction audit files. Two prompt-only
fixes did not change the outcome. That result is recorded as the finding for plain
Swarm (`coordinators/swarm/README.md`), not silently patched -- this module is the
redesigned alternative, tested separately. See `plan/Thesis_Project_Tracker.xlsx`,
Topology Comparison sheet, rows 11-12, for the full comparison and evidence.

## Running it

```
./scripts/execute_topology.sh -t swarm_constrained_adaptive -q Q10
```

Per-agent instruction audit files land in
`runs/swarm_constrained_adaptive/<run_uid>/wave<N>_<agent_name>.md`.

## File map

- `config/prompts/coordinators/swarm_constrained_adaptive/master.md` -- seed planner
  prompt. Includes `@security_guardrails`, `@chatbot_behavior_swarm` (shared with plain
  Swarm -- NOT the generic `@chatbot_behavior`, which pulls in `@plan_confirmation` and
  tells the model to present a plan and wait for a confirmation turn this topology
  never sends; fixed 30-Aug-26), `@input_handling`, `@participant_capabilities`,
  `@scope_selection`, `@dependency_discovery`, `@self_check`, `@exception_handling`.
- `topologies/swarm_constrained_adaptive.py` -- `WavePlan`/`AgentSpec`/`Blackboard`
  schemas, `_prerequisites_met()`, the wave loop (`ConstrainedAdaptiveSwarmEntryPoint`).
- `topologies/registry.py` -- registers this module under the topology key
  `swarm_constrained_adaptive`.
