# Harness Scripts — the command-line surface

[← Documentation index](../README.md) · [Experimental design](experimental-design.md)

Reference for the shell scripts in `scripts/`. Each pins the virtual environment to
the repository and loads `.env`, so they behave identically however they are invoked.
Four of them drive the measurement workflow; the fifth (`run_chat_app.sh`, section 8)
launches the demonstration UI and records nothing.

Where a script's behaviour has caused a mistake, that is called out — resume-by-default
and the `.env` tier in particular.

---

## Contents

| # | Script / topic |
|---|---|
| 1 | [Overview](#1-overview) |
| 2 | [`execute_topology.sh` — run one topology once](#2-execute_topologysh--run-one-topology-once) |
| 3 | [`run_experiment.sh` — plan and run a batch](#3-run_experimentsh--plan-and-run-a-batch) |
| 4 | [`delete_topology_run.sh` — remove runs from the store](#4-delete_topology_runsh--remove-runs-from-the-store) |
| 5 | [After a batch finishes](#5-after-a-batch-finishes-one-command) |
| 6 | [`generate_metrics_report.sh` — the reporting pipeline](#6-generate_metrics_reportsh--the-full-reporting-pipeline) |
| 7 | [Keeping a long batch running: `caffeinate`](#7-keeping-a-long-batch-running-caffeinate) |
| 8 | [`run_chat_app.sh` — the Gradio delivery app](#8-run_chat_appsh--the-gradio-delivery-app) |

---

## 1. Overview

This is the workflow that produces the thesis data, alongside the Gradio delivery app: the
orchestration-topology comparison for the thesis. Each of the topologies under
`topologies/` (see `topologies/registry.py` — the single source of truth for which
topologies exist and how each is built) answers the same frozen query set
(`measurement/query_set_v1.xlsx` / `query_metadata` table) and every run is scored and
persisted to `data/run_store.db` via `measurement/run_store_writer.py`. Four scripts in
the repo-root `scripts/` folder drive this workflow; all four `cd` to the repo root and
pin `UV_PROJECT_ENVIRONMENT` so they always use the repo's own `.venv`, regardless of
which directory they were invoked from.

## 2. `execute_topology.sh` — run one topology once

Executes a single topology against a single query and writes one row to the run store
(via `execute_topology.py`). This is the unit every other script builds on.

```bash
./scripts/execute_topology.sh                          # defaults: planner_executor, run_n=1
./scripts/execute_topology.sh swarm                    # named topology, run_n=1
./scripts/execute_topology.sh planner_executor 3        # topology + run_n
./scripts/execute_topology.sh --list                    # show enabled topologies + full registry
./scripts/execute_topology.sh --cache planner_executor  # allow freshness/cache reuse (NOT for measurement)
SC_QUERY_ID=Q4 ./scripts/execute_topology.sh sequential  # pick a query from the frozen set (default Q11)
```

Key behavior:
- **Measurement mode is on by default** (`NO_CACHE=1`, `SC_DEV_PATH_FALLBACK=0`) — every
  run executes independently with no cache or freshness-skip shortcuts, which is required
  for latency/cost figures to be comparable across runs (Risk Log R16, R10). `--cache`
  turns this off for exploratory use only; any run intended for the thesis must not use it.
- `TOPOLOGY` (first positional arg, default `planner_executor`) must be uncommented in the
  script's own `TOPOLOGIES` array, which is kept in sync with `topologies/registry.py` —
  this rejects a topology whose orchestration code doesn't exist yet before any API call
  is made.
- `RUN_N` (second positional arg, default `1`) is the repetition number recorded on the
  run. It must be passed positionally, not via an `SC_RUN_N` environment variable — the
  script always computes and exports `SC_RUN_N` from this argument itself, so an
  ambient/pre-set env var of the same name is silently overridden (this is why
  `run_experiment.sh` below invokes it positionally too).
- `SC_QUERY_ID` (env var, optional) selects a query from `query_metadata`; unset defaults
  to Q11 (the full workflow).

## 3. `run_experiment.sh` — plan and run a batch

Thin wrapper around `run_experiment.py`, which plans the cross-product of
topologies × queries × repetitions and executes each combination as its own
`execute_topology.sh` subprocess (one per run — `execute_topology.py` resolves its
topology at import time, so it cannot be reused for a second topology in the same
process). Every run in the batch is stamped with a shared `batch_id` and a per-run
`execution_order`.

```bash
./scripts/run_experiment.sh                          # all topologies, all queries, N=3
./scripts/run_experiment.sh -q all -r all -n 1        # pilot: N=1 rep, everything else default
./scripts/run_experiment.sh -q 1,2,3 -r 1,2           # specific queries, specific repetitions
./scripts/run_experiment.sh -t swarm,mesh -q 6        # specific topologies + one query
./scripts/run_experiment.sh --dry-run                 # preview the plan, no API calls
./scripts/run_experiment.sh --list                    # show built topologies and frozen queries
./scripts/run_experiment.sh --no-resume               # re-run combos even if already done
```

| Option | Meaning |
|---|---|
| `-t`, `--topology` | `all` (every **built** topology per the registry) or a comma-list of names. Default `all`. |
| `-q`, `--query` | `all` (every frozen query) or a comma-list of numbers/ids, e.g. `1,2,3` or `Q1,Q2`. Brackets optional (`[1,2,3]` also accepted). Default `all`. |
| `-r`, `--reps` | `all` (expands to `run_n` 1..`--total-reps`) or a comma-list of specific `run_n` values, e.g. `1,2`. Default `all`. |
| `-n`, `--total-reps` | What `-r all` expands to. Default `3`; pass `1` for a single-pass pilot. |
| `-b`, `--batch` | Batch label recorded on every run in this invocation. Default: auto-generated UTC timestamp. |
| `--run-phase` | `run_phase` value recorded on every run. Default `pilot`. |
| `--no-resume` | Re-run combinations that already have a `success`/`partial` run (resume is ON by default). |
| `--no-shuffle` | Execute in plan order instead of a randomized execution order (randomized is the default). |
| `--dry-run` | Print the planned runs and exit; no API calls. |
| `--list` | List built topologies and frozen query ids, then exit. |

Key behavior:
- **Resume is on by default.** A `(topology, query_id, run_n)` combination that already
  has a `success` or `partial` row in `run` is skipped, so stopping the harness partway
  (Ctrl-C) and re-invoking the same command later continues rather than duplicates work.
  `--no-resume` disables this.
- **Execution order is randomized by default** (`--no-shuffle` to disable), and every run
  is stamped with the batch's `batch_id` plus its own `execution_order` — this is what
  lets a single batch be distinguished from ordinary timestamp ordering, and lets an
  interrupted batch's partial results be identified afterward.
- Each planned run is dispatched as `bash execute_topology.sh <topology> <run_n>` with
  `run_n` passed positionally (per the note above) and `SC_QUERY_ID`, `SC_BATCH_ID`,
  `SC_EXECUTION_ORDER`, `SC_RUN_PHASE` set as env vars for that subprocess only.
- **Console output stays high-level; detail goes to a file.** `execute_topology.sh`/`.py`
  print a lot per run (banners, turn text, tool/cost tables, run-validity checks) — fine
  for one manual run, unreadable across a batch of dozens. Each run's full stdout/stderr
  is captured to its own file under `log/batches/<batch_id>/`, named
  `<execution_order (3-digit)>_<topology>_<query_id>_n<run_n>.log`
  (e.g. `007_static_graph_dag_Q6_n2.log`); this process's own console output stays to one
  line per run (`[order/total] topology query run_n ... OK/FAILED (log filename)`).
  Runs with `PYTHONUNBUFFERED=1` so `tail -f` on a run's log shows it filling in live,
  not only once that run finishes.
- Writes `log/batches/<batch_id>/summary.json` when the batch finishes OR is
  interrupted — planned/succeeded/failed/remaining counts plus each run's log filename —
  useful for inspecting a stopped batch without querying the database directly.

## 4. `delete_topology_run.sh` — remove runs from the store

Deletes rows from the run store (and their child `tool_call`/`quality_scores` rows),
sparing locked runs by default.

```bash
./scripts/delete_topology_run.sh                    # --unlocked (default)
./scripts/delete_topology_run.sh --unlocked         # every run with lock_rows = 0
./scripts/delete_topology_run.sh --all              # EVERYTHING, locked runs included
./scripts/delete_topology_run.sh --dry-run          # show what would go, delete nothing
./scripts/delete_topology_run.sh --orphan-logs      # also sweep log files no run claims
./scripts/delete_topology_run.sh --unlocked <run_id> [run_id ...]   # specific run ids only
```

Key behavior:
- **Default scope is `--unlocked`, not `--all`**, on purpose: a locked run is one whose
  numbers the thesis cites, and deleting one is an irreversible, real-API-spend mistake.
  `--all` additionally requires typing `delete <N>` (the exact count about to be deleted)
  before anything is removed.
- Always previews the damage first (run count, affected child-row counts, log-file count,
  and — for `--unlocked` — how many locked runs are being spared) before asking for any
  confirmation.
- `--dry-run` shows the same preview and exits without deleting anything.
- `--orphan-logs` additionally sweeps log files that no remaining run row references.

## 5. After a batch finishes: one command

```bash
./scripts/generate_metrics_report.sh
```

That is the whole refresh. The script runs both stages of the reporting pipeline in
order, because the second reads what the first writes:

1. **`backfill_scheduling.py`** computes the execution-timing measures for every run and
   writes seven values onto each run row: `critical_path_s`, `actual_span_s`,
   `fully_serial_s`, `coordinator_idle_s`, `scheduling_ratio`, `scheduling_deviation`
   and `infeasible_overlap`. These are derived from the `tool_call` start/end offsets
   already stored plus the static dependency table, so no run is re-executed. A run with
   no capability tool calls keeps NULLs, because the measure is undefined rather than
   zero for a correct decline.
2. **`analysis/aggregate.py`** rebuilds `agg_query` and `agg_topology` and prints the
   report.

Stage 1 exists as a separate step because `run_experiment.sh` does not compute the
timing measures at write time. It used to have to be invoked by hand, which meant a
report run straight after a batch silently omitted critical-path latency, achievable and
actual concurrency, and scheduling deviation for the new runs. Folding it into the script
removes that failure mode: there is no longer a way to produce a report with stale timing
columns by forgetting a step.

`--skip-timing` omits stage 1. It is only worth using when the timing measures are known
to be current and the store is large enough that recomputing them is not worth the wait.

A note on the infeasibility tolerance used in stage 1: a ratio marginally above 1.0 is
timing noise rather than a dependency breach. Offsets are stored to two decimal places,
so a correctly scheduled run of short calls can compute to 1.002. The tolerance is 0.02,
set between the two groups observed in the pilot — every run from 1.046 upward carries a
recorded dependency violation, and every run below it carries none.

## 6. `generate_metrics_report.sh` — the full reporting pipeline

Runs both reporting stages against `data/run_store.db`: `backfill_scheduling.py` for the
execution-timing measures, then `analysis/aggregate.py`, which derives the per-run
measures (`analysis/measures.py`) and rebuilds two tables — `agg_query` (one row per
model/topology/query/measure) and `agg_topology` (one row per model/topology/scope/
measure). This is the pipeline behind the tracker's Model Comparison, Full Measures
Comparison and Complexity Bins tabs, and the single command to run after any batch.

```bash
./scripts/generate_metrics_report.sh                          # FULL REPORT: every model, every view
./scripts/generate_metrics_report.sh --model gpt-5.4-mini      # one tier only
./scripts/generate_metrics_report.sh --no-bins                 # topology summary only
./scripts/generate_metrics_report.sh --no-print                # complexity bins only
./scripts/generate_metrics_report.sh --no-print --no-bins      # rebuild the tables, print nothing
./scripts/generate_metrics_report.sh --skip-timing             # skip the timing stage
./scripts/generate_metrics_report.sh --db path/to/other.db     # report on a different store
```

Key behavior:
- **Runs both stages in order.** The timing stage writes the columns the aggregation
  stage reads, so the sequence is fixed and handled by the script rather than left to
  the caller. `--db` is passed to both stages.
- **Makes no API calls and re-executes no run.** Both stages derive everything from what
  is already in the store — tool-call offsets, token counts and judge scores — so
  running this as often as you like costs nothing and cannot alter a measurement.
- **Rebuilds `agg_query`/`agg_topology` from scratch every time** (drop and recreate),
  not an incremental update — both tables are fully derived from `run`, so a rebuild
  cannot lose data, and it guarantees no row survives from a since-changed measure
  definition.
- **The full report is the default.** Both views print with no flags: the topology
  summary and the complexity-bin breakdown. `analysis/aggregate.py` itself makes each
  view opt-in via `--print` and `--bins`; this wrapper inverts that, because running it
  by hand means wanting to see everything. Suppress a view with `--no-print` or
  `--no-bins`; passing both rebuilds the tables silently.
- **Re-running is safe and idempotent.** The timing stage recomputes and overwrites
  every value from the stored offsets; the aggregation stage drops and rebuilds both
  tables. Running the script twice in a row produces identical output, so it cannot
  double-count or drift. Restricting to one tier with `--model` replaces only that
  tier's rows and leaves the others intact (Risk Log R66).
- **The printed summary carries every measure in the proposal's Section 7.3 table**,
  split across two blocks so each line stays readable: operational measures (cost,
  total and generated tokens, latency, critical path, orchestration and specialist
  token shares) and reliability/quality measures (scope-adjusted quality, coverage,
  precision, scheduling efficiency and deviation, infeasible overlap, violation rate,
  completion rate, and the out-of-scope decline rate). Both blocks list the topologies
  in the same order so the rows line up between them.
- Cost, tokens, latency and quality are medians across the 10 workload queries;
  capability coverage, capability precision and scheduling deviation show the mean
  instead, plus the share of queries sitting exactly at the optimum — the median
  reports the optimum value for every topology on these three measures and separates
  none of them (see the `BOUNDED_OPTIMUM` comment in `analysis/aggregate.py`).
- **`qrys` is the denominator and should be read first.** A topology that failed some
  queries carries a smaller one, and its cost, token and latency figures exclude those
  runs entirely, because a failed run stores no cost or timing. A topology that fails
  its most demanding queries therefore looks cheaper and faster than one that completed
  them.

## 7. Keeping a long batch running: `caffeinate`

`run_experiment.sh` against the full topology x query grid can run for hours. macOS
will sleep the machine (or at minimum the display) partway through if nothing prevents
it, which pauses the batch along with everything else. `caffeinate` keeps the machine
awake for the duration of a command and exits on its own when that command finishes —
nothing to remember to turn back off afterward.

```bash

caffeinate -i ./scripts/run_experiment.sh -t all -q all -r 3 -b my-batch-20260906
```

`-i` prevents idle sleep for as long as the wrapped command runs. Run this from a
second terminal tab/window if you want to keep working in the first while the batch
runs in the background — `caffeinate` and the batch both live in that second terminal
and quitting it stops both.

This is a shell-level safeguard, not specific to this repo: it works the same way in
front of any long-running command (`generate_metrics_report.sh`, a `pytest` run, and so
on), though the aggregation script itself typically finishes in seconds and rarely
needs it.

## 8. `run_chat_app.sh` — the Gradio delivery app

The delivery app is the demonstration surface for the substrate, not part of the
measurement workflow: it writes nothing to the run store. It is listed here only so the
`scripts/` folder is fully accounted for.

```bash
./scripts/run_chat_app.sh                 # start the UI on http://127.0.0.1:7860
SC_NO_CACHE=1 ./scripts/run_chat_app.sh   # no response cache, no freshness reuse
```

| Behaviour | Detail |
|---|---|
| Topology | planner-executor only, imported directly. Not selectable |
| Protocol | one chat message, one `run(stream=True)` call, one `MasterOutput`. No turn protocol |
| MCP server | spawned by the app as a stdio subprocess; nothing to start separately |
| Run store | untouched. For a recorded run use `execute_topology.sh` |

**Why it is not topology-selectable.** The UI resolves its own plan confirmation in chat
and then sends a single message, so it needs a condition that finishes in one call and
streams. Only planner-executor does both while also producing the five canonical tool
names the result tabs dispatch on. Four conditions plan or triage on turn 1 and execute
on turn 2; three finish in one call but are custom coordinators with no `stream`
parameter; monolith streams but attaches raw MCP tools rather than the five capability
wrappers. Making the app drive the harness's two-turn protocol was tried and reverted —
it imports a measurement contract into a place that has no need of one.

---

[← Documentation index](../README.md) · [Experimental design](experimental-design.md)
