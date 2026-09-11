# Experimental Design — conditions, workload, and how a batch is run

[← Documentation index](../README.md)

---

## 1. The design

A quantitative, controlled comparison. **Orchestration topology is the manipulated
variable; everything else is held fixed.**

| Factor | Levels | Role |
|---|---|---|
| Topology | 9 conditions | manipulated |
| Query | 11 (10 workload + 1 out-of-scope probe) | workload, crossed with topology |
| Model tier | 1, frozen after the pilot | controlled |
| Repetition | N ≥ 3 in the main experiment, 5 preferred where budget and schedule allow; N = 1 in the pilot | precision |

One repetition of the design is 9 × 11 = **99 runs**.

### Vocabulary

Four things get counted, each with its own column.

| Term | Column | Means |
|---|---|---|
| **run** | `run_n` | one execution of one topology × one query. One row in the `run` table, and the unit of observation. `run_n` is its repetition number: `run_n = 3` is the third run of that same topology and query, in the same experiment |
| **experiment** | `experiment_no` | one campaign: one **phase** at one **model**. Holds 99 × N runs |
| **phase** | `run_phase` | `pilot` or `main` |
| **batch** | `batch_id` | one invocation of the harness. An experiment takes as many batches as it takes |

**Reading the suffixes:**

| Suffix | Means | Example |
|---|---|---|
| `_n` | a position in a repeated series — the *n*th of something | `run_n = 2` is the 2nd run of that topology–query pair |
| `_no` | a number naming one member of a numbered set | `experiment_no = 3` is experiment 3 |
| `_id` | a label, not a number | `batch_id = 'pilot-nano-20260906'` |

### The experiments

| `experiment_no` | `run_phase` | model | `run_n` | runs |
|---|---|---|---|---|
| 1 | pilot | gpt-5.4-nano | 1 | 99 |
| 2 | pilot | gpt-5.4-mini | 1 | 99 |
| 3 | pilot | gpt-5.4 | 1 | 99 |
| 4 | main | frozen tier | 1 … N | 99 × N |

Each experiment covers the full 9 × 11 grid. The pilot runs each tier once, so its three
experiments hold 99 runs each and every row carries `run_n = 1`. The main experiment holds
the frozen tier repeated N times, so its rows carry `run_n` 1 … N.

**`experiment_no` is looked up, never typed.** The `experiment` table maps
`(run_phase, model)` to a number, with `UNIQUE (run_phase, model)`. The harness is given
the phase and the model and resolves the number itself, so a wrong number is unreachable
by typo. `aggregate.py` checks every run row against that table before it groups anything.

**`experiment_no` and `batch_id` answer different questions.** `batch_id` records which
invocation produced a row, and fragments whenever a batch is interrupted, resumed, or
partly re-run — experiment 3 is spread across `pilot-5-4-20260906`, `fix-q8-gpt54` and a
smoke batch. `experiment_no` records which campaign the run belongs to however many
invocations it took, so `WHERE experiment_no = 3` selects a complete experiment in one
clause.

**A run's slot** is `(run_phase, model, topology, query_id, run_n)` — the resume and
replace key. Phase belongs in it because the pilot is N=1 at every tier, so a main-experiment
`run_n = 1` at the frozen tier matches its pilot row on every other column.

**Ad-hoc runs.** A run launched directly through `execute_topology.sh` belongs to no
campaign. It carries `experiment_no = NULL` and stays unlocked, and aggregation selects by
experiment, so a smoke test cannot reach a reported figure.

**Pilot data.** N = 1 per tier — each `(topology, query, model)` cell holds one run,
9 × 11 × 3 = **297**.

**Trace paths** are stored relative to `supply_chain_topology_app/`, so a run resolves its
own log from any checkout.

### Repetition and aggregation

Repetition characterises run-to-run stochastic variability under pinned model and
configuration settings with caching disabled.

Figures are built in two aggregation steps, each collapsing one level:

| Aggregation | Groups by | Produces |
|---|---|---|
| **Query aggregation** | experiment, topology, query | one figure per topology–query pair, pooling that pair's repetitions |
| **Topology aggregation** | experiment, topology, scope | one figure per topology, taken across its query-level figures |

Both group by experiment, not by model. Keying on the model alone would pool the pilot
and the main experiment at the frozen tier into one figure — two campaigns with different
N, averaged into a number belonging to neither. `run_phase` and `model` are carried on
every aggregate row, so a figure stays readable and either can be filtered on directly.

> **The query is the unit of comparison across topologies.** Repetitions aggregate to the
> query first; query figures then aggregate to the topology. Every spread figure states
> the N it was computed over.

Both steps are implemented in `analysis/aggregate.py` and written to `agg_query` and
`agg_topology`. The statistic each step uses — median for distributions, pooled for rates
— is in
[measure-definitions.md §4](../instrumentation/measure-definitions.md#4-aggregation).

### Confound control

Topology execution order is **randomised within each repetition batch**, so that a
condition is not systematically run at the same point in a batch every time — which would
confound topology with time of run (provider load, model-side variation over the session).
Every run records its batch, execution order and timestamp, so a time-of-run effect can be
checked after the fact rather than assumed absent.

---

## 2. Two stages

The study runs in two stages, and the distinction matters because the model is a
controlled variable, not a free one.

```
  STEP 1 — MODEL TIER SELECTION AND FREEZE          (pilot, complete)
  ┌────────────────┐   ┌────────────────┐   ┌──────────────────────┐
  │ run the pilot  │──▶│ compare tiers  │──▶│ freeze one tier      │
  │ 9x11 per tier  │   │ on whether     │   │ record model,        │
  │ N=1            │   │ every topology │   │ prompts, tools and   │
  │                │   │ can be         │   │ settings as fixed    │
  │                │   │ measured       │   │                      │
  └────────────────┘   └────────────────┘   └──────────┬───────────┘
                                                       │ tier fixed
                                                       ▼
  STEP 2 — TOPOLOGY COMPARISON AT THE FROZEN TIER   (main experiment)
  ┌────────────────┐   ┌────────────────┐   ┌──────────────────────┐
  │ run 9x11       │──▶│ measure        │──▶│ compare topologies   │
  │ at N>=3        │   │ cost, latency, │   │ operational trade-off│
  │                │   │ tokens, sched, │   │ frontier, tier stated│
  │                │   │ coverage,      │   │ as a boundary        │
  │                │   │ violations,    │   │ condition            │
  │                │   │ quality        │   │                      │
  └────────────────┘   └────────────────┘   └──────────────────────┘
```

The tier was selected on **whether every topology can be measured across the full
complexity range** — not on which tier performs best in absolute terms. A tier at which
some conditions cannot execute the harder queries cannot support a nine-way comparison,
because those conditions are absent rather than merely weaker.

---

## 3. The frozen query set

### How complexity is assigned

Complexity is assigned **a priori** from two properties of the capabilities a query
requires, before any run, and is never adjusted from observed results. Deriving bins from
observed difficulty would make any complexity finding circular (decision D4).

| Capability | Dependencies | Outcome-generation complexity |
|---|---|---|
| Predict | none | High |
| Diagnose | Predict | Medium |
| Simulate | Predict | High |
| Recommend | Predict, Diagnose | Very high |
| Email | Predict | Low |

*Dependencies* are the prerequisite outputs a capability consumes as arguments — a fact
about the tool signature, not an orchestration choice, and the same table
`measurement/dependencies.py` uses to judge dependency order in every condition.
*Outcome-generation complexity* is the relative difficulty of producing the required
result.

From these, each query carries an ordinal **complexity index from 0 to 15**, used to order
the set from lower to higher task complexity and to separate queries with different
dependency structures from those with different generation demands. The four bins group
the index for reporting.

### The eleven queries

| Query | Expected tier | Required capabilities | Index | Bin |
|---|---|---|---|---|
| Q1 | Simple — out-of-scope | none | 1 | low |
| Q2 | Single capability | predict | 3 | low |
| Q3 | 2 cap + simple dependency | predict, email | 4 | medium |
| Q4 | 2 cap + simple dependency | predict, simulate | 5 | medium |
| Q5 | 2 cap + simple dependency | predict, diagnose | 7 | medium |
| Q6 | 3 cap + dependency | predict, diagnose, email | 8 | high |
| Q7 | 3 cap + dependency | predict, diagnose, simulate | 9 | high |
| Q8 | 3 cap + dependency | predict, diagnose, recommend | 12 | high |
| Q9 | 4 cap + dependency + concurrency | predict, diagnose, recommend, email | 13 | very high |
| Q10 | 4 cap + dependency + concurrency | predict, diagnose, simulate, recommend | 14 | very high |
| Q11 | 5 cap + dependency + concurrency | predict, diagnose, simulate, recommend, email | 15 | very high |

Query text, required capabilities, index and bin all live in the `query_metadata` table
and are frozen. Every condition receives the identical text.

| Query | Text |
|---|---|
| Q1 | Can you cancel the delayed orders and issue refunds to the affected customers? |
| Q2 | Which of today's orders are predicted to be delayed? |
| Q3 | Identify customers impacted with delivery delays and send an email to them |
| Q4 | What will be the delivery impact if there is a storm in the east today? |
| Q5 | Find out which customer orders will be late and what are the reasons for the delays |
| Q6 | Which orders are likely to be delayed today, what patterns are driving those delays, and can you notify the affected customers? |
| Q7 | What patterns are driving today's delays, and would that get worse if a storm hit the East? |
| Q8 | What are the typical delay patterns and how can we optimize last-mile delivery? |
| Q9 | What patterns are driving today's delays, what should we do about it, and let affected customers know |
| Q10 | Diagnose today's delay patterns, check how a storm in the East would affect things, and tell me how to optimize delivery |
| Q11 | Which orders can get delayed today, what are the reasons and delay patterns, what will happen if there is a storm in the east region, provide your recommendations to optimize the order delivery and also provide emails that can be sent to customers whose orders are predicted to be delayed |

**Q1 is the out-of-scope probe.** It requires no capability, so declining is the correct
outcome. It is reported in its own scope and excluded from every workload figure and
every complexity bin.

**Q8 is deliberately ambiguous** — it asks two things at once. It tests whether a
condition recognises under-specification and asks, rather than refusing or guessing.

> **Bin sizes are uneven.** The low bin holds one workload query (Q2), the others three
> each. Any bin-level figure must carry its n.

---

## 4. Running a batch

Every batch states two things: which experiment it belongs to, and which repetition it is.

```bash
# list the experiments that exist
uv run python supply_chain_topology_app/measurement/run_store_schema.py --list-experiments

# open the main experiment at the frozen tier, first repetition
caffeinate -i ./scripts/execute_experiment.sh --run-phase main --model gpt-5.4 --run-n 1 --new-experiment

# its second and third repetitions
caffeinate -i ./scripts/execute_experiment.sh -e 4 --run-n 2
caffeinate -i ./scripts/execute_experiment.sh -e 4 --run-n 3

# one query across all topologies, inside an existing experiment
caffeinate -i ./scripts/execute_experiment.sh -e 4 --run-n 1 -q Q8 -b fix-q8

# preview without spending
./scripts/execute_experiment.sh -e 4 --run-n 1 --dry-run
```

| Flag | Meaning |
|---|---|
| `-e` | experiment number. Alternative to `--run-phase` with `--model` |
| `--run-phase` | `pilot` or `main`. Use with `--model` |
| `--model` | the tier. Checked against `OPENAI_MODEL` in `.env`; a mismatch aborts the batch |
| `--new-experiment` | allocate a number for a `(phase, model)` pair that has none yet |
| `--run-n` | **required** — which repetition this is |
| `-t` | topologies, or `all` |
| `-q` | queries, `Q8` or `1,2,3`, or `all` |
| `-b` | batch label recorded on every run |
| `--no-resume` | re-run combos that already have a success or partial |

### Three behaviours that have caused mistakes

**Resume is on by default.** A slot that already has a `success` or `partial` row is
skipped. If you intend to replace runs, delete them first — otherwise the batch silently
narrows and you get fewer runs than planned, with old and new rows coexisting.

**The model is stated twice, on purpose.** `--model` names the tier; `.env` supplies it to
the run. The harness compares the two and aborts before planning if they disagree. A
mis-set `.env` has produced contaminated rows more than once, and a contaminated row
cannot be told from a valid one afterwards.

```bash
grep -E '^OPENAI_MODEL=' .env
```

**A `(phase, model)` pair that does not exist yet needs `--new-experiment`.** Without it
the batch aborts and lists the experiments that do exist, so a typo'd phase cannot quietly
open a new campaign. A generation-parameter parity check also runs before every batch and
aborts on mismatch.

---

## 5. After a batch

```bash
./scripts/generate_metrics_report.sh                 # every experiment, every view
./scripts/generate_metrics_report.sh -e 4            # one experiment
./scripts/generate_metrics_report.sh --run-phase main
```

Both steps are idempotent and make no API calls, so it can be re-run freely. A filtered
run rebuilds only the experiments it covers and leaves the others untouched.

---

## 6. Validity rules

| Rule | Consequence |
|---|---|
| A run that was part of the design and fails a validity check is **excluded, not deleted** | `excluded_reason` set; row retained for audit |
| A run that should never have executed is **deleted** | it was not part of the design, so retaining it would make the store disagree with it |
| Pre- and post-change runs are never pooled | a harness change that alters what a condition receives invalidates comparison with earlier runs |
| A single run is a signal, not a result | every claim states n |

---

[← Documentation index](../README.md)
