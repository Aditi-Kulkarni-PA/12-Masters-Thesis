# Monolith — condition manifest

**Who decides order:** the single agent itself, discovering real dependencies via
`upstream_missing` — same mechanism as every other model-driven condition.

**Files in this folder:** `master.md` only. No sub-agents — the domain agent prompts
are inlined by reference (`@predict_delivery_delays` etc.) rather than duplicated, so
they stay byte-identical to `00_shared_domain_agents/` (verify with `diff`, not by eye).

**Assembled `@include` chain:** `security_guardrails` → `chatbot_behavior` →
`dependency_discovery` → `self_check` → `exception_handling` →
`predict_delivery_delays` → `diagnose_delay_patterns` → `delay_simulation` →
`recommendation` → `email_alert` → `output_contract`.

**Tools attached directly:** all 5 raw pipeline tools (`predict_delivery_delays_tool`,
`diagnose_delay_patterns_tool`, `delay_simulations_tool`, `recommendation_tool`,
`email_alert_tool`), no agent-as-tool wrapping.

**Plan turn:** yes (2-turn plan → confirm, via `chatbot_behavior`).

**Expected, legitimate finding:** this assembles into the longest system prompt of any
condition (5 domain prompts inlined). Higher per-run prompt-token cost is a predicted
structural property of "no separation of concerns," not a measurement artifact — report
it as such, don't normalize it away.

**One thing to verify before relying on this file (T37 smoke test):** the 5 domain
prompts were each written assuming they run as a focused, single-purpose agent (e.g.
predict's "call it exactly once"). Concatenated together, check whether any instructions
conflict. If they do, the fix is a short reconciliation note added here — never a silent
edit to the domain prompt files in `00_shared_domain_agents/`.

## Why the 5 domain prompts are inlined verbatim (moved out of `master.md`, 23-Aug-26)

`master.md` includes `@predict_delivery_delays` … `@email_alert` — the *same* files the
specialist agents use in every other condition, copied rather than summarised. With no
sub-agents to delegate to, this is the only place that domain expertise can live.
Leaving it out would make any "monolith performs worse" finding an artifact of a missing
instruction rather than a property of the architecture (`Topology_Prompt_Design_Spec.md`
§4.1).

That rationale used to sit **inside `master.md`**, where the model read it on every run.
It was removed because it leaked the experiment into the prompt: it referred to "every
other condition of this system", to "handicapping this condition", and to a hypothesis
that *"monolith performs worse"*. Telling a model it is the arm of a benchmark expected
to underperform is a demand characteristic, and it named the topology besides — the same
R13 confound the shared-file neutrality rule exists to prevent. Justification belongs in
this README; `master.md` now carries only a functional lead-in.

## What this condition actually isolates (23-Aug-26)

Not "no tools". The tools are the task — `predict_delivery_delays` is a trained
RandomForest, `get_delay_diagnosis` is SQL over historical baselines, `recommend_actions`
is RAG over the SLA knowledge base. Planner-Executor's five specialists call these *same*
tools one level down (`core/agents.py`: each is `Agent(..., tools=[pipeline_mcp])`).
Removing them would make this condition fabricate its data while the others compute it.

The variable is context partitioning:

| | Monolith | Planner-Executor |
|---|---|---|
| LLM contexts | 1 | 6 |
| System prompt | 49,804 chars (5 domain prompts inlined) | master 21,367 |
| Tool payloads | all 68,516 chars in the same context | split across 5 specialist contexts |
| Peak single context | ~117,000 chars | ~31,000 chars |

`@fallback_advisor` is inlined here rather than attached as an agent-as-tool (which is what
Planner-Executor does) because an agent-as-tool is a second LLM context and would destroy
the single-context property. Capability equal, context count still 1.

Full writeup: `Topology_Prompt_Design_Spec.md` §4.1.
