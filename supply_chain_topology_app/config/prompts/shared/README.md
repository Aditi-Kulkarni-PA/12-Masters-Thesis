# 00_shared/ — includes used by 2+ conditions

**Convention: these files must stay clean prompt content only — no changelog/"corrected
on X date" notes inside them.** Every file here gets `@include`d directly into a real
system prompt; a changelog note describing what used to be wrong (e.g. naming the exact
tool identifiers that were leaking) would itself leak straight back into the model's
context the moment it's included. Document corrections in this README (or the top-level
`topology_specs/README.md` / `Topology_Prompt_Design_Spec.md`), never inside the file
that gets included.

## Files

| File | Used by | Purpose |
|---|---|---|
| `security_guardrails.md` | All 7 (byte-identical copy from the app) | Scope, injection defense, PII, fabrication ban |
| `field_glossary.md` | Domain agents that need it (byte-identical copy) | Feature/field definitions |
| `format_summary.md` | As wired in the app (byte-identical copy) | Formatting helper |
| `dependency_discovery.md` | All 5 model-driven conditions | The `upstream_missing` discovery rule — identical bytes is the load-bearing control |
| `input_handling.md` | Every coordinator that hands work to a specialist: Planner-Executor, Swarm, Dynamic-Graph. Monolith calls the tools itself and inlines predict's own file_path section; Mesh's entry agent carries it in its own prompt; Sequential / Static-Graph DAG pass inputs in code | Pass the file path **verbatim**, carry the user's scenario wording and other specifics into the task string. Added 22-Aug-26, R23 |
| `self_check.md` | All 7 (aggregators get the completeness-check portion only) | Reflect-retry (cap 1/tool) + uniform 10-call/turn hop budget |
| `exception_handling.md` | All 7 | Never-fabricate, always-report principle, generalized from `master_expert.md` |
| `output_contract.md` | All conditions with a MasterOutput-producing coordinator (not Mesh — see `coordinators/mesh/`) | MasterOutput field contract + narrative-writing rules |
| `chatbot_behavior.md` | All 7 | Plan-confirmation UX flow |

## `mesh_peers.md` / `mesh_handoff.md` are NOT here — moved to `coordinators/mesh/` (30-Aug-26)

Both used to live in this folder, findable only by their `mesh_` filename prefix — this
table used to list `mesh_handoff.md` as "the one deliberate exception to byte-identical
domain prompts." That was the same category of mistake `result_expectations.md` and
`deliverable_contract.md` already made below (R19, R22): topology-only content sitting in
`shared/`, distinguished from genuinely cross-condition content only by convention, not by
location. Neither was ever observed to leak into another condition, but nothing structural
prevented it — the same informal-safety gap R18/R19 caught for real. Moved to
`coordinators/mesh/` for consistency with the rule stated below. `topologies/mesh.py`
loads `mesh_peers.md` via `get_instruction('mesh_peers', topology='mesh')`; `mesh_handoff.md`
is unused until Mesh A (the Handoff variant) is built.

## Corrections made to `chatbot_behavior.md` (22-Aug-26, two rounds)

The app's live copy of this file has two leaks; both are fixed in this folder's
version, neither should be copied over from the live app as-is:

1. **Dependency-order leak.** Said "Follow prerequisite chains (predict before
   diagnose, diagnose before recommend, predict before email)" — handed the true
   dependency graph to every condition, defeating `dependency_discovery.md`. Risk Log
   **R15**.
2. **Named-tool-menu leak.** The Query Interpretation table's "Action" column named
   the 5 Planner-Executor-specific wrapped tool identifiers
   (`predict_delivery_delays_tool` etc.) directly. Since every condition includes this
   file, Swarm's dispatcher — whose entire premise is having no fixed named-tool
   roster (`05_swarm/master.md`) — was handed exactly that roster by name, through the
   one file that was supposed to be topology-neutral. Fixed by describing the 5
   capabilities in natural language only, everywhere in this file ("tools" →
   "capabilities" throughout, not just in the table). Risk Log **R18**.

## `result_expectations.md` is NOT here either — also Swarm-only (R22, revised 22-Aug-26)

Briefly lived in this folder and was included by six conditions. That was wrong in both
directions, and the corrected rule is:

- **Output schema** — only needed where agents are created dynamically, i.e. Swarm.
  Everywhere else the domain agents are pre-registered in `core/agents.py` with
  `response_format=<Schema>`, so the framework enforces the structure; restating it to
  a coordinator is dead prompt tokens against a measured variable.
- **Domain instructions** (how to write `predict_summary`, the five ordered diagnosis
  sections, SLA quoting, per-row enrichment) — already carried by the agents themselves
  through `00_shared_domain_agents/`, which every topology uses. That folder exists
  precisely so these never need restating per condition. A coordinator does not need
  them; its specialists already have them.
- **Swarm is the exception on both counts**, because its specialists are described at
  runtime rather than pre-registered — so it needs the schema *and* the instructions,
  which is what `05_swarm/result_expectations.md` carries.

The general principle: instructions are common to all conditions unless the topology
itself demands a difference. Schema is topology-dependent; instructions are not.

## `deliverable_contract.md` is NOT here — it's Swarm-only (R19, revised 22-Aug-26)

Recorded here because the first version of this fix put the file in `00_shared/` and
included it in five conditions. That was wrong, and the corrected rule is worth stating
explicitly since it governs any similar future addition.

**The governing rule:** a prompt-level contract should compensate for missing
**structural** information — things absent from the model's function-calling schema —
never for missing reasoning. Judged that way, only one condition qualifies:

| Condition | What its schema structurally reveals | Needs a prose contract? |
|---|---|---|
| Monolith | 5 tools attached, + all 5 domain prompts inlined | No — most-informed condition |
| Planner-Executor | 5 named agent-as-tools with descriptions | No |
| Dynamic-Graph | 5 named participants, + ledger tracks completion | No — near-verbatim restatement of its participant list |
| Mesh | Each agent's 4 handoff targets, registered | No — see below, this one matters |
| Sequential / Static-Graph DAG | n/a — code calls all 5 exactly once | No — completeness structurally guaranteed |
| **Swarm** | **One generic `dispatch_specialist`. Nothing else.** | **Yes** |

**Mesh is the case worth reading twice.** The original (wrong) reasoning was "with no
coordinator, nobody notices the request is incomplete." But *that is the documented Mesh
failure mode* — Unverified Handoffs, 11.8% of MAST failures
(`Multi_Agent_Topology_Reference.md` §5). Adding a completion checklist would have
suppressed exactly the weakness the condition exists to measure. The fix would have
quietly destroyed the finding.

**Where the file now lives:** `05_swarm/deliverable_contract.md`, included by Swarm's
dispatcher only. It states five user *outcomes*, never tools/agents/steps — so Swarm
still receives strictly less than every other condition (outcomes without names), which
keeps the correction levelling rather than advantaging. Full rationale in
`05_swarm/README.md`.

## Correction made to `05_swarm/master.md` (same session)

The dispatch-description examples originally given ("a specialist that can predict...",
"someone who can compare... historical baselines") mapped 1:1 onto 2 of the app's real
5 domains. Even without naming a tool, few-shot examples shaped exactly like the real
task teach the model the shape of the answer space — a softer version of the same bias.
Replaced with a single out-of-domain illustrative example (trip planning) so the model
learns the *format* of a capability description without learning the *content* of this
app's actual roster.
