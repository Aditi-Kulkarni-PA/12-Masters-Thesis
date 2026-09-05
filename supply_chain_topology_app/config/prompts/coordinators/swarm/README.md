# Swarm (pure mode)

Rewritten fresh 30-Aug-26 (T99) -- the prior README described an abandoned
dispatch_specialist design and a since-deleted codegen path; not carried forward.

## What this is

Pure emergent agent architecture: an LLM plans only the specialists that can start
immediately, and every downstream capability is requested directly by whichever
specialist produces the output it depends on. No code anywhere checks a dependency
table. Timing is entirely agent-decided.

- A seed call (`master.md`) decides wave 1: which capabilities can start with nothing
  posted to the blackboard yet.
- Each specialist, once it finishes its own work, decides for itself whether the
  request still needs another capability that depends on what it just posted, and
  calls `request_specialist` directly if so.
- Whatever gets requested during a wave becomes the next wave, run unconditionally --
  `topologies/swarm.py` does not check that a requested capability's own prerequisites
  have actually posted before running it.
- Every specialist is dynamically constructed, per wave, by `_build_specialist()` --
  no fixed roster, no pre-existing agent objects.

## Why this is a separate topology from Constrained Adaptive Swarm

Two live runs of this exact mechanism (Q10, runs n=3 and n=4 in the run store) failed
on the same handoff: `diagnose` never called `request_specialist` for `recommend`,
confirmed from the per-agent instruction audit files. Two earlier runs (n=1, n=2) show
a related but distinct failure: a specialist requesting a downstream capability before
that capability's own prerequisite had posted. Both are recorded as findings for this
condition -- what unconstrained, agent-timed propagation actually does under this
system's dependency structure -- not defects that were patched away. The redesigned
alternative, which gates timing in code instead, is
`topologies/swarm_constrained_adaptive.py` -- see that topology's own README, and
`plan/Thesis_Project_Tracker.xlsx`, Topology Comparison sheet rows 11-12, for the full
comparison and evidence.

## Running it

```
SC_QUERY_ID=Q10 ./scripts/execute_topology.sh swarm
```

Per-agent instruction audit files land in `runs/swarm/<run_uid>/wave<N>_<agent_name>.md`.

## File map

- `config/prompts/coordinators/swarm/master.md` -- seed planner prompt (wave 1 only).
  Includes `@security_guardrails`, `@chatbot_behavior_swarm` (NOT the generic
  `@chatbot_behavior` -- that pulls in `@plan_confirmation`, which tells the model to
  present a plan and wait for a confirmation turn that this topology never sends; fixed
  30-Aug-26 after a live Q5 run showed the seed call doing exactly that instead of
  executing), `@input_handling`, `@participant_capabilities`, `@scope_selection`,
  `@dependency_discovery`, `@self_check`, `@exception_handling`.
- `topologies/swarm.py` -- `WavePlan`/`AgentSpec`/`Blackboard` schemas, the wave loop
  (`SwarmEntryPoint`). No dependency table is imported or consulted anywhere in this
  file.
- `topologies/registry.py` -- registers this module under the topology key `swarm`.
