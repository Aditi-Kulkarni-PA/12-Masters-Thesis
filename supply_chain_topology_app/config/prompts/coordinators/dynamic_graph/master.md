# Retired (29-Aug-26)

This file predated the MagenticBuilder implementation and bundled everything (security,
chatbot behavior, task framing, output contract) into one manager-agent prompt. That
does not work as constructed: `manager_agent`'s `default_options` (including
`response_format`) apply uniformly across every internal call the manager makes
(facts/plan/progress-ledger/final-answer), and `prepare_final_answer()` returns plain
text with no schema hook at all — so `@output_contract` here was never actually
enforceable, and bundling refusal/chat handling into the same instructions risked
corrupting the progress-ledger call's strict JSON parsing.

Split into three files instead, mirroring Static-Graph DAG (T39):
- `coordinator.md` — tools-off refuse/inform/proceed gate, no fixed plan (the ledger
  decides scope and order, not this turn).
- `manager.md` — this file's task-framing content, trimmed to what the manager_agent
  itself needs (participant capabilities, scope/dependency reasoning, self-check,
  exception handling, "do not fix the sequence in advance").
- `aggregator.md` — a post-workflow tools-off turn that writes the real `MasterOutput`
  JSON, since the manager's own final answer cannot.

Kept on disk rather than deleted so the history is visible; not read by any topology
code or `config/get_instruction`.
