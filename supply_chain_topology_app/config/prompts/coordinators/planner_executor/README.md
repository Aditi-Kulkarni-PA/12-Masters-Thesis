# Planner-Executor — condition manifest

**Who decides order:** the master, discovering real dependencies via `upstream_missing`
— same mechanism as every other model-driven condition. No fixed order, no "one tool at
a time" throttle (removing that was the R13 fix — forcing serial execution made this
condition behaviorally indistinguishable from Sequential).

**Files in this folder:** `master.md` — this is the corrected replacement for the app's
current `config/prompts/agents/master_expert.md`. The 5 domain agents run with their
unmodified prompts from `00_shared_domain_agents/`, wrapped as agent-as-tools (existing
`_wrap_as_tool` pattern in `topologies/planner_executor.py`).

**What changed vs the current `master_expert.md` (Risk Log R13, 7 sites):**
1. Top "CRITICAL: SEQUENTIAL EXECUTION ONLY" block — replaced with a neutral
   "you decide which tools and what order" statement.
2. Full-workflow trigger — kept "call all 5 tools", deleted the explicit order
   restatement.
3. Sections 3/4/5/6 "Pre-requisite" blocks — freshness-skip logic kept (genuinely a
   caching feature, see below), but the "call predict first, then X" phrasing replaced
   by "call X; resolve `upstream_missing` if it occurs."
4. Section 7 "Full Workflow" numbered 1-5 list — deleted, replaced by the same neutral
   framing as #2.
5. Section 10 "SEQUENTIAL ONLY" bullet — deleted.
6. `@dependency_discovery` added.
7. Sections 4/5/6's per-field narrative-writing rules (`simulate_summary` etc.) — moved
   out entirely into `output_contract.md`, corrected 22-Aug-26 (they were never actually
   PE-specific; leaving them only here would have meant this is the only condition with
   usable writing instructions for those fields).

**Assembled `@include` chain:** `security_guardrails` → `chatbot_behavior` →
`dependency_discovery` → `self_check` → `result_expectations` → `exception_handling` →
`output_contract` (domain agents wrapped as tools, called by the master, not inlined).

**Genuinely PE-specific content kept in `master.md`:** §0 prediction contract, §9
`chat_response`/`PLAN CONFIRMED` handling.

**`result_expectations` added 22-Aug-26 (R22).** The master judges returned results
under `self_check.md`'s reflect-retry, but had no criteria to judge against — the tool
descriptions are one-liners. Monolith gets these criteria by inlining the domain
prompts outright; this condition got nothing. Shared file, identical bytes with Swarm
and Dynamic-Graph.

**Freshness-skip caching removed — R16 closed 22-Aug-26.** The live `master_expert.md`
carries a `[SYSTEM: ... FRESH` caching section; it is deliberately absent here. Reading
the code settled the open question: `SC_NO_CACHE=1` suppresses
`build_freshness_system_msg()` outright (`execute_topology.py:90`, `delivery_chat_app.py:385`),
and the comment at `delivery_chat_app.py:81` states plainly that this mode is "required
for thesis latency/cost measurements". So in every measurement run the marker is never
emitted for **any** condition — the suspected PE-only advantage does not exist, and the
instruction block was inert text that still consumed prompt tokens on every turn. Since
prompt tokens are themselves a measured variable, keeping dead instructions would have
inflated this condition's cost against conditions that never had them.

**Consequent requirement (T53 config freeze):** `SC_NO_CACHE=1` must be pinned and
recorded in the run manifest, and verified per run — the same treatment as
`SC_DEV_PATH_FALLBACK` under R10. If it is ever unset, a freshness marker appears that
no condition's prompt explains, and any condition that then skips work silently
corrupts that run's comparability.

**Plan turn:** yes (2-turn plan → confirm).
