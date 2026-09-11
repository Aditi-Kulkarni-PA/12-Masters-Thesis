# shared/ — cross-cutting partials

26 files, `@include`d by whichever conditions need them. Guardrails, conversational
behaviour, dependency rules, the output contract, and the five capability-list renderers.
Some are pure compositions that exist so a coordinator includes one name and gets a stable
bundle — `chatbot_behavior.md`, `dependency_discovery.md`, `output_contract.md`.

## The rule for editing anything here

**Prompt content only. No changelog entries, no "corrected on <date>" notes, no
descriptions of what used to be wrong.**

Every file in this folder is pasted verbatim into a live system prompt, so anything
written in one is sent to the model.

That makes a correction note self-defeating. Suppose a prompt here once named internal
tool identifiers it should not have, and that text was removed. Adding a note saying
*"removed the tool identifiers that were being exposed here: X, Y, Z"* puts X, Y and Z
straight back into the model's context — the note undoes the fix it is describing.

Record corrections in this README, in the folder README, or in the decision log. Never
inside a file that gets included.

## Where the rest is documented

Which conditions include which partial, the composition chains, the capability renderers,
and the full per-condition diff:

[`docs/thesis-topology-tradeoffs/topologies/prompt-modularization.md`](../../../../docs/thesis-topology-tradeoffs/topologies/prompt-modularization.md)
