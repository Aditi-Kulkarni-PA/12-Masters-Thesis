# Run Store Schema — what is persisted, and why

[← Documentation index](../README.md)

The run store (`supply_chain_topology_app/data/run_store.db`) is the single source of
truth for every reported figure. Logs are evidence; the database is the measurement.

---

## 1. Tables

| Table | Grain | Purpose |
|---|---|---|
| `run` | one row per run | the run and everything derived at write time |
| `tool_call` | one row per capability invocation | timing, tokens, cost and payload per call |
| `quality_scores` | one row per scored run | judge output, raw and scope-adjusted |
| `query_metadata` | one row per query | the frozen workload: required capabilities, complexity |
| `agg_query` | derived | query-level aggregation, rebuilt on demand |
| `agg_topology` | derived | topology-level aggregation, rebuilt on demand |
| `row_output` | per judged artifact | optional per-artifact detail |

`agg_query` and `agg_topology` are **fully derived** — dropped and rebuilt by
`analysis/aggregate.py`. Nothing is lost by deleting them.

---

## 2. The `run` table, grouped

55 columns. Grouped by what they answer:

| Group | Columns | Answers |
|---|---|---|
| **Identity** | `run_id`, `query_id`, `topology`, `selected_topology`, `run_n`, `model` | which cell of the design is this |
| **Provenance** | `config_hash`, `prompt_versions_json`, `capture_version`, `env_config_json`, `query_text_actual` | what exactly was executed |
| **Batch** | `run_phase`, `batch_id`, `execution_order` | which batch, and where in its order |
| **Outcome** | `run_status`, `failure_category`, `final_answer`, `all_turns_json` | what happened |
| **Behaviour** | `behaviour_class`, `clarification_appropriate`, `behaviour_rationale` | what it did *instead*, when it did not execute |
| **Cost & tokens** | `grand_total_*`, `master_*`, `orchestration_*` | what it consumed, split by group |
| **Timing** | `wall_time_s`, `critical_path_s`, `actual_span_s`, `fully_serial_s`, `coordinator_idle_s`, `tool_base_offset_s` | how long, and how well scheduled |
| **Scheduling** | `scheduling_ratio`, `scheduling_deviation`, `infeasible_overlap` | derived scheduling quality |
| **Correctness** | `dependency_violations_json`, `tool_call_count_*`, `missing_expected_tools_json`, `fabricated_narrative_json`, `path_fallback_used` | did it do the right work, in a valid order |
| **Housekeeping** | `started_at`, `recorded_at`, `log_path`, `lock_rows`, `excluded_reason` | audit and lifecycle |

### Columns whose semantics are easy to misread

| Column | Rule |
|---|---|
| `started_at` | the run's actual start. **NULL** when the run never reached the model — it is not stamped with write time |
| `recorded_at` | when the row was written. Coincides with `started_at` for a completed run; the two diverge only for a non-completion |
| `config_hash` | hashes **model + prompt versions only**. It does not cover generation parameters or topology source, so it cannot detect either kind of drift |
| `excluded_reason` | drops a run from every aggregate without deleting it. Reserved for a run that was *part of the design* and failed a validity check |
| `lock_rows` | marks a run whose numbers are cited; the delete tooling refuses to touch it by default |
| `selected_topology` | what a routing condition chose, where that differs from the condition itself |

---

## 3. `tool_call`

One row per capability invocation, 21 columns. The measurement-critical ones:

| Column | Purpose |
|---|---|
| `started_offset_s`, `ended_offset_s` | seconds from the run's first tool start; **all timing measures derive from these**, never from wall clock |
| `duration_s` | display only, rounded — not used in comparisons |
| `empty_payload` | tool returned valid JSON whose primary lists were all empty |
| `error` | an exception was raised. Distinct from `empty_payload` |
| `unprompted` | the capability was not implied by the query |
| `prompt_tokens`, `completion_tokens`, `cost_usd` | per-call consumption |
| `agent_messages` | internal round-trips, so a large prompt can be told from several repeated ones |

> `duration_s` is rounded to 2dp for display; adding it to a 3dp offset once produced a
> false premature-start violation. Comparisons use `ended_offset_s`, so both ends of every
> comparison are measured the same way.

---

## 4. Lifecycle rules

| Rule | Enforced by |
|---|---|
| A re-run replaces a previous non-completion for the same cell rather than adding a second row | `write_run()` and `write_failed_run()` |
| A locked run is never deleted without an explicit override | `lock_rows` + `delete_topology_run.sh` |
| Excluded runs are filtered from every aggregate | `aggregate.load_runs()` — `WHERE excluded_reason IS NULL` |
| Schema changes are additive and idempotent | `run_store_schema.migrate_schema()` |

---

## 5. Integrity checks

Each has caught a real defect:

| Check | Query |
|---|---|
| No duplicate cells | `GROUP BY model, topology, query_id, run_n HAVING COUNT(*) > 1` |
| Every run resolves its log | every `log_path` exists on disk |
| No fabricated start times | no two runs share `started_at` to the millisecond |
| Timing values reproduce | recompute `scheduling_values()` over stored `tool_call` rows and diff |
| Row count equals the design | `COUNT(*)` = topologies × queries × repetitions |

---

[← Documentation index](../README.md)
