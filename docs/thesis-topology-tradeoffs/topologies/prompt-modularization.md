# Prompt Modularization — how the same text reaches all nine

[← Documentation index](../README.md) · [Topology reference](topology-reference.md) · [How they actually work](how-they-actually-work.md)

The design requirement behind this mechanism: the same instruction text is fed to every
topology, and only the extra text a topology's coordination actually requires is allowed
to sit outside that shared text. A prompt is not considered done until it has been
expanded and diffed against every other condition it will be compared with — an
undocumented difference is a build error, not a finding. This document is that diff,
made once and kept current rather than re-derived by hand for every review.

---

## 1. The mechanism

`config/load_config.py`'s `get_instruction(agent_key, topology=None)` reads one prompt
file and expands include directives recursively (depth 5, a guard against a cycle, not a
real limit — the deepest chain in use is three). Two directive forms exist:

| Directive | Effect |
|---|---|
| `@name`, alone on a line | replaced with `name.md`'s content, itself expanded |
| `@@capabilities:<style>`, alone on a line | replaced with a rendered capability list, from the single source `shared/capability_details.md` |

A missing include file leaves the directive visible rather than failing the whole prompt
— a deliberate choice, so a typo surfaces as a stray `@name` in the model's context
instead of an exception at startup that is easy to misattribute.

**Search order:** `shared/` → `agents/` → `coordinators/<topology>/`, in that order, for
every `@name`. A topology folder can add its own partial or override a name — none of the
live topologies actually override a `shared/`-level name — but it can never shadow one,
because `shared/` is searched first.

---

## 2. The three-tier layout

| Tier | Folder | Holds | Shared across |
|---|---|---|---|
| Domain | `config/prompts/agents/` | the five capability prompts, output schemas, the fallback advisor | all nine, byte-identical |
| Cross-cutting | `config/prompts/shared/` | guardrails, behaviour, dependency rules, the output contract, capability-list renderers | whichever topologies include each one — see §5 |
| Topology | `config/prompts/coordinators/<name>/` | the coordinator/aggregator/manager/planner files that decide what runs and when | that topology only |

Nine `coordinators/` subfolders, one per built topology, each holding only what that
topology's coordination requires — no domain prompt text is ever duplicated into a
coordinator folder (Monolith is the one apparent exception, and §5 explains why it is
not: it has no sub-agents to `@include` domain text *for*, so its coordinator recites
the same domain files every other topology's specialist reads).

---

## 3. Composition chains

Several `shared/` files are themselves pure compositions — they exist so a topology
`@include`s one name and gets a stable bundle, without every coordinator file re-stating
which sub-parts belong together.

| Composed file | Expands to | Used by |
|---|---|---|
| `chatbot_behavior.md` | `chatbot_behavior_basic` + `plan_confirmation` | Planner-Executor, Monolith, Mesh |
| `chatbot_behavior_swarm.md` | `chatbot_behavior_basic` (no confirmation turn) | Swarm, Swarm-CA |
| `chatbot_behavior_static_graph_dag.md` | `chatbot_behavior_basic` | Static Graph DAG |
| `chatbot_behavior_sequential.md` (topology-local) | `chatbot_behavior_basic` + its own turn-shape text | Sequential |
| `dependency_discovery.md` | `dependency_basics` + `upstream_recovery` + `concurrency_policy` | Planner-Executor, Monolith, Swarm, Swarm-CA, Dynamic Graph's manager |
| `output_contract.md` | `narrative_field_guidance` | every coordinator/aggregator that writes `MasterOutput` directly |

The omissions are as load-bearing as the inclusions. `upstream_recovery` (can retry) and
`upstream_report_only` (cannot retry) are never both present for one topology — Sequential
gets only `upstream_report_only`, because its forced one-tool-per-turn shape cannot retry
within a turn; everything that *can* retry gets `dependency_discovery`'s full bundle
instead of `dependency_basics` alone.

---

## 4. Capability descriptions — one source, five renderings

`shared/capability_details.md` is the single description of the five capabilities. Five
one-line wrapper files each call `@@capabilities:<style>` to render it in the wording that
matches a topology's own interface metaphor, so a Mesh peer reads about "peers" and a
Planner-Executor sub-agent reads about "tools" — same facts, different frame:

| Renderer | Style | Used by |
|---|---|---|
| `tool_capabilities.md` | named tool | Planner-Executor, Sequential (both files), Static Graph Routed |
| `raw_tool_capabilities.md` | raw tool, no sub-agent wrapper | Monolith |
| `participant_capabilities.md` | Magentic participant | Swarm, Swarm-CA, Dynamic Graph's manager |
| `peer_capabilities.md` | peer, addressed by name | Mesh (`mesh_handoff.md`) |

Static Graph DAG uses none of these — its coordinator never selects a scope, so it has no
reason to describe the capabilities to the model at all; the graph is fixed in code.

---

## 5. The partial diff, per topology

Resolved to leaf `shared/` files (composition chains expanded). This is the artefact the
project's own rule requires before a coordinator prompt is considered done: every
topology, side by side, so a missing or extra partial is visible rather than assumed.

| Topology | guardrails | behaviour | capability list | scope | dependency | self-check / exceptions | output contract |
|---|---|---|---|---|---|---|---|
| **Monolith** | ✓ | full (+confirm) | raw_tool | ✓ | full | ✓ | own override (see below) |
| **Sequential** — planner | — | basic (+confirm) | tool | ✓ | basics only | — | — |
| **Sequential** — executor | ✓ | basic, no confirm | tool | — | basics + report-only | ✓ | ✓ |
| **Planner-Executor** | ✓ | full (+confirm) | tool | ✓ | full | ✓ | ✓ |
| **Static Graph DAG** — coordinator | ✓ | basic, no confirm | — | — | — | — | — |
| **Static Graph DAG** — aggregator | — | — | — | — | — | ✓ | ✓ |
| **Static Graph Routed** — coordinator | ✓ | basic (+confirm) | tool | ✓ | basics only | — | — |
| **Static Graph Routed** — aggregator | — | — | — | — | — | ✓ | ✓ |
| **Dynamic Graph** — coordinator | ✓ | basic, no confirm | — | — | — | — | — |
| **Dynamic Graph** — manager | — | — | participant | ✓ | full | ✓ | — |
| **Dynamic Graph** — aggregator | — | — | — | — | — | ✓ | ✓ |
| **Mesh** — entry point | ✓ | full (+confirm) | — | ✓ | — | — | — |
| **Mesh** — peer body | — | — | peer | — | basics + concurrency | referenced, not included† | — |
| **Mesh** — 3 narrative peers | — | — | — | — | — | — | `narrative_field_guidance` only |
| **Swarm** | ✓ | basic, no confirm | participant | ✓ | full | ✓ | — |
| **Swarm-CA** | ✓ | basic, no confirm | participant | ✓ | full | ✓ | — |

Blank cells are not omissions to fix — each is a topology for which that shared concern
does not apply, and the reason is stated in §3 and §6. "Own override" and "†" are the two
places worth reading carefully:

- **Monolith's output contract** does not `@include output_contract.md`. It restates the
  same four narrative fields itself, in `coordinators/monolith/output_contract_monolith.md`,
  because it also has a full per-capability structured-output section that
  `output_contract.md` was never written to describe (Monolith is the one topology
  producing predict/diagnose/simulate/recommend/email row data directly, since it has no
  sub-agents for the app to capture that data from). It does still `@include
  narrative_field_guidance` — the actual per-field writing rules — so the shared
  substance is present even though the four-field statement is written by hand rather than
  included.

- **† Mesh points a peer at a file the peer never receives.** `mesh_handoff.md` tells each
  peer to "see `self_check.md` for the retry/hop limits that apply to your own handoff
  decisions." Nothing under `coordinators/mesh/` `@include`s `self_check.md`, so that text
  never reaches the model and the instruction refers to a document the peer does not have.

  **Behaviour is unaffected**: Mesh enforces its **retry and hop limits in code**, through the
  shared hop budget, rather than by asking the model to observe them. Only the sentence is
  dangling. Left uncorrected here because fixing it means editing a prompt, which is a
  different kind of change from documenting one.

---

## 6. Two contrasts confirmed at the prompt level, not just the code level

The two controlled pairs (see [topology-reference.md §3](topology-reference.md#3-the-nine-conditions))
are supposed to isolate one variable each. Diffing the actual prompt files confirms both
hold at the prompt layer too, not only in the orchestration code:

**Static Graph DAG vs Static Graph Routed — aggregators.** Byte-diff of the two
`aggregator.md` files shows both include the identical three shared partials
(`self_check`, `exception_handling`, `output_contract`); the only text that differs is
scoped exactly to the variable being isolated — Routed's aggregator is told which
capabilities the router selected and instructed not to infer a result for one that was
never selected, DAG's is not, because DAG always runs all five.

**Swarm vs Swarm Constrained Adaptive — seed planners.** The two `master.md` files
`@include` the identical eight shared partials, in the identical order — confirmed by
diff, not by re-reading the include lines. Every line that differs is prose about scope:
Swarm's planner names only what can start with nothing posted yet ("wave 1 only");
Swarm-CA's planner names everything the request needs and is told explicitly not to
reason about order or timing, because code checks readiness every round instead. Since
the shared partials are identical, the only thing left to produce a behavioural
difference between the pair is the wave-gating code itself — which is the pair's entire
point.

---

## 7. An undetected drift risk in the frozen layer

The output-schema files in `agents/` state each capability's output contract to the model,
and are part of what the study holds constant across all nine conditions. They mirror the
`Field(description=...)` strings in `core/schemas.py` and were originally generated from
it, but the generator is no longer in the repository — so they are hand-maintained, and
nothing checks that the two still agree.

A change to `core/schemas.py` that is not mirrored here, or an edit here that is not
mirrored back, would alter what every condition is told to produce while leaving no trace
in any measure. `check_model_parity.py` does not cover this: it compares generation
parameters, not prompt text.

---

[← Documentation index](../README.md) · [Topology reference](topology-reference.md) · [How they actually work](how-they-actually-work.md)
