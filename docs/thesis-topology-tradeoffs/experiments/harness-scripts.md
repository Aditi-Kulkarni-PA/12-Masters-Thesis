# Harness Scripts — the command-line surface

[← Documentation index](../README.md) · [Experimental design](experimental-design.md)

Reference for the shell scripts in `scripts/`. Each pins the virtual environment to
the repository and loads `.env`, so they behave identically however they are invoked.
Four of them drive the measurement workflow; the last (`execute_chat_app.sh`, section 10)
launches the demonstration UI and records nothing. Sections 7 and 8 cover three Python
tools that have no shell wrapper: two pre-flight checks and the human-eval exporter.

Where a script's behaviour has caused a mistake, that is called out — resume-by-default
and the `.env` tier in particular.

---

## Contents

| # | Script / topic |
|---|---|
| 1 | [Overview](#1-overview) |
| 2 | [`execute_topology.sh` — run one topology or a select topology-query combination once](#2-execute_topologysh--run-one-topology-or-a-select-topology-query-combination-once) |
| 3 | [`execute_experiment.sh` — plan and run a full experiment or an experiment batch](#3-execute_experimentsh--plan-and-run-a-full-experiment-or-an-experiment-batch) |
| 4 | [`delete_topology_run.sh` — remove runs from the store](#4-delete_topology_runsh--remove-runs-from-the-store) |
| 5 | [After a batch finishes](#5-after-a-batch-finishes-one-command) |
| 6 | [`generate_metrics_report.sh` — the reporting pipeline](#6-generate_metrics_reportsh--the-full-reporting-pipeline) |
| 7 | [Pre-flight checks: config freeze and figure sources](#7-pre-flight-checks-config-freeze-and-figure-sources) |
| 8 | [Human-eval export/import: `human_eval_io.py`](#8-human-eval-exportimport-human_eval_iopy) |
| 9 | [Keeping a long batch running: `caffeinate`](#9-keeping-a-long-batch-running-caffeinate) |
| 10 | [`execute_chat_app.sh` — the Gradio delivery app](#10-execute_chat_appsh--the-gradio-delivery-app) |

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

## 2. `execute_topology.sh` — run one topology or a select topology-query combination once

Executes a single topology against a single query and writes one row to the run store
(via `execute_topology.py`). This is the unit every other script builds on.

```bash
./scripts/execute_topology.sh                              # defaults: planner_executor, Q11, run_n 1
./scripts/execute_topology.sh -t swarm                     # named topology
./scripts/execute_topology.sh -t sequential -q Q4          # topology + query
./scripts/execute_topology.sh -t planner_executor -n 3     # topology + run_n
./scripts/execute_topology.sh --list                       # enabled topologies + full registry
./scripts/execute_topology.sh --cache -t planner_executor  # freshness/cache reuse (NOT for measurement)
```

| Option | Meaning |
|---|---|
| `-t`, `--topology` | One topology name. Default `planner_executor`. |
| `-q`, `--query` | One query id from the frozen set, `Q4` or `4`. Default Q11, the full workflow. |
| `-n`, `--run-n` | Repetition number recorded on the run. Default `1`. |
| `--cache` | Allow cache/freshness reuse. Exploratory use only. |
| `--list` | List topologies and exit. |

Key behavior:
- **Flags, not positional arguments or env vars.** `-t` and `-q` are the same flags
  `execute_experiment.sh` uses; this script takes one value each because it runs exactly
  one topology against exactly one query. The script exports `SC_TOPOLOGY`, `SC_QUERY_ID`
  and `SC_RUN_N` from its own flags, so a variable of the same name left exported in the
  calling shell cannot change what runs.
- **A bare positional argument is rejected**, not interpreted. The topology and `run_n`
  used to be positional, so accepting one silently would let an old invocation run against
  the wrong defaults instead of saying what changed.
- **Measurement mode is on by default** (`NO_CACHE=1`, `SC_DEV_PATH_FALLBACK=0`) — every
  run executes independently with no cache or freshness-skip shortcuts, which is required
  for latency/cost figures to be comparable across runs (Risk Log R16, R10). `--cache`
  turns this off for exploratory use only; any run intended for the thesis must not use it.
- **An unbuilt topology is rejected before any API call.** `-t` must name a topology
  uncommented in the script's own `TOPOLOGIES` array, kept in sync with
  `topologies/registry.py`.
- **A run launched here is ad-hoc.** It belongs to no experiment, so `experiment_no` is
  left `NULL` and the row stays unlocked. Aggregation selects by experiment, so an ad-hoc
  run never reaches a reported figure. If `SC_EXPERIMENT_NO` is set in the environment —
  a stray export from an earlier harness invocation, say — `execute_topology.py` checks it
  against the phase and model this process actually has and aborts on a mismatch.

## 3. `execute_experiment.sh` — plan and run a full experiment or an experiment batch

Thin wrapper around `execute_experiment.py`, which plans one repetition — every topology ×
query pair once — and executes each as its own `execute_topology.sh` subprocess (one per
run — `execute_topology.py` resolves its topology at import time, so it cannot be reused
for a second topology in the same process). Every run in the batch is stamped with the
experiment it belongs to, a shared `batch_id` and a per-run `execution_order`.

```bash
./scripts/execute_experiment.sh -e 4 --run-n 1                     # every topology x query pair once
./scripts/execute_experiment.sh --run-phase main --model gpt-5.4 --run-n 1 --new-experiment
./scripts/execute_experiment.sh -e 4 --run-n 1 -q 1,2,3            # three queries only
./scripts/execute_experiment.sh -e 4 --run-n 1 -t swarm,mesh -q 6  # specific topologies + one query
./scripts/execute_experiment.sh -e 4 --run-n 1 --dry-run           # preview the plan, no API calls
./scripts/execute_experiment.sh --list                             # built topologies and frozen queries
./scripts/execute_experiment.sh -e 4 --run-n 1 --no-resume         # re-run pairs even if already done
```

| Option | Meaning |
|---|---|
| `-t`, `--topology` | `all` (every **built** topology per the registry) or a comma-list of names. Default `all`. |
| `-q`, `--query` | `all` (every frozen query) or a comma-list of numbers/ids, e.g. `1,2,3` or `Q1,Q2`. Brackets optional (`[1,2,3]` also accepted). Default `all`. |
| `-e`, `--experiment` | The experiment to run under. Mutually exclusive with `--run-phase`/`--model`. |
| `--run-phase` | `pilot` or `main`. Use with `--model` instead of `-e`. |
| `--model` | The tier. Compared against `OPENAI_MODEL` as this process resolves it; a mismatch aborts before planning. |
| `--new-experiment` | Allocate a number for a `(run_phase, model)` pair that has none yet. Without it an unknown pair aborts and lists the experiments that do exist. Never allocates during `--dry-run`. |
| `--run-n` | **Required.** Which repetition this is. Every run in the batch carries it, so N=3 is three invocations with `--run-n 1`, `2`, `3`. |
| `-b`, `--batch` | Batch label recorded on every run in this invocation. Default: auto-generated UTC timestamp. |
| `--no-resume` | Re-run combinations that already have a `success`/`partial` run (resume is ON by default). |
| `--no-shuffle` | Execute in plan order instead of a randomized execution order (randomized is the default). |
| `--dry-run` | Print the planned runs and exit; no API calls, and nothing is written to the store. |
| `--list` | List built topologies and frozen query ids, then exit. |

Key behavior:
- **The experiment number is looked up, not typed.** The `experiment` table maps
  `(run_phase, model)` to a number with a uniqueness constraint, so `-e 4` and
  `--run-phase main --model gpt-5.4` name the same campaign and cannot disagree.
- **The model is stated twice on purpose.** `--model` names the tier and `.env` supplies
  it to the run; the harness aborts if the two differ. A batch executed at the wrong tier
  produces rows that cannot be told from valid ones afterwards.
- **Resume is on by default.** A `(run_phase, model, topology, query_id, run_n)` slot that
  already has a `success` or `partial` row in `run` is skipped, so stopping the harness
  partway (Ctrl-C) and re-invoking the same command later continues rather than duplicates
  work. `--no-resume` disables this. `run_phase` is part of the slot because the pilot is
  N=1 at every tier, so a main-experiment `run_n = 1` at the frozen tier would otherwise
  match its pilot row exactly and be skipped.
- **Execution order is randomized by default** (`--no-shuffle` to disable), and every run
  is stamped with the batch's `batch_id` plus its own `execution_order` — this is what
  lets a single batch be distinguished from ordinary timestamp ordering, and lets an
  interrupted batch's partial results be identified afterward.
- Each planned run is dispatched as `bash execute_topology.sh <topology> <run_n>` with
  `run_n` passed positionally (per the note above) and `SC_QUERY_ID`, `SC_BATCH_ID`,
  `SC_EXECUTION_ORDER`, `SC_RUN_PHASE`, `SC_EXPERIMENT_NO` set as env vars for that subprocess only.
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
./scripts/delete_topology_run.sh -e 4                               # one experiment
./scripts/delete_topology_run.sh --run-phase main --model gpt-5.4 --dry-run
```

Key behavior:
- **Campaign filters narrow, never widen.** `-e`, `--run-phase` and `--model` restrict the
  selection to one campaign so a batch written under the wrong phase can be removed without
  listing 99 run ids. The `--unlocked` guard still applies on top of them.
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

Stage 1 exists as a separate step because `execute_experiment.sh` does not compute the
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
experiment/topology/query/measure) and `agg_topology` (one row per experiment/topology/
scope/measure). This is the pipeline behind the tracker's Model Comparison, Full Measures
Comparison and Complexity Bins tabs, and the single command to run after any batch.

```bash
./scripts/generate_metrics_report.sh                          # FULL REPORT: every experiment, every view
./scripts/generate_metrics_report.sh -e 3                      # one experiment only
./scripts/generate_metrics_report.sh --run-phase main          # one phase, every tier in it
./scripts/generate_metrics_report.sh --model gpt-5.4-mini      # one tier, every phase it appears in
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
- **Unfiltered, rebuilds `agg_query`/`agg_topology` from scratch** (drop and recreate),
  not an incremental update — both tables are fully derived from `run`, so a rebuild
  cannot lose data, and it guarantees no row survives from a since-changed measure
  definition. **Filtered** (`-e`, `--run-phase` or `--model`), only the rows belonging to
  the experiments present in the filtered run set are deleted and re-inserted; every
  other experiment's rows are left untouched. `./generate_metrics_report.sh
  -e 3` therefore does not rebuild the whole table — it replaces experiment 3's rows only.
- **The full report is the default.** Both views print with no flags: the topology
  summary and the complexity-bin breakdown. `analysis/aggregate.py` itself makes each
  view opt-in via `--print` and `--bins`; this wrapper inverts that, because running it
  by hand means wanting to see everything. Suppress a view with `--no-print` or
  `--no-bins`; passing both rebuilds the tables silently.
- **Re-running is safe and idempotent**, filtered or not. The timing stage recomputes and
  overwrites every value from the stored offsets; the aggregation stage replaces exactly
  the rows described above. Running the same command twice in a row produces identical
  output, so neither mode can double-count or drift.
- **Campaign filters pass straight through** to `analysis/aggregate.py`, which owns their
  meaning. This wrapper does not parse them; parsing them in two places is how the two
  would drift apart.
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

## 7. Pre-flight checks: config freeze and figure sources

Two checks that cost seconds and guard mistakes that are expensive to find later. Neither
makes an API call or writes to the run store.

### `freeze_config.py` — has the substrate moved? (T53)

Records what every run's result depends on, and detects drift against that record.

```bash
# record the current substrate as the frozen one
uv run python supply_chain_topology_app/cli/freeze_config.py --write

# compare the substrate against the manifest; exits 1 on drift
uv run python supply_chain_topology_app/cli/freeze_config.py
```

Covered: the resolved model and `OPENAI_MODEL`/`OPENAI_MODEL_MINI`, the RAG corpus, the
built vector index, every prompt file, the frozen query set, and the prediction models.
The manifest lives at `config/freeze_manifest.json`.

**Why the RAG corpus is in scope even though retrieval is not under study.** The same
corpus and the same index feed every topology in every experiment. If either is rebuilt
between the pilot and the main experiment, the `recommend` capability changes underneath
a comparison that is supposed to hold it fixed. Checksumming turns that from an assumption
into a check. `check_model_parity.py` covers generation parameters across conditions;
this covers the data and prompt substrate across time — the two do not overlap.

Build artefacts (`__pycache__`, `.pyc`, editor lock files) are excluded from every digest.
They are rewritten without the substrate changing, and a check that reports drift on an
unchanged substrate stops being read. An unreadable file is reported rather than raised
on, and is named in the output so it cannot pass as verified.

### `check_figure_sources.py` — can every planned figure actually be drawn? (T15)

Each figure in
[analysis-report-design-spec.md](../reporting/analysis-report-design-spec.md) declares the
run-store fields it reads; this resolves every declaration against the live store.

```bash
uv run python supply_chain_topology_app/analysis/check_figure_sources.py
```

A measure that was never computed is invisible until someone sits down to draw the
figure, at which point the fix is a re-run rather than an edit. Run it after any change
to the measure set, and before starting figure work. It also names figures that have no
owning task, so an unowned figure is visible here rather than only in the spec prose.

---

## 8. Human-eval export/import: `human_eval_io.py`

Blinded human rating, matched to the same runs the LLM judge already scored, one
experiment at a time (T43).

```bash
# list defined experiments (run_phase, model, experiment_no)
uv run python supply_chain_topology_app/cli/human_eval_io.py --list-experiments

# export experiment 3 (pilot, gpt-5.4) -- writes two workbooks under human_eval/
uv run python supply_chain_topology_app/cli/human_eval_io.py -e 3

# merge a rater's filled-in blind sheet back in
uv run python supply_chain_topology_app/cli/human_eval_io.py --import rated.xlsx --rater aditi
```

Two workbooks per export, never one. `human_eval_experiment<N>_blind.xlsx` carries an
`item_id`, the query text, and the response text — reusing
`cli/score_topology_run.py`'s own T42 extraction so a rater reads the same artifacts the
LLM judge read — with no `run_id` and no topology name anywhere in the file. This is the
one to send to raters. `human_eval_experiment<N>_key.xlsx` maps `item_id` back to
`run_id`/`topology`/`query_id` and carries the LLM judge's scores for the same run, for
the human-vs-LLM comparison T59 needs. It stays with the analyst — a rater who sees it is
no longer blind. Row order and `item_id` assignment are seeded on the experiment number,
so re-exporting the same experiment reproduces the same mapping rather than reshuffling a
batch that may already be with raters.

The 1–5 scale and the three dimension definitions on the blind sheet's Instructions tab
are copied verbatim from `evals/judge.py`'s own prompt to the LLM judge — a human-vs-LLM
agreement check is only meaningful if both are using the same scale.

`--import` reads a rater's filled-in `Rating Sheet` tab, resolves each `item_id` against
the matching `*_key.xlsx` in the same output directory, and writes one row per
`(run_id, rater)` into a new `human_eval_rating` table — not into `quality_scores`, which
is one row per `run_id` and has no room for more than one rater's opinion on the same
run. T58 needs more than one rater per run to compute inter-rater reliability
(Krippendorff's alpha / Cohen's kappa).

---

## 9. Keeping a long batch running: `caffeinate`

`execute_experiment.sh` against the full topology x query grid can run for hours. macOS
will sleep the machine (or at minimum the display) partway through if nothing prevents
it, which pauses the batch along with everything else. `caffeinate` keeps the machine
awake for the duration of a command and exits on its own when that command finishes —
nothing to remember to turn back off afterward.

```bash

caffeinate -i ./scripts/execute_experiment.sh -e 4 --run-n 1 -t all -q all -b my-batch-20260906
```

`-i` prevents idle sleep for as long as the wrapped command runs. Run this from a
second terminal tab/window if you want to keep working in the first while the batch
runs in the background — `caffeinate` and the batch both live in that second terminal
and quitting it stops both.

This is a shell-level safeguard, not specific to this repo: it works the same way in
front of any long-running command (`generate_metrics_report.sh`, a `pytest` run, and so
on), though the aggregation script itself typically finishes in seconds and rarely
needs it.

## 10. `execute_chat_app.sh` — the Gradio delivery app

The delivery app is the demonstration surface for the substrate, not part of the
measurement workflow: it writes nothing to the run store. It is listed here only so the
`scripts/` folder is fully accounted for.

```bash
./scripts/execute_chat_app.sh                 # start the UI on http://127.0.0.1:7860, with cache

SC_NO_CACHE=1 ./scripts/execute_chat_app.sh   # no response cache, no freshness reuse
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
