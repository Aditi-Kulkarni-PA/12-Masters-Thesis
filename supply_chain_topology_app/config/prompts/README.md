# Prompts

Every instruction any agent receives, in any of the nine orchestration conditions.
`config/load_config.py`'s `get_instruction(agent_key, topology=None)` reads one file from
here and expands its `@include` directives recursively.

| Folder | Holds | Shared across |
|---|---|---|
| `agents/` | the five domain capability prompts and their output schemas | all nine, byte-identical |
| `shared/` | cross-cutting partials — guardrails, behaviour, dependency rules, the output contract, capability renderers | whichever conditions include each one |
| `coordinators/<name>/` | the coordinator, aggregator, planner and manager prompts for one condition | that condition only |
| `helper/` | `extract_descs.py`, `validate_specs.py` — authoring utilities, not loaded at runtime |  |
| `templates/` | currently empty |  |

Nine `coordinators/` subfolders, one per built topology, matching
`topologies/registry.py`.

**Lookup order for any `@name` is `shared/` → `agents/` → `coordinators/<topology>/`.**
A topology folder can add its own partial, but cannot shadow a `shared/` one, because
`shared/` is searched first.

## Rules that govern editing anything in here

- **No changelog or correction notes inside a partial.** Any file that gets `@include`d
  becomes part of a real system prompt, so a note about what used to be wrong leaks into
  the model's context. Record corrections in a README, never in the included file. See
  [`shared/README.md`](shared/README.md).
- **Never edit a domain prompt per topology.** A `diff` between two conditions' view of
  `agents/*.md` showing any difference is a build defect, not a design choice. See
  [`agents/README.md`](agents/README.md).

## Where the rest is documented

The include mechanism, the composition chains, the capability renderers, the full diff of
which partials each of the nine conditions actually receives, and the two controlled
contrasts confirmed at the prompt level:

[`docs/thesis-topology-tradeoffs/topologies/prompt-modularization.md`](../../../docs/thesis-topology-tradeoffs/topologies/prompt-modularization.md)

### Regenerating that diff

The per-condition partial table in that document goes stale as soon as a coordinator
prompt gains or loses an `@include`. Rebuild the raw lists with:

```bash
# from supply_chain_topology_app/config/prompts/
for d in coordinators/*/; do
  echo "── $(basename "$d") ──"
  for f in "$d"*.md; do
    [ "$(basename "$f")" = README.md ] && continue
    echo "  $(basename "$f"): $(grep -oE '^@[A-Za-z0-9_]+$|^@@capabilities:[a-z_]+$' "$f" | tr '\n' ' ')"
  done
done
```

Composition chains resolve one level further — check `shared/` the same way for any
partial that is itself only a list of `@include`s.
