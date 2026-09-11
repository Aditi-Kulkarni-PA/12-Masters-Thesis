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

56 columns. Grouped by what they answer:

| Group | Columns | Answers |
|---|---|---|
| **Identity** | `run_id`, `query_id`, `topology`, `selected_topology`, `run_n`, `experiment_no`, `run_phase`, `model` | which cell of the design is this, and which experiment it belongs to |
| **Provenance** | `config_hash`, `prompt_versions_json`, `capture_version`, `env_config_json`, `query_text_actual` | what exactly was executed |
| **Batch** | `run_phase`, `batch_id`, `execution_order` | which invocation produced it, and where in that invocation's order |
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
| `experiment_no` | which experiment the run belongs to — one `run_phase` at one model, resolved through the `experiment` table. An experiment covers the full 9 × 11 grid at N repetitions and may span several `batch_id` values, so this is the filter for a complete measurement set. `NULL` for an ad-hoc `execute_topology.sh` run, which belongs to no campaign and is excluded from every aggregate |

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
| No duplicate cells | `GROUP BY experiment_no, topology, query_id, run_n HAVING COUNT(*) > 1` |
| Every run resolves its log | every `log_path` exists on disk |
| No fabricated start times | no two runs share `started_at` to the millisecond |
| Timing values reproduce | recompute `scheduling_values()` over stored `tool_call` rows and diff |
| Row count equals the design | `COUNT(*)` = topologies × queries × repetitions |

---

## 6. Common queries

Open the store with `sqlite3 supply_chain_topology_app/data/run_store.db`, or
`.headers on` / `.mode column` first for readable output.

Two rules apply to nearly everything below. **Filter to one experiment** — a figure
pooled across experiments mixes conditions. **Filter `query_id != 'Q1'`** for workload
figures — Q1 is the out-of-scope probe, where declining is correct, so including it
depresses coverage and completion.

### Two ways to select an experiment

Both forms appear in the examples below, the second as a trailing comment. They work
identically on `run`, `tool_call` and the `agg_*` tables, all of which carry
`experiment_no`, `run_phase` and `model`.

| Form | Query | When |
|---|---|---|
| **By number** | `WHERE experiment_no = 3` | the normal case — one clause, and it holds however many batches the experiment took |
| **By pair** | `WHERE run_phase = 'pilot' AND model = 'gpt-5.4'` | when the phase and tier are what you have in mind rather than a number. `run_phase` alone spans every tier in that phase |

Experiments: **1 = pilot/gpt-5.4-nano, 2 = pilot/gpt-5.4-mini, 3 = pilot/gpt-5.4**. List
them with:

```bash
uv run python supply_chain_topology_app/measurement/run_store_schema.py --list-experiments
```

`model` alone is not a substitute for either form: it selects every experiment at that
tier, which pools the pilot with the main experiment once both exist.

### Shape of the data

```sql
-- what is in the store, by tier and phase
SELECT model, run_phase, COUNT(*) AS runs
FROM run GROUP BY model, run_phase ORDER BY model;

-- the design grid: is every cell filled?
SELECT topology, COUNT(DISTINCT query_id) AS queries, COUNT(*) AS runs
FROM run WHERE experiment_no = 3                    -- or: model='gpt-5.4' AND run_phase='pilot'
GROUP BY topology ORDER BY topology;

-- run status spread for one tier
SELECT topology, run_status, COUNT(*) FROM run
WHERE experiment_no = 3                             -- or: model='gpt-5.4' AND run_phase='pilot'
GROUP BY topology, run_status ORDER BY topology;
```

### Cost, tokens and latency

> **Specialist cost and tokens are not columns on `run`.** Only `master_*`,
> `orchestration_*` and `grand_total_*` are stored; the specialist group is derived by
> summing `tool_call` rows, which is what `measures.token_split()` and
> `measures.cost_split()` do. Any query wanting the three-way split has to join.

```sql
-- headline per topology, workload only
SELECT topology,
       ROUND(AVG(grand_total_cost_usd), 4) AS cost,
       ROUND(AVG(grand_total_tokens))      AS tokens,
       ROUND(AVG(wall_time_s), 1)          AS wall_s,
       ROUND(AVG(critical_path_s), 1)      AS critical_s
FROM run
WHERE experiment_no = 3 AND query_id != 'Q1'        -- or: model='gpt-5.4' AND query_id!='Q1'
GROUP BY topology ORDER BY cost;

-- where the tokens go: specialist (from tool_call) vs orchestration vs master
SELECT r.topology,
       ROUND(AVG(t.spec))                       AS specialist,
       ROUND(AVG(r.orchestration_total_tokens)) AS orchestration,
       ROUND(AVG(r.master_total_tokens))        AS master
FROM run r
LEFT JOIN (SELECT run_id, SUM(total_tokens) AS spec FROM tool_call GROUP BY run_id) t
       ON t.run_id = r.run_id
WHERE r.experiment_no = 3 AND r.query_id != 'Q1'
GROUP BY r.topology ORDER BY orchestration DESC;

-- cost by complexity bin
SELECT q.complexity_bin, r.topology, ROUND(AVG(r.grand_total_cost_usd), 4) AS cost
FROM run r JOIN query_metadata q USING (query_id)
WHERE r.experiment_no = 3 AND r.query_id != 'Q1'
GROUP BY q.complexity_bin, r.topology
ORDER BY q.complexity_bin, cost;
```

### Reliability and scheduling

```sql
-- dependency violations, by topology
SELECT topology, COUNT(*) AS runs,
       SUM(CASE WHEN dependency_violations_json NOT IN ('[]','') 
                 AND dependency_violations_json IS NOT NULL THEN 1 ELSE 0 END) AS with_violation
FROM run WHERE experiment_no = 3 GROUP BY topology ORDER BY with_violation DESC;

-- scheduling: deviation is comparable, efficiency is not (see measure-definitions.md)
SELECT topology,
       ROUND(AVG(scheduling_deviation), 3) AS deviation,
       SUM(infeasible_overlap)             AS infeasible_runs,
       COUNT(scheduling_ratio)             AS defined_on
FROM run WHERE experiment_no = 3 GROUP BY topology ORDER BY deviation;

-- runs that finished but executed nothing
SELECT topology, query_id, run_status, behaviour_class
FROM run
WHERE experiment_no = 3 AND query_id != 'Q1'
  AND run_id NOT IN (SELECT DISTINCT run_id FROM tool_call);
```

### Quality

```sql
-- judge scores per topology, raw and scope-adjusted
SELECT r.topology,
       ROUND(AVG(s.judge_mean), 3)           AS judge_mean,
       ROUND(AVG(s.judge_mean_scope_adj), 3) AS scope_adjusted,
       COUNT(*)                              AS n
FROM run r JOIN quality_scores s USING (run_id)
WHERE r.experiment_no = 3 AND r.query_id != 'Q1'
GROUP BY r.topology ORDER BY scope_adjusted DESC;

-- runs missing a capability the query implied
SELECT r.topology, r.query_id, s.missing_implied_capabilities_json
FROM run r JOIN quality_scores s USING (run_id)
WHERE s.missing_implied_capabilities_json NOT IN ('[]','')
  AND s.missing_implied_capabilities_json IS NOT NULL
  AND r.experiment_no = 3;
```

### Tool-call level

```sql
-- per-capability duration and failure count
SELECT tool_name, COUNT(*) AS calls,
       ROUND(AVG(duration_s), 1) AS avg_s,
       SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END)  AS errors,
       SUM(CASE WHEN empty_payload = 1 THEN 1 ELSE 0 END)  AS empty
FROM tool_call t JOIN run r USING (run_id)
WHERE r.experiment_no = 3 GROUP BY tool_name ORDER BY calls DESC;

-- over-execution: calls the query never implied
SELECT r.topology, COUNT(*) AS unprompted_calls
FROM tool_call t JOIN run r USING (run_id)
WHERE t.unprompted = 1 AND r.experiment_no = 3
GROUP BY r.topology ORDER BY unprompted_calls DESC;

-- the call sequence of one run, in execution order
SELECT call_order, tool_name,
       ROUND(started_offset_s, 2) AS start_s,
       ROUND(ended_offset_s, 2)   AS end_s,
       ROUND(duration_s, 2)       AS dur_s
FROM tool_call WHERE run_id = '<run_id>' ORDER BY call_order;
```

### Reading the aggregates

`agg_topology` and `agg_query` are rebuilt by `analysis/aggregate.py`; query them rather
than recomputing when a figure already exists there. Both are keyed by `experiment_no` and
carry `run_phase` and `model` alongside, so the same filter works here as on `run` and
`tool_call`.

```sql
-- one measure across topologies, workload scope
SELECT topology, ROUND(median, 4) AS median, ROUND(rate, 3) AS rate, count
FROM agg_topology
WHERE experiment_no = 3 AND scope = 'workload' AND measure = 'capability_coverage'
ORDER BY median DESC;

-- what measures and scopes exist
SELECT DISTINCT measure FROM agg_topology ORDER BY measure;
SELECT DISTINCT scope   FROM agg_topology;   -- workload | out_of_scope | bin:<name>
```

### The report scripts' own queries

These are the shapes the reporting code actually runs. Reuse them rather than writing a
variant, so an ad-hoc check and the printed report cannot disagree.

**The headline table** — `analysis/aggregate.py` pivots `agg_topology` from long to wide.
Every column in the printed report comes from one `measure` row. Note which statistic each
takes: `median` for distributions, `mean` for bounded measures, `rate` for proportions.

```sql
SELECT model, topology,
       MAX(CASE WHEN measure='cost_usd'                THEN median END) AS cost,
       MAX(CASE WHEN measure='total_tokens'            THEN median END) AS total_tok,
       MAX(CASE WHEN measure='generated_tokens'        THEN median END) AS gen_tok,
       MAX(CASE WHEN measure='wall_time_s'             THEN median END) AS latency,
       MAX(CASE WHEN measure='critical_path_s'         THEN median END) AS critpath,
       MAX(CASE WHEN measure='orchestration_token_share' THEN median END) AS orch_share,
       MAX(CASE WHEN measure='specialist_token_share'  THEN median END) AS spec_share,
       MAX(CASE WHEN measure='judge_mean_scope_adj'    THEN median END) AS quality,
       MAX(CASE WHEN measure='capability_coverage'     THEN mean   END) AS coverage,
       MAX(CASE WHEN measure='capability_coverage_at_optimum'  THEN rate END) AS cover_opt,
       MAX(CASE WHEN measure='capability_precision'    THEN mean   END) AS precision,
       MAX(CASE WHEN measure='capability_precision_at_optimum' THEN rate END) AS prec_opt,
       MAX(CASE WHEN measure='scheduling_efficiency'   THEN median END) AS sched_eff,
       MAX(CASE WHEN measure='scheduling_deviation'    THEN mean   END) AS deviation,
       MAX(CASE WHEN measure='infeasible_overlap'      THEN rate   END) AS infeasible,
       MAX(CASE WHEN measure='has_violation'           THEN rate   END) AS violation_rate,
       MAX(CASE WHEN measure='completed'               THEN rate   END) AS completion_rate
FROM agg_topology
WHERE scope = 'workload' AND model = 'gpt-5.4'
GROUP BY model, topology;
```

**One run in full** — what `cli/report_topology_run.py` prints. Pass a `run_id` prefix;
`LIKE 'abc%'` is enough, the ids are UUIDs.

```sql
-- the run listing
SELECT run_id, topology, model, run_n, started_at, run_status, lock_rows
FROM run ORDER BY started_at DESC LIMIT 20;

-- one run, and everything attached to it
SELECT * FROM run            WHERE run_id LIKE 'a40c4ac6%';
SELECT * FROM tool_call      WHERE run_id LIKE 'a40c4ac6%' ORDER BY call_order;
SELECT * FROM quality_scores WHERE run_id LIKE 'a40c4ac6%';
SELECT * FROM query_metadata WHERE query_id = (
    SELECT query_id FROM run WHERE run_id LIKE 'a40c4ac6%');

-- the most recent run, when you just want the last thing that happened
SELECT * FROM run ORDER BY started_at DESC LIMIT 1;
```

**Before a batch** — what `delete_topology_run.sh` and the resume logic look at.

```sql
-- combos that already have a usable run, so resume will skip them
SELECT topology, query_id, run_n, model FROM run
WHERE run_status IN ('success','partial') ORDER BY topology, query_id;

-- what a deletion would take, and what it would spare
SELECT lock_rows, run_status, COUNT(*) FROM run GROUP BY lock_rows, run_status;
```

### Provenance and audit

```sql
-- confirm one tier used one configuration throughout
SELECT model, config_hash, COUNT(*) FROM run GROUP BY model, config_hash;

-- trace a figure back to its log
SELECT run_id, topology, query_id, log_path FROM run
WHERE experiment_no = 3 AND query_id = 'Q8';

-- what is protected from deletion
SELECT lock_rows, COUNT(*) FROM run GROUP BY lock_rows;
```

---

[← Documentation index](../README.md)
