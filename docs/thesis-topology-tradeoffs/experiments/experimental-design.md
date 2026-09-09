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
| Repetition | N ≥ 3 in the main experiment; N = 1 in the pilot | precision |

One pass over the pilot design is 9 × 11 = **99 runs per tier**.

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

Complexity is assigned **a priori** from two properties of the required capability set —
dependency depth and outcome-generation complexity — before any run, and is not adjusted
from observed results.

| Query | Bin | Index | Required capabilities |
|---|---|---|---|
| Q1 | low | 1 | none — out-of-scope probe |
| Q2 | low | 3 | predict |
| Q3 | medium | 4 | predict, email |
| Q4 | medium | 5 | predict, simulate |
| Q5 | medium | 7 | predict, diagnose |
| Q6 | high | 8 | predict, diagnose, email |
| Q7 | high | 9 | predict, diagnose, simulate |
| Q8 | high | 12 | predict, diagnose, recommend |
| Q9 | very high | 13 | predict, diagnose, recommend, email |
| Q10 | very high | 14 | predict, diagnose, simulate, recommend |
| Q11 | very high | 15 | predict, diagnose, simulate, recommend, email |

**Q1 is the out-of-scope probe.** It requires no capability, so declining is the correct
outcome. It is reported in its own scope and excluded from every workload figure and
every complexity bin.

**Q8 is deliberately ambiguous** — it asks two things at once. It tests whether a
condition recognises under-specification and asks, rather than refusing or guessing.

> **Bin sizes are uneven.** The low bin holds one workload query (Q2), the others three
> each. Any bin-level figure must carry its n.

---

## 4. Running a batch

```bash
# whole design at N=3
caffeinate -i ./scripts/run_experiment.sh

# one query across all topologies, one repetition index
caffeinate -i ./scripts/run_experiment.sh -q Q8 -r 3 -b fix-q8

# preview without spending
./scripts/run_experiment.sh --dry-run
```

| Flag | Meaning |
|---|---|
| `-t` | topologies, or `all` |
| `-q` | queries, `Q8` or `1,2,3`, or `all` |
| `-r` | which repetition indices |
| `-n` | what `-r all` expands to |
| `-b` | batch label recorded on every run |
| `--no-resume` | re-run combos that already have a success or partial |

### Two behaviours that have caused mistakes

**Resume is on by default.** A combo that already has a `success` or `partial` row is
skipped. If you intend to replace runs, delete them first — otherwise the batch silently
narrows and you get fewer runs than planned, with old and new rows coexisting.

**The model tier comes from `.env`.** It is not a flag. Check it before every batch:

```bash
grep -E '^(MODEL|OPENAI_MODEL)=' .env
```

A mis-set `.env` has produced contaminated rows more than once. A generation-parameter
parity check now runs automatically before the batch and aborts on mismatch.

---

## 5. After a batch

```bash
./scripts/generate_metrics_report.sh     # backfill + aggregate + print
```

Both steps are idempotent and make no API calls, so it can be re-run freely.

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
