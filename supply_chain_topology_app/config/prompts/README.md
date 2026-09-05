# Topology Specs — working prompt files for all 7 conditions

This folder holds the actual prompt content for the thesis's orchestration-topology
comparison, staged for review before wiring into the live app
(`0_supply_chain_thesis/supply_chain_topology_app/config/prompts/`). It implements the
design in the two companion documents in `../papers-articles/`:

- `Multi_Agent_Topology_Reference.md` — the pattern catalog, the Group A/B
  dependency-handling principle, the verified true dependency graph.
- `Topology_Prompt_Design_Spec.md` — per-condition prompt architecture, the
  constants-vs-variables framing, and the Swarm dispatch design.

Read those two first if something below is unclear — this README is a compressed,
tabular index into decisions made there (plus the corrections made while building this
folder, noted inline).

## Folder layout

```
00_shared/                  -- includes used by 2+ conditions (identical bytes)
00_shared_domain_agents/    -- the 5 domain agent prompts, byte-identical, frozen
01_monolith/                -- CORE
02_sequential/               -- CORE
03_planner_executor/         -- CORE (already built and Done in the app as of T32)
04_static_graph_dag/         -- CORE
05_swarm/                    -- CORE (promoted from optional 22-Aug-26)
06_mesh_optional/            -- OPTIONAL, gated at T100
07_dynamic_graph_optional/   -- OPTIONAL, gated at T100
```

Each topology folder has its own `README.md` manifest with the exact assembled
`@include` chain and condition-specific notes. This file is the cross-condition view.

## The frozen substrate (identical everywhere, verify with `diff`, not by eye)

Model, `temperature=0`, `tool_choice="required"` on every domain agent, the 5 domain
agent prompts (`00_shared_domain_agents/`), their Pydantic schemas (`core/schemas.py`),
the tool-level `upstream_missing` guards, the query set, and the measurement pipeline.
None of these vary by topology. If a `diff` between two conditions' domain-agent files
ever shows a difference, that's a bug, not a design choice.

## Two corrections made while building this folder (not previously documented)

1. **`shared/chatbot_behavior.md` leaked the true dependency graph.** The app's current
   version says "Follow prerequisite chains (predict before diagnose, diagnose before
   recommend, predict before email)" — included by every single condition, this handed
   out the answer `dependency_discovery.md` exists to make conditions discover for
   themselves. Fixed in `00_shared/chatbot_behavior.md` here; the app's live copy still
   has the bug (do not copy it over this folder's version).
2. **Narrative-writing rules for `simulate_summary`/`recommendation_summary`/
   `email_alert_summary` only existed in `master_expert.md`.** 6 of 7 conditions would
   have produced those fields with zero formatting guidance. Moved into
   `00_shared/output_contract.md`, included identically everywhere. See
   `Topology_Prompt_Design_Spec.md` §3.2 for the full writeup.

**Two more corrections, 22-Aug-26 (same session, after review):**

3. **`recommendation_summary` was ambiguous against `recommended_actions`.**
   `recommendation_summary` (thin, 2-3 sentence master-level wrapper) is not the
   recommendation content — the actual 9-15 detailed `recommended_actions` (each with
   `action_desc`, `supporting_data`, a quoted `sla_reference`) are long by design,
   produced entirely by the `recommendation` domain agent, and captured directly. Now
   stated explicitly in `output_contract.md` so the two can't be conflated again.
4z. **Swarm was handicapped by construction — new `05_swarm/deliverable_contract.md`
   (R19).** Every other condition's function-calling schema structurally reveals its
   roster (5 attached tools, 5 named agent-as-tools, 5 named participants, or 4
   registered handoff targets per agent). Swarm's schema contains one generic
   `dispatch_specialist` and nothing else — so its dispatcher had no count of
   capabilities and no definition of "done", would have had to guess the scope of the
   task, and would have systematically under-delivered on dashboard runs. That measures
   under-specification, not topology — the same handicap already documented for Monolith
   in §4.1 and missed here. Fixed with a Swarm-only include stating what the user needs
   as **outcomes**, never who provides them; Swarm still gets strictly less than every
   other condition (outcomes without names), so the correction levels rather than
   advantages.

   **Revised same day after review:** the first version of this fix put the file in
   `00_shared/` and included it in five conditions. Wrong — a prompt contract should
   compensate for missing *structural* information, not missing reasoning, and only
   Swarm qualifies. Mesh is the case worth reading twice: "nobody notices the request is
   incomplete" **is** Mesh's documented failure mode (Unverified Handoffs, 11.8% of MAST
   failures), so a completion checklist would have suppressed the very weakness the
   condition exists to measure. Full rule and per-condition table in
   `00_shared/README.md`.
4a. **A second, more serious leak in `chatbot_behavior.md` (R18).** Its Query
   Interpretation table named the 5 Planner-Executor-specific wrapped tool identifiers
   directly (`predict_delivery_delays_tool` etc.) — since every condition includes this
   file, Swarm's dispatcher (whose whole premise is *no* fixed named-tool roster) was
   handed exactly that roster by name. Fixed by describing all 5 capabilities in
   natural language throughout the file, never a literal identifier. Also fixed:
   `05_swarm/master.md`'s own worked examples mapped 1:1 onto 2 real domains
   (predict, diagnose) — a softer version of the same bias via few-shot anchoring —
   replaced with an out-of-domain illustrative example. Full writeup in
   `00_shared/README.md`.
4b. **Mesh's final-output question is resolved, not open (R17 closed).** Each of the 5
   domain agents has `response_format` locked to its own frozen schema in
   `core/agents.py` — there is no MasterOutput slot to attach to any of them, and a
   covering/aggregator agent would just rebuild a hub. **Mesh has no MasterOutput
   object at all** — each agent's own structured output is captured directly, same
   mechanism as every other condition, just without a wrapper narrative. Consequence:
   the 3 thin fields that exist only because a master needed somewhere to put them
   (`simulate_summary`/`recommendation_summary`/`email_alert_summary`) are absent for
   Mesh — the substantive, scored content is unaffected. Full reasoning in
   `06_mesh_optional/entry_point.md`.

## Cross-condition comparison

| Category | Monolith | Sequential | Planner-Executor | Static-Graph DAG | Swarm | Mesh (optional) | Dynamic-Graph (optional) |
|---|---|---|---|---|---|---|---|
| **Scope** | CORE | CORE | CORE (Done) | CORE | CORE | Optional, T100-gated | Optional, T100-gated |
| **Who decides call order** | The agent itself | Code | The master | Code (leveled scheduler) | The dispatcher | Each domain agent, decentralized | Orchestrator's Task Ledger |
| **Dependency-handling group** | B — discovers via `upstream_missing` | A — code-enforced | B — discovers | A — code-enforced | B — discovers, at dispatch time | B — discovers, peer handoff | B — discovers, ledger-driven |
| **Tool interface** | 5 raw pipeline tools, direct | n/a (code chains agents) | 5 named agent-as-tools | n/a (code, leveled) | 1 generic `dispatch_specialist` | Peer handoff to any of the other 4 | 5 named participants, ledger-driven |
| **Coordinator/master prompt file** | `01_monolith/master.md` | none (aggregator only) | `03_planner_executor/master.md` | none (aggregator only) | `05_swarm/master.md` | none (`06_mesh_optional/entry_point.md` documents composition) | `07_dynamic_graph_optional/master.md` |
| **Sub-agent prompts** | Inlined via `@include` (not delegated — no sub-agents exist in this condition) | Unmodified, called by code | Unmodified, wrapped as agent-as-tools | Unmodified, called by code | Unmodified, invoked after dispatch resolution | Unmodified + `@mesh_handoff` overlay (the one deliberate exception) | Unmodified, invoked by ledger |
| **`deliverable_contract.md`** (what "done" means, outcomes only) | No — 5 tools + inlined prompts | No — code guarantees completeness | No — 5 named tools in schema | No — code guarantees completeness | **Yes — only condition whose schema reveals nothing** | No — handoff targets in schema, *and* adding it would mask Mesh's own failure mode | No — named participant list + ledger |
| **`dependency_discovery.md`** | Yes | No (n/a) | Yes | No (n/a) | Yes | Yes | Yes |
| **`result_expectations.md`** (output schema + domain instructions) | No | No | No | No | **Yes — only condition whose specialists are described at runtime, not pre-registered with a bound `response_format`** | No | No |
| **`input_handling.md`** (pass file path verbatim + user specifics into the task string) | Via inlined predict prompt | **Yes** | **Yes** | Code passes inputs | **Yes** | Via entry agent's own prompt | **Yes** |
| **`self_check.md` (reflect + hop limit)** | Yes | Yes | Yes | Yes (aggregator's completeness check only) | Yes + Swarm-specific dispatch-mismatch note | Yes | Yes |
| **Hop/tool-call budget** | 10 calls/turn | n/a — exactly one forced tool per turn, over the planned subset | 10 calls/turn | n/a — exactly 5 by construction | 10 dispatch calls/turn | 10 handoffs+calls/turn, combined | 10 ledger invocations/turn |
| **Reflect-retry cap per tool** | 1 | n/a (code: 1 retry on transient failure) | 1 | n/a (code: 1 retry on transient failure) | 1 (+ redescribe-on-mismatch case) | 1 | 1 |
| **`exception_handling.md`** | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| **`security_guardrails.md` / `chatbot_behavior.md`** | Yes / Yes | Yes / Yes (`chatbot_behavior_sequential` → `_basic`; no plan-confirmation, the plan is already confirmed) | Yes / Yes | No / No | Yes / Yes | Per-agent, TBD if entry agent only | Yes / Yes |
| **`output_contract.md` (MasterOutput fields, UI)** | Yes | Yes | Yes | Yes | Yes | **No — resolved (no MasterOutput object; see below)** | Yes |
| **Domain-level structured output (`predict_summary`, `recommended_actions`, etc.)** | Unaffected — same schemas | Unaffected | Unaffected | Unaffected | Unaffected, *if* dispatch calls through to the real tool (not a stub) | Unaffected — captured per-agent, no wrapper | Unaffected |
| **Plan turn (2-turn plan→confirm)** | Yes | Yes — the planner's response IS turn 1 | Yes | No — structurally NULL | Yes | Yes | Yes |
| **PE-specific freshness-skip caching** | No (open question — should it be extended here too? see PE README) | No | Yes | No | No | No | No |
| **Novel/topology-specific measurable** | — | — | — | — | Dispatch resolution accuracy | — | Native ledger adaptivity (asymmetry to document, not eliminate) |
| **Expected structural cost signature** | Longest prompt (5 domain prompts inlined) — higher token cost is a predicted finding, not noise | Slowest wall-clock — zero parallelism by construction, plus a planning turn and one coordinator turn per planned step; report serialization and round-trips separately | Moderate | Fastest given true parallelism at Level 0 | Router cost ≈0, reported separately from orchestrator cost | O(n²) coordination risk (article-cited 2-11.8x Sequential's token cost) | Ledger bookkeeping overhead, TBD |
| **Known open question** | Do the 5 inlined domain prompts conflict when concatenated? (verify T37) | — | Is freshness-skip caching PE-only or shared? (verify T34) | — | — | Resolved 22-Aug-26 — no open question remaining | Confirm what MagenticBuilder actually provides in installed `agent-framework` (T28) |

## Why "same hop-step limit" matters here specifically

The retry/hop budget (10 calls/turn, 1 reflect-retry per tool) is the same named
constant in every `self_check.md` include, not re-derived or re-typed per condition —
that equality is as load-bearing as `dependency_discovery.md`'s identical bytes. Group A
(Sequential, Static-Graph DAG) can't exceed it by construction (exactly 5 calls, once
each), so the limit is trivially satisfied there rather than actively enforced — that
asymmetry is a property of what those two topologies *are*, not a gap.

## Recommended instrumentation additions (not yet implemented — proposals only)

Building this folder surfaced two run-store fields worth adding alongside the T98
refactor, so retries and hop-limit hits are measurable per condition rather than
silently inflating `tool_call_count`:
- `tool_call.attempt_number` (int, default 1, incremented on a reflect-retry)
- `run.hop_limit_hit` (bool) — did this run exhaust the 10-call budget

## What's still a proposal, not yet live

Nothing in this folder is wired into the running app. `config/load_config.py`'s
`@include` mechanism already supports one-level includes and has an empty
`coordinators/` directory apparently provisioned for exactly this — see the
implementation checklist in `Topology_Prompt_Design_Spec.md` §7 for the file-by-file
plan to move this folder's content into the live config tree.
