# Execution Flow — from a planned run to a reported figure

[← Documentation index](../README.md)

Every number in the thesis is produced by one chain of six stages. This document is the
authoritative description of that chain: what each stage consumes, what it writes, and
what would be lost if it were skipped.

---

## 1. The chain at a glance

```
                    ┌──────────────────────────────────────────────┐
                    │  run_experiment.py                           │
  batch plan  ───▶  │  plans topology x query x repetition,        │
                    │  shuffles, and invokes one child per run     │
                    └───────────────────┬──────────────────────────┘
                                        │  one subprocess per run
                                        │  context passed via environment:
                                        │  SC_TOPOLOGY, SC_QUERY_ID, SC_RUN_N,
                                        │  SC_BATCH_ID, SC_EXECUTION_ORDER
                                        ▼
                    ┌──────────────────────────────────────────────┐
                    │  execute_topology.py                         │
                    │  resolves the topology, runs the two turns,  │
                    │  records every tool call through RunRecorder │
                    └───────────────────┬──────────────────────────┘
                                        │  RunRecorder (in memory)
                                        ▼
                    ┌──────────────────────────────────────────────┐
                    │  measurement/run_store_writer.py             │
                    │  write_run(): derives run_status, dependency │
                    │  violations, scheduling values; INSERTs the  │
                    │  run row and its tool_call rows              │
                    └───────────────────┬──────────────────────────┘
                                        │  run_store.db
                          ┌─────────────┴─────────────┐
                          ▼                           ▼
        ┌───────────────────────────┐   ┌──────────────────────────────┐
        │  score_topology_run.py    │   │  backfill_scheduling.py      │
        │  judges the persisted     │   │  repairs timing values for   │
        │  artifacts; writes        │   │  runs written before the     │
        │  quality_scores           │   │  write path computed them    │
        └─────────────┬─────────────┘   └──────────────┬───────────────┘
                      └─────────────┬─────────────────-┘
                                    ▼
                    ┌──────────────────────────────────────────────┐
                    │  analysis/aggregate.py                       │
                    │  rebuilds agg_query and agg_topology across   │
                    │  workload, out-of-scope and complexity-bin    │
                    │  scopes                                       │
                    └───────────────────┬──────────────────────────┘
                                        ▼
                        thesis tables, the tracker workbook,
                        and the model-comparison report
```

---

## 2. Stage by stage

| # | Stage | Reads | Writes | Skipping it costs |
|---|---|---|---|---|
| 1 | `run_experiment.py` | frozen query set, topology registry | one child process per run; a console log per run under `log/batches/<batch>/` | no batch identity, no execution order, no randomised order |
| 2 | `execute_topology.py` | `SC_*` environment, prompts, tools | `RunRecorder` in memory; a trace under `log/batches/<batch>/traces/` | nothing runs |
| 3 | `run_store_writer.write_run()` | `RunRecorder`, `query_metadata` | `run` + `tool_call` rows | no measurement exists at all |
| 4 | `score_topology_run.score_run()` | `run.all_turns_json`, `tool_call.output_text` | `quality_scores` row | no quality measure; every other measure survives |
| 5 | `backfill_scheduling.py` | `tool_call` offsets | timing columns on `run` | nothing, for runs written after the write path computed them; it is now a repair tool |
| 6 | `analysis/aggregate.py` | `run`, `tool_call`, `quality_scores` | `agg_query`, `agg_topology` | no aggregated figure; per-run data is intact |

**Stages 4 and 5 are independent of each other** and both read only what stage 3 persisted.
Neither can change a run's execution; both can be re-run at any time.

---

## 3. What is derived where

A recurring question when tracing a thesis figure back to its source is *which stage
decided it*. Derivation happens in three places, and only three:

| Decided at | Examples | Why there |
|---|---|---|
| **Write time** (stage 3) | `run_status`, `dependency_violations_json`, `scheduling_ratio`, `critical_path_s`, `actual_span_s`, `coordinator_idle_s` | needs the in-memory tool-call record, including offsets, that only exists during the run |
| **Score time** (stage 4) | `judge_mean`, `judge_mean_scope_adj` | needs an LLM judge call, so it is deliberately separable and repeatable |
| **Aggregate time** (stage 6) | `capability_coverage`, `capability_precision`, `orchestration_tax`, token and cost splits, every rate | derived arithmetic over stored values; recomputing costs nothing and cannot drift |

Nothing is derived twice. Where a value could be computed in two places, it is computed
once and read everywhere else — see `measurement.dependencies.scheduling_values()`, which
both the write path and the backfill call.

---

## 4. Run identity, and how artefacts line up

One run produces three artefacts in three places. They share a filename stem so that any
one can be found from any other without querying the database:

| Artefact | Path |
|---|---|
| Console log | `log/batches/<batch_id>/<order>_<topology>_<query>_n<run_n>.log` |
| Trace log | `log/batches/<batch_id>/traces/<order>_<topology>_<query>_n<run_n>.log` |
| Swarm agent instructions | `runs/<topology>/<batch_id>/<order>_<topology>_<query>_n<run_n>/` |
| Database row | `run.run_id` (a UUID), with `run.log_path` pointing at the trace |

The stem is built by `helpers.logging_utils.run_slug()` from the same `SC_*` environment
the harness sets, so all three are produced from one definition.

---

## 5. Invariants worth checking after any change

These are cheap to verify and each one has failed at least once:

| Invariant | Check |
|---|---|
| Every run resolves its log | `SELECT log_path FROM run` — every path exists on disk |
| No duplicate cells | no two live rows share `(model, topology, query_id, run_n)` |
| Timing values agree between paths | recomputing `scheduling_values()` over stored `tool_call` rows reproduces the stored columns |
| Generation parameters are identical across conditions | `python supply_chain_topology_app/cli/check_model_parity.py` |
| Aggregates match the run store | regenerate and diff `agg_topology` |

---

[← Documentation index](../README.md)
