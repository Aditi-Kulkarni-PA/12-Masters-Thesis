# Analysis Report Design Spec — tables and figures for the final results

[← Documentation index](../README.md)

Written 30-Aug-26. Specifies exactly what tables and figures the results chapter
produces, what question each one answers, how each is encoded, and the minimum data
state at which each is honest to draw. The purpose is to decide chart form **once, in
writing**, so that form is not picked ad hoc per task once the pilot data lands.

Chart-pattern inspiration reviewed directly from artificialanalysis.ai's model
leaderboard and per-model comparison pages (30-Aug-26). Every pattern below is
adopted, adapted, or rejected with a stated reason. Two figures (F10, F11) come from
the ML benchmarking literature rather than from that site, because the site has no
analog for comparing k systems across N shared workloads.

This spec governs **form**. Content comes from `run_store.db` at whatever N each task
runs at.

**Query IDs renumbered 5-Sep-26 (Aditi).** The frozen query set's IDs were reassigned so
Q1–Q11 read in ascending `query_complexity_score` order; `run.query_id` for every
historical run was migrated to match, so the run store's IDs are internally consistent
as of this date. Old-ID → new-ID mapping: Q1→Q5, Q2→Q4, Q3→Q3, Q4→Q8, Q5→Q7, Q6→Q6,
Q7→Q9, Q8→Q10, Q9→Q1, Q10→Q11, Q11→Q2. Literal spec lines that future analysis code
would execute against (the §5 ranking table, F14's filter) were updated to the new
numbering. Dated narrative passages elsewhere in this document — anything describing a
specific observation, run, or decision made before 5-Sep-26 — were **left as originally
written** and use the OLD numbering; cross-check the mapping above (or
`query_set_v1.xlsx`'s Q1 note, which carries the same mapping) before treating any
specific Q-ID mention in this document as reflecting the current query set.

---

## 1. Figure inventory

Status as of 30-Aug-26. Every figure now has a tracker owner; the D-series
(D1–D6, created 30-Aug-26) holds the figure work that previously had none.

| ID | Figure | Owner | Min. data state | Status |
|---|---|---|---|---|
| — | Shared figure style module | **D1.1** | n/a | prerequisite for all F-figures |
| T1 | Master comparison table | T61 | n=1 draws, N≥3 for CIs | specified |
| F1 | Pareto: quality vs cost | T63 | N≥2 (ranges), N≥3 (CIs) | specified |
| F2 | Pareto: quality vs latency | T63 | N≥2 / N≥3 | specified |
| F3 | Pareto frontier shift, simple vs multi_hop | **D2.3** | N≥2 | specified |
| F4 | Cost anatomy, tokens per capability | T65 | n=1 | **blocked by R46 / T110** |
| F5 | Orchestration tax, coordinator share of tokens | **D3.1** | n=1 | **blocked by R45 / T109** |
| F6 | Quality vs capabilities-required | **D2.1** | N≥2 | specified |
| F7 | Cost vs capabilities-required, log axis | **D2.2** | N≥2 | specified |
| F16 | Latency vs query complexity score | **D2.4** | n=1 | **built 30-Aug-26** |
| F8 | Design-family frontier | **D4.1** | N≥2 | specified |
| F17 | Composite topology score (latency+cost+quality) | **D4.2** | n=1 | **built 30-Aug-26** |
| F18 | 3D interactive scatter, latency×cost×quality, target zone | **D4.3** | n=1 | **built 30-Aug-26** |
| F9 | Cumulative cost across query set | **D3.2** | N≥1 full sweep | specified |
| F10 | Critical-difference / rank diagram | **D5.1** | N≥3, all 10 queries | specified |
| F11 | Forest plot, paired effect sizes | **D5.2** | N≥3 | specified |
| F12 | Scope-selection accuracy heatmap | **D6.1** | N≥2 | specified |
| F13 | Execution timeline (Gantt) | **D6.2** | n=1 | **buildable today** |
| F14 | Robustness probe behaviour (Q1, was Q9 before 5-Sep-26 renumbering) | **D6.3** | N≥2 | specified |
| T2 | Failure taxonomy table | T67 | N≥2 | specified |
| F15 | Variance across reps | T68 | N≥2 strip, N≥5 box | specified |
| — | Topology diagrams (6) | T66 | n/a | specified |

**Naming note.** `D1`–`D3` were used in an earlier draft of this document as labels for
three build defects. Those now carry their real Risk Log numbers — **R45** (Sequential
token accounting), **R46** (missing per-capability capture), **R47** (suspect Monolith
timing run) — with fix tasks T109, T110, T111. The `D` prefix belongs to the figure task
series only. Recorded rather than silently renamed, since the earlier labels may appear
in session notes.

**Two figures are gated on defect fixes, not on data volume.** F4 and F5 must not be
drawn until T110 and T109 land respectively; §7 explains why F5 in particular would
invert its own ranking.

---

## 2. Design system

Carried over from the visualization conventions used in this session's sample charts,
and binding on every figure above.

- **One y-axis per figure.** Never dual-axis. Two scales that need comparing become two
  figures, or one indexed axis.
- **Colour follows the entity, never its rank.** Each topology holds one colour for the
  whole thesis. Filtering or re-sorting must not repaint.
- **Never colour alone.** Pair with marker shape (scatter), dash pattern (line), or a
  value label (bar). A reader printing in greyscale must still be able to separate
  conditions.
- **Round every displayed number** to the precision the metric warrants: integer token
  counts, 1–2 decimals for percentages and USD, 1 decimal for seconds.
- **Every figure caption states N.** Not optional. A figure without its N beside it
  overclaims by default.
- **Every figure gets an interactive form.** Directed by Aditi, 30-Aug-26: "all charts
  should be interactive with best visuals for slicing/dicing/analysis." A static image
  is not the deliverable by default — the interactive companion (rotate/zoom for 3D,
  hover for full point detail, toggle/filter for subsets, legend click to isolate a
  topology) is. Where the results chapter needs a static print panel too (a PDF cannot
  embed interaction), that panel is a rendered snapshot of the same interactive figure,
  not a separately designed one — this was already the working pattern for F6/F7/F16
  before it was written down as a general rule here; F17/F18 follow it from the start.

### Fixed topology identity

| Topology | Colour | Marker | Scope decision | Execution shape |
|---|---|---|---|---|
| Monolith | blue `#2a78d6` | circle | fixed-always-all | single-context |
| Sequential | orange `#eb6834` | triangle | planned-once, **binding** | forced-serial |
| Planner-Executor | aqua `#1baf7a` | square | planned-once, **advisory** | model-batched |
| Static-Graph DAG | yellow `#eda100` | diamond | fixed-always-all | graph-concurrent |
| Static-Graph Routed | magenta `#e87ba4` | star | discovered-at-runtime | graph-concurrent |
| Dynamic-Graph | green `#008300` | cross | discovered-at-runtime | ledger-serial |
| Swarm (not built) | violet `#4a3aa7` | pentagon | discovered-at-runtime | tbd |
| Mesh (not built) | red `#e34948` | hexagon | fixed-always-all | peer-to-peer |

### Fixed capability identity (segment colours in F4, F13)

predict `#2a78d6` · diagnose `#eb6834` · simulate `#1baf7a` · recommend `#eda100` ·
email `#e87ba4`

**Scope-axis labels corrected 30-Aug-26 — the distinction is plan ENFORCEMENT, not
decision timing.** This table previously gave Sequential and Planner-Executor the same
`planned-once` label, which collapsed a real difference between them. Aditi's
instruction was that the implementation settles the classification and the documents
are then corrected to match, so this was resolved by reading code rather than by
reconciling the two READMEs (both of which describe their own condition accurately).

Finding: `execute_topology.py` drives exactly two turns, and turn 2 sends
`"Yes, proceed."` to the **same master object** — the same five agent-as-tools still
bound, `tool_choice` still `"auto"` (`planner_executor.py::build_master`). Nothing
between the turns reduces the tool set, forces a tool, or parses the plan into a
constraint. Planner-Executor's plan is therefore text in the conversation history:
persuasive, not binding. Sequential does the opposite — it builds a **new** master
carrying only the planned tools and drives it one turn per planned step with
`tool_choice` forced to that step's tool by name, so the plan is enforced in code.

Both conditions plan once; only one is held to its plan. `planned-once` alone cannot
express that, hence the binding/advisory qualifier. Note this does **not** make
Sequential's plan *correct* — the planner can still derive a wrong order, which is
exactly why a dependency violation is a recorded, measurable outcome rather than an
impossibility.

Topology colours and capability colours are drawn from the same 8-slot palette. This is
deliberate but must never appear in the same figure — no figure may ask the reader to
hold two meanings of one colour at once. F4 and F13 are capability-coloured with
topologies on the category axis; every other figure is topology-coloured.

---

## 3. What "n" means throughout

- **n=1 (now)** — one opportunistic run per topology. Single point or bar. No error
  bar, no distribution shape; drawing either implies precision the data lacks.
- **N=2 (T44 pilot)** — two reps per topology per query. Enough for mean ± range. A CI
  formula at n=2 returns a number but not a trustworthy one; show the range and say so.
- **N≥3 (T54 full run)** — first point at which T61's confidence intervals and T62's
  Wilcoxon tests are the pre-registered plan (T50) rather than an approximation.
- **N≥5** — first point at which box-plot quartiles (F15) carry meaning.

---

## 4. Pre-registration of predicted shape

Project rule already in force for runs: *predict the expected output before each run;
it makes a wrong model visible immediately rather than after the fact.* This spec
extends that rule to figures. Each figure below carries a **Predicted shape** — what it
should look like if the current understanding of these topologies is correct.

The point is not to be right. It is that a figure matching its prediction is weak
evidence, while a figure contradicting it is a finding that must be explained rather
than quietly absorbed into the narrative. These predictions should be lifted into T50's
pre-registered analysis plan before T54 runs, not written after the data is seen.

---

## 5. Figure specifications

### T1 · Master comparison table (T61)

One row per topology, columns grouped: **Identity** (scope decision, execution shape) ·
**Plan & scope** (`plan_presented`, tools expected/actual, dependency violations,
scope-selection accuracy) · **Concurrency** (actual span, critical path, % exploited) ·
**Cost** (tokens, USD) · **Latency** (wall time) · **Quality** (judge mean, scope-adjusted)
· **Validity** (run status, `path_fallback_used` gate per R10, **N**).

Swarm and Mesh keep their rows with cells marked "not built" rather than being omitted —
the table is the experimental design, not just the conditions that happen to have run.

`plan_presented` needs a footnote: it means different things per condition. For DAG it
is a tools-off triage confirmation with scope fixed in code; for Sequential and Routed
it reflects a real scope-and-order decision. It is an interpretability signal, not a
proxy for "did real planning."

---

### F1, F2 · Pareto frontiers (T63)

**Question.** Is any topology dominated — worse on both axes than some alternative?

**Encoding.** Scatter, one point per topology. x = cost USD (F1) or wall time (F2);
y = judge quality, scope-adjusted where available. Marker shape per §2. **Quadrant
shading** on the high-quality/low-cost and high-quality/low-latency corners — adopted
from AA's "Input vs. Output Token Usage" chart, which shades a "most attractive
quadrant" rather than making the reader infer which corner wins.

**n.** n=1 draws without error bars (sample only). N≥2 adds min–max range bars on both
axes. N≥3 switches to 95% CI, which is what T63's existing DoD already requires.

**Predicted shape.** Planner-Executor and Static-Graph Routed on the frontier;
Sequential dominated on both (highest cost, mid quality); Dynamic-Graph far bottom-left
— cheap because it under-executes, which is the already-documented R42.1 finding rather
than an efficiency win. If Dynamic-Graph appears anywhere near the frontier, the
scope-adjusted quality score is not doing its job and needs re-examination before the
figure is used.

---

### F3 · Pareto frontier shift, simple vs multi_hop — **no owner**

**Question.** Does the frontier move with workload size, and do the same conditions
hold it in both regimes?

**Encoding.** F1's axes, drawn twice — once over simple-tier queries, once over
multi_hop — as two panels or one panel with hollow/filled markers.

**Why it exists.** It is the static, cheaper answer to the same question F6 asks
continuously, and it reuses F1's figure code. Good fallback if F6 proves too sparse at
pilot N.

**Predicted shape.** The frontier shifts right (everything costs more) and the
membership changes — Monolith competitive on simple, displaced on multi_hop.

---

### F4 · Cost anatomy, tokens per capability (T65)

**Question.** Where does each topology spend its tokens?

**Encoding.** Horizontal stacked bar, one per topology, segments = the 5 capabilities.
Adapted from AA's "Token Usage per Task" stacked bar; their segments are
input/cache/output token *types*, ours are capability *roles* — same stacking logic,
different semantic dimension.

**Blocked.** Monolith and Dynamic-Graph record zero per-capability tokens (confirmed
30-Aug-26; neither routes through the per-role capture path the other four use). Their
absence must appear as a caption note, not a silent omission — it is a finding about
the instrumentation. See D1.

**Predicted shape.** diagnose dominant in every condition that runs it (already visible
at n=1: 110k–120k prompt tokens against ~4k for email), because it receives the full
multi-intent query text — the open R44 question.

---

### F5 · Orchestration tax — **no owner**, blocked by D2

**Question.** What fraction of a run's tokens is spent on coordination rather than on
domain work? This is the most direct measurement of orchestration overhead in the whole
design, and the metric the thesis title most nearly names.

**Encoding.** Stacked bar to 100% per topology: coordinator/router/aggregator share vs
specialist share. Source: `run.master_total_tokens` against `Σ tool_call tokens`.

**n.** n=1 is enough to draw it; the metric is a within-run ratio, not a comparison of
noisy magnitudes.

**Two degenerate cases that must be annotated, not plotted as if comparable.** Monolith
and Dynamic-Graph both show `master_total == grand_total` with zero tool tokens — for
Monolith that is architecturally correct (there is one context; the orchestration/domain
distinction does not exist), for Dynamic-Graph it is the same capture gap as F4. Neither
is a 100% orchestration tax in any meaningful sense.

**Blocked by D2.** The accounting identity holds exactly for DAG, Routed and
Planner-Executor but fails for Sequential by 192,126 tokens. Drawing this figure before
D2 is fixed would rank Sequential as the *leanest* multi-agent condition (6.5%) when the
unattributed tokens are almost certainly its executor-coordinator turns, which would
make it the heaviest. The figure would be exactly backwards.

**Predicted shape (once D2 is fixed).** Graph-concurrent conditions lowest — their
coordination is one router call plus one aggregator call, with the graph structure
itself carrying the sequencing that other conditions pay an LLM to decide. Sequential
and Planner-Executor highest, because a model re-reasons about what to do next at every
step.

---

### The progression axis, corrected (30-Aug-26) — query complexity score, not capability count

**Superseded.** F6/F7 below originally used `len(implied_tools_json)` — a bare capability
count — as the x axis. Corrected on Aditi's objection: two queries with the same count
can differ hugely in actual load (`predict + email`, 2 light capabilities, vs
`predict + diagnose`, 2 capabilities where diagnose alone dominates every cost-anatomy
figure in §3 on this session's evidence), and count also obscures the concern that
motivated this correction — how much of a query's real work lands on the orchestrator
versus the specialists is itself topology-dependent, so an axis that only counts
capabilities without weighting them makes cross-topology latency comparisons look more
apples-to-apples than they are.

**Fix: a capability-weighted query complexity score**, replacing capability count
everywhere in D2 (F6, F7, F16). **Now persisted, not recomputed per figure** (Aditi,
30-Aug-26) — `query_metadata.query_complexity_score`, an actual column, computed once by
`measurement/build_query_metadata.py::query_complexity_score()` at query-set build time
and synced from there. Every figure below reads the column; none recompute the weighting
inline, so it has exactly one definition project-wide.

```
score(query) = Σ weight(c)  for c in query_metadata.implied_tools_json
weight = { email: 1, simulate: 2, predict: 3, diagnose: 4, recommend: 5 }
```
(`CAPABILITY_WEIGHT` in `build_query_metadata.py` — read the code comment there before
changing a weight; this doc and that constant must not drift apart.)

Weights are Aditi's stated low-to-high ordering (30-Aug-26), not derived from measured
cost — worth being explicit that this is a design choice, not itself an empirical
finding, and stating so wherever the score is cited. The x axis becomes query IDs sorted
by this score, not a 0–5 ordinal count.

**Q9 is NULL, not 0 — a correction (30-Aug-26).** An earlier version of this table put
Q9 (0 implied capabilities) at score 0, the leftmost point. Aditi flagged this as
misleading: Q9 is not the lightest point on a capability-complexity axis, it is a
different *kind* of query entirely. Its own note in `query_set_v1.xlsx` states its
purpose directly — "Tests honest decline vs fabrication, not a happy path" — an
out-of-scope/refusal probe (F14 measures it separately, on restraint). Checked and
confirmed **not a duplicate** of any other query before deciding what to do with it.
`query_complexity_score` was NULL for this query in the schema at the time, and it was
excluded from every figure in this section — not scored, not plotted, not ranked.

**Superseded (5-Sep-26, Aditi).** Two changes since the note above, both reflected in
the table below: (1) the query implying zero capabilities now scores a **floor value of
1**, not NULL — it is kept on this axis as its lowest point rather than excluded, per
`_ZERO_CAPABILITY_FLOOR_SCORE` in `build_query_metadata.py`; (2) **every query ID in this
document was renumbered** so the set reads in ascending `query_complexity_score` order
(Q1 = lowest, Q11 = highest). The old-ID → new-ID mapping is Q1→Q5, Q2→Q4, Q3→Q3,
Q4→Q8, Q5→Q7, Q6→Q6, Q7→Q9, Q8→Q10, Q9→Q1, Q10→Q11, Q11→Q2. Any Q-ID cited in a dated
narrative passage elsewhere in this document (before 5-Sep-26) uses the OLD numbering —
cross-check that mapping, or `query_set_v1.xlsx`'s own Q1 note, before treating any
specific Q-ID mention as current. `run.query_id` for every historical run was migrated
to match, so `query_id` in the run store is consistent with this table as of 5-Sep-26.

**Q2 (was Q11) added (30-Aug-26) — the missing floor.** No query previously isolated a
single capability; every other one starts at a pair (this document's own Q5, "Minimal
diagnostic pair"). This query — *"Which of today's orders are predicted to be
delayed?"*, predict only, score 3 — fills that gap as the lowest-complexity point that
still executes a real capability. The frozen query set is now **11 queries**, corrected
across T16/T44/D3.2/D5.1's task text in the tracker.

| Rank | Query | Score | Capabilities |
|---|---|---|---|
| 1 | Q1 | 1 | none (out-of-scope probe) |
| 2 | Q2 | 3 | predict |
| 3 | Q3 | 4 | predict, email |
| 4 | Q4 | 5 | predict, simulate |
| 5 | Q5 | 7 | predict, diagnose |
| 6 | Q6 | 8 | predict, diagnose, email |
| 7 | Q7 | 9 | predict, diagnose, simulate |
| 8 | Q8 | 12 | predict, diagnose, recommend |
| 9 | Q9 | 13 | predict, diagnose, recommend, email |
| 10 | Q10 | 14 | predict, diagnose, simulate, recommend |
| 11 | Q11 | 15 | predict, diagnose, simulate, recommend, email |

Worth flagging once more: **the ranking is not identical to a count-based one** — Q6 (3
capabilities, score 8) sits left of Q7 (3 capabilities, score 9), because diagnose+email
is lighter than diagnose+simulate on these weights, which a count-only axis could not
express. **Named queries, not synthetic bins** — every point is directly traceable to one
frozen, citable query. Q1 is included in this table (unlike before 5-Sep-26) but remains
a structurally different kind of point: it is the only row requiring zero capability
execution, and F14 still measures it separately, on restraint rather than capability
load — its presence here reflects ordering convenience, not equivalence with Q2–Q11.

### F6 · Quality vs capabilities-required (extend T64)

**Question.** Does orchestration overhead only pay for itself once there is enough
dependent or concurrent work to amortise it against?

**Encoding.** Line chart. x = the 10 queries ordered by **query complexity score** (see
above, not a bare capability count), y = judge quality. One line per topology; the upper
envelope is the frontier. Adapted from AA's "Frontier Intelligence Over Time" — what
that chart actually encodes is not time but *an ordered progression axis with a frontier
envelope over it*, and this thesis has a better progression axis than time.

**Source.** `query_metadata.implied_tools_json` run through the weight table above. No
new instrumentation, no new queries, no re-tagging.

**Why this supersedes T64's current binary split.** `complexity_tier` is binary and
collapses queries of very different real weight into one "multi_hop" bucket (e.g. Q6,
score 8, and Q11, score 15, are both just "multi_hop"). The weighted score is the axis
the orchestration argument actually turns on. T64's task text should be widened to this
axis, with the binary split retained only as the coarse fallback (F3).

**n.** N≥2, and needs coverage at 3+ distinct capability counts per topology. Not
buildable from current data — most existing runs cluster on Q10 (5) and Q5/Q6 (3).

**Predicted shape.** A crossover. Monolith highest at 2 capabilities, where coordination
buys nothing and its single context avoids all hand-off loss; graph-concurrent conditions
overtaking by 4–5 as dependency management starts to matter. **A flat, non-crossing set
of lines would be the more interesting result** — it would say topology choice is
workload-independent over this range, which contradicts the premise motivating the whole
comparison and would need to be reported as such rather than explained away.

---

### F7 · Cost vs capabilities-required, log y-axis (extend T64)

**Question.** How does each topology's cost *scale*, not just where it lands?

**Encoding.** As F6, y = cost USD on a **log scale**. Adapted from AA's "Inference Price
by Intelligence Band" chart, which uses a log price axis because its entities span
orders of magnitude.

**Why log is not stylistic here.** At n=1 on Q10, Dynamic-Graph cost USD 0.019 and
Sequential USD 1.541 — ~80×. A linear axis renders the five cheaper conditions as
indistinguishable near-zero lines and hides every difference among them.

**Predicted shape.** Conditions that re-send accumulated context at each step (Monolith,
Sequential) scale superlinearly with query complexity; conditions that fan out from one
shared prediction (DAG, Routed) scale closer to linearly. This is falsifiable and should
be recorded in T50 before T54 runs.

---

### F16 · Latency vs query complexity — built 30-Aug-26, real data

**Question.** Does wall-clock latency scale with query complexity the same way across
topologies, and does any topology's advantage or disadvantage grow or shrink as
complexity increases?

**Encoding.** Line chart, same x axis as F6/F7 (queries ordered by complexity score),
y = `wall_time_s`. One line per topology, gaps left where no run exists rather than
interpolated — a topology missing a point is missing data, not zero. Adapted from the
same AA step-line form as F6, requested directly by Aditi as the corrected version of a
capability-count-based time chart, on the objection that raw capability count conflates
queries of different real weight and obscures how much of a query's work lands on the
orchestrator versus the specialists — which is itself topology-dependent, so a bare count
axis makes cross-topology latency comparisons look more comparable than they are.

**Built with real data already on disk** (`wall_time_s`, `run_status='success'`,
`Q_PLACEHOLDER_1` excluded): Monolith only has Q10 (n=1); Sequential has Q5/Q4/Q10
(n=1,2,1); Planner-Executor has Q5/Q4 (n=1,3); Static-Graph DAG only Q10 (n=2);
Static-Graph Routed has Q6/Q5/Q10 (n=1,1,2); Dynamic-Graph has Q6/Q5/Q10 (n=6,1,2, mixed
capture era per R48 — its Q6 points blend pre- and post-14:15-29-Aug runs; wall time is
unaffected by the token-capture fix that R48/T112 concerns, so this is noted rather than
excluded, unlike a cost or quality figure using the same runs).

**Observation from what's on disk (n=1–2, not a finding — flagged per project rule, not
enough repetition to claim anything).** At the highest-complexity point (Q10, score 15),
Static-Graph Routed sits lowest among the conditions with data there (122.9s) while
Sequential and Dynamic-Graph are both near 190–200s. That is the shape §5's F1/F2
predicted section already expects, and it's worth someone re-running to see if it holds
at real N — but one or two points per topology is a signal, not a result, exactly as the
project's evidence-over-assertion rule requires stating.

**F6/F7/F16 are one figure with three y-axis views, for the interactive companion —
requested by Aditi, 30-Aug-26.** All three share the identical x axis
(`query_complexity_score`) and identical entities (topologies); only y changes (quality /
cost-log / latency). For §7's companion interactive reference: one chart, a
Latency/Cost/Quality toggle, and a single rich tooltip per point showing **all three
metrics plus the query ID and its capability list** regardless of which y is currently
plotted — so a reader checking latency can still see that a spike also came with unusual
cost or a low quality score, without switching views. For the printed thesis figures,
F6/F7/F16 stay three separate static panels (print has no toggle) but should be laid out
as a 1×3 row sharing one x axis and one legend, so the print reader gets the same
"same queries, three lenses" comparison the toggle gives the interactive version.

---

### F17 · Composite topology score (latency + cost + quality) — built 30-Aug-26, real data

**Question.** Collapsed into one number per topology, which condition trades off
latency, cost and quality best? Requested directly by Aditi, 30-Aug-26, alongside F18.

**Encoding.** For each (topology, query) point with data: latency and cost are
min-max normalized **within that query only** (comparing a topology only against other
topologies that ran the *same* query) and inverted so lower is better; quality
(`judge_mean_scope_adj`, falling back to `judge_mean` where the scope-adjusted score is
NULL — see the quality-source note below) is normalized the same way but not inverted.
The three normalized values are averaged and scaled to 0–100 for a per-point composite;
a topology's overall score is the unweighted mean of its available per-query
composites.

**Why per-query, not global, normalization.** An earlier pass of this computation
normalized across ALL available points regardless of which query they came from. That
conflates "this topology is efficient" with "this topology happened to only run cheap,
easy queries" — Monolith's only data point is Q10 (the heaviest query, complexity 15)
while Planner-Executor's are Q4/Q5 (lighter), so a global scale would structurally
penalize Monolith and favour Planner-Executor regardless of either topology's actual
efficiency. Per-query normalization only ever compares a topology against others that
ran the identical query, which removes that conflation. It does not remove the coverage
problem itself (see below) — it only stops one specific bias the coarser method would
have baked in silently.

**Weighting is a stated design choice, not a finding.** Latency, cost and quality are
weighted equally (1/3 each). No literature or prior analysis in this project justifies
that split — it is the simplest defensible default until there is a reason to weight
one axis more than another (e.g. a stated production SLA that treats latency as a hard
constraint rather than a soft cost). State this plainly wherever the score is cited.

**Quality-source gap, found while building this figure.** `judge_mean_scope_adj` is
NULL for every Monolith, Planner-Executor and Static-Graph DAG run and for most
Sequential/Dynamic-Graph runs — confirmed by direct query against `quality_scores`, and
by reading `score_topology_run.py` (~lines 690–735), which computes it unconditionally,
meaning the NULLs are runs scored before the scope-adjustment feature existed, not an
intentional per-topology exclusion. This figure falls back to plain `judge_mean` for
those points via `COALESCE`. Not yet logged as its own Risk Log entry — candidate for
one once it's confirmed whether the pilot re-scoring pass will apply scope-adjustment
uniformly to every run (if it does, this fallback becomes moot at pilot; if some runs
still lack it, the fallback needs to stay and be flagged in the figure caption).

**Current numbers (dev-mode snapshot, real data, NOT a finding — see n below).**

| Topology | Score | Queries covered |
|---|---|---|
| Static-Graph Routed | 83.6 | 3/10 (Q5, Q6, Q10) |
| Monolith | 73.9 | 1/10 (Q10) |
| Static-Graph DAG | 72.4 | 1/10 (Q10) |
| Planner-Executor | 69.5 | 2/10 (Q4, Q5) |
| Sequential | 32.0 | 3/10 (Q4, Q5, Q10) |
| Dynamic-Graph | 24.5 | 3/10 (Q5, Q6, Q10) |

**n and what it means.** Every topology here is scored on between 1 and 3 of 10 real
queries (11 once Q11 syncs into a run), opportunistic dev-mode coverage, not a
designed sample. Per Aditi's 30-Aug-26 direction this is not being treated as an
unfair-comparison problem to solve now — full N×Q coverage is a pilot-stage task, not
a dev-mode one — but the ranking above must not be read as a result until it is
recomputed at real coverage. State n explicitly next to this table wherever it is
reused.

**Sample code.** `sample-charts/F17_composite_score.py` — runs unchanged against
`run_store.db` at any coverage level; re-run at pilot rather than editing by hand.

---

### F18 · 3D interactive scatter — latency × cost × quality, target zone shaded — built 30-Aug-26, real data

**Question.** Where does each topology actually sit across all three axes at once,
and which (if any) fall in the region that is simultaneously fast, cheap and
high-quality?

**Encoding.** Rotatable `scatter3d` (Plotly): x = latency (s, lower better), y = cost
(USD, lower better), z = quality (judge mean, scope-adjusted where available, higher
better). Two point layers, independently toggleable: per-(topology, query) points, and
per-topology means (larger markers). Marker color and shape follow §2's topology design
system (shape approximated from Plotly's smaller 3D symbol set — a substitution, not a
new convention). Hover shows latency, cost, quality, quality source (scope-adj vs
judge_mean fallback), the composite score from F17, repetition count, capabilities
touched, and — for a per-query point — the query's complexity score.

**Target zone.** The octant bounded by below-median latency, below-median cost and
above-median quality (medians taken over the observed axis ranges, not a statistical
median) is shaded translucent green — the region Aditi specified as the one that
matters: low latency, low cost, high quality simultaneously. Toggleable off. A
topology-mean point sitting inside it is doing all three well at once; a point outside
it is trading at least one axis away, and the two other axes plus the hover detail show
which.

**Why 3D over three separate 2D Pareto plots (F1/F2).** F1/F2 already cover
quality-vs-cost and quality-vs-latency pairwise; this figure is not a replacement for
those (which support proper 2D Pareto-frontier reasoning per pair) but a single-view
composite for the results-chapter narrative and for the interactive companion, where
rotation and hover substitute for reading three separate panels side by side.

**n and status.** Same dev-mode snapshot and coverage caveat as F17 — built with real
data already on disk, not a designed pilot sample. Interactive only; no static print
form specified yet (open decision — see §9).

**Sample code.** `sample-charts/F18_3d_scatter_widget.html` — self-contained,
open directly in a browser, no server needed (Plotly loads from cdnjs). Re-derive the
hard-coded data arrays from `run_store.db` rather than hand-editing once pilot data
exists — see §8's build recipe for the source query.

**Known bug, fixed, worth keeping documented for reuse.** A translucent `mesh3d`
target-zone trace blocked mouse hover on `scatter3d` markers sitting inside or behind
it, even with `hoverinfo: 'skip'` set on the mesh — that setting only suppresses the
mesh's own tooltip, it does not reliably exclude the mesh from Plotly's gl3d picking
pass. Fixed by, in order of effect: (1) drawing the mesh trace *first* in the traces
array so marker traces (drawn later) win picking ties; (2) keeping mesh opacity low
(0.14); (3) `layout.hoverdistance` raised to 20; (4) sizing topology-mean markers up
(16) so they're easier pixel targets once picking works. Recorded in the sample file's
header comment too — this will recur on any future mesh3d-plus-scatter3d figure.

---

### F8 · Design-family frontier — **no owner**

**Question.** Does the design axis explain the variance better than the individual
implementation does? This tests the thesis's own conceptual framework rather than
reporting per-condition numbers.

**Encoding.** F6's axes; topologies grouped into families (by scope decision, or by
execution shape — two versions of the figure). Family envelope drawn solid, member
conditions faded behind it. Structural analog of AA's "Intelligence By Country" chart,
which groups models into creator-families and plots the family frontier.

**Predicted shape.** Static-Graph DAG and Static-Graph Routed track each other closely
while both diverge from Sequential — i.e. execution shape dominates scope decision.

**The result that matters most is the negative one.** If within-family spread is as
large as between-family spread, the two-axis framing is decorative, and the thesis must
say so rather than keeping a framework the data does not support. This figure is the
only one specified here that can falsify the conceptual model itself, which is why it
should not stay ownerless.

---

### F9 · Cumulative cost across the query set — **no owner**

**Encoding.** Stepped line. x = the 10 queries in fixed order, y = running total USD,
one line per topology. The most literal borrow of AA's step-line form.

**Why.** Answers "what does one full sweep cost per condition" directly, feeding T46's
budget confirmation as well as the results chapter. Line divergence *is* the budget
argument.

---

### F10 · Critical-difference diagram — **no owner** (T62)

**Question.** Across all 10 queries, which topologies differ in rank by more than
chance?

**Encoding.** Friedman test across conditions over the shared query set, followed by a
Nemenyi post-hoc; plotted as the standard critical-difference diagram — conditions on a
rank axis, with horizontal bars joining groups that are *not* significantly different.

**Provenance.** Not from AA — this comes from the ML benchmarking literature (Demšar's
procedure for comparing multiple classifiers over multiple datasets). Our structure is
exactly that shape: k systems × N shared workloads, same workloads for every system.

**Relation to T62's existing plan.** T62 currently specifies paired Wilcoxon per metric
plus effect sizes, which is the right *pairwise* test. The critical-difference diagram is
the omnibus companion — it controls for the multiple-comparison problem that arises from
running Wilcoxon across every pair of 6–8 conditions, which T50 already flags as needing
correction. Should be added to T62 rather than replacing anything.

**n.** N≥3 and all 10 queries. Not approximable at pilot scale.

---

### F11 · Forest plot of paired effect sizes — **no owner** (T62)

**Encoding.** One row per comparison against a nominated reference condition, showing
effect size with CI, zero-line marked. Reference should be Monolith (the simplest
condition — "does orchestration help at all?" is the baseline question).

**Why.** T62's DoD asks for effect sizes but specifies no figure for them. A table of
effect sizes is far harder to read than a forest plot, and effect size with CI is what
distinguishes "significant" from "meaningfully different."

---

### F12 · Scope-selection accuracy heatmap — **no owner**

**Question.** When a topology chooses its own scope, does it choose correctly?

**Encoding.** Heatmap, rows = the 5 capabilities, columns = the discovery-capable
conditions (Sequential, Static-Graph Routed, Dynamic-Graph, Swarm once built), cell =
rate of correct / over- / under-selection against `query_metadata.implied_tools_json`.
Diverging colour scale with a neutral midpoint at "correct" — over-selection and
under-selection are opposite failures and must not share a colour ramp direction.

**Why it must exist.** This is the measurement Static-Graph Routed was promoted to core
*for*, and the axis on which discovery-capable conditions are supposed to differ from
fixed-scope ones. It currently has one column in T1 and no figure. `judge_mean_scope_adj`
and `missing_implied_capabilities_json` are already captured, so the data is there.

**Predicted shape.** Routed near-perfect (3/3 exact matches at n=1); Dynamic-Graph
systematically under-selecting on diagnose, per R42.1.

---

### F13 · Execution timeline (Gantt) — **no owner**, buildable today

**Question.** *Why* do the concurrency numbers differ?

**Encoding.** Horizontal bars, one row per topology, x = seconds from first tool call,
one bar per capability coloured per §2. Shared x-axis across conditions.

**Source.** `tool_call.started_offset_s` / `ended_offset_s`, already captured;
`measurement/dependencies.py::render_timeline()` already produces the ASCII equivalent,
so this is a rendering change rather than new instrumentation.

**Why.** "% concurrency exploited" compresses the whole execution structure into one
scalar. The Gantt shows the structure: on the Q10 sample runs, Sequential tiles five
capabilities end-to-end across 193s, while DAG and Routed both fan three out at ~40s and
rejoin for recommend. This is the figure that explains the concurrency column rather than
restating it.

**Validity flag — see D3.** Building this exposed a Monolith run whose `predict`
completes in 0.16s against 38–42s elsewhere.

---

### F14 · Robustness probe behaviour (extend T67)

**Question.** How does each topology handle a request it should refuse?

**Encoding.** Q1 (was Q9 before the 5-Sep-26 renumbering) is tagged
`out_of_scope`/`robustness` with 0 implied capabilities. Small-multiples or a simple
categorical bar: refused correctly / answered informationally / ran tools anyway.
Running any capability on Q1 is a guardrail failure and belongs in T2's taxonomy.

**Why it is separate.** Every other figure measures capability. This measures restraint,
which is a different axis and currently has no figure. Q1 exists in the frozen set
precisely to test it.

---

### T2 · Failure taxonomy table (T67)

Stays a table (topology × failure category × count). Checked deliberately: none of the
AA patterns reviewed have a failure-mode analog, and forcing this into a chart would fit
the pattern rather than the data. If a figure is wanted alongside, a faceted count bar is
the honest option — noted so its absence is not read as an oversight.

---

### F15 · Variance across reps (T68)

**Encoding.** Adapted from AA's "Token Distribution" box-and-whisker (P5/P25/P50/P75/P95).

**Gated hardest on N of any figure here.** Box-plot quartiles are uninformative — arguably
misleading — below roughly 5 points per group. At the pilot's N=2, draw a strip plot
instead: both rep values as dots per topology with a thin connecting range line, no
computed quartiles. Switch to a true box plot only at N≥5. See D4 — that threshold is a
general convention, not derived from this project, and needs confirming or overriding.

---

## 6. Patterns reviewed and genuinely not applicable

- **Wall-clock project time as an x axis.** `run.started_at` spans Jul–Aug 2026 and would
  produce a convincing "improvement over time" line. It must not be drawn: prompts,
  instrumentation and capture code changed continuously across that window, so any trend
  measures this project's own development rather than topology behaviour. Recorded
  explicitly *because* the data supports the chart — nothing else would stop it being
  built.
- **Ordinal capability bands replacing named conditions.** AA bins hundreds of models into
  intelligence bands because it cannot label them individually. With 6–8 named conditions,
  banding discards the identity that is the entire point.
- **"15 of 57 models" selector chrome.** Solves a scale problem this dataset does not have.

---

## 7. Defects and validity flags surfaced while writing this spec

Classified per the project rule: a cross-condition difference caused by instrumentation
is a build defect, not a finding. All three are now logged in the Risk Log with fix
tasks.

- **R45 / T109 — Sequential token accounting does not close.** `master_total + Σ tool_call`
  equals `grand_total` exactly for Static-Graph DAG (279,039), Static-Graph Routed
  (268,022) and Planner-Executor (113,930), but is short by **192,126 tokens (40% of the
  run total)** for Sequential (run `5748995a`, Q10: master 31,327 + tools 258,180 =
  289,507 against grand 481,633). Most likely the executor-coordinator's five
  forced-tool-choice turns, captured in neither field. **Highest-priority item in this
  document** — it inverts F5's ranking. Does not affect `grand_total_tokens` or cost USD,
  so existing headline cost comparisons stand.
- **R46 / T110 — per-capability token capture missing for 2 of 6 core conditions.**
  Monolith and Dynamic-Graph record zero per-capability tokens. Blocks F4 for those rows
  and makes F5 degenerate for them. **The two must be separated before either is called
  correct:** for Monolith the absence is plausibly architectural (one context, raw tools
  attached directly — there is no per-capability LLM call to attribute), making the fix a
  written justification; for Dynamic-Graph it is more likely a real capture gap on the
  Magentic participant path, making it a defect.
- **R47 / T111 — suspect Monolith timing run.** On run `858e62f1` (Q10), `predict`
  completes in 0.16s against 38–42s in every other condition, and `get_delay_diagnosis`
  logs 0.0s. Matches the "stale artifacts satisfying the `upstream_missing` guards" defect
  class already recorded in T44's notes, not a speed advantage. Must be screened before
  any Monolith timing figure cites it. **Method note worth keeping:** this was invisible
  in the summary table and obvious the moment the timeline was plotted — an argument for
  building F13/D6.2 early rather than at the end of Phase 7.

---

## 8. Build recipes

Concrete source fields per figure, so building these later does not require re-deriving
the queries. DB: `supply_chain_topology_app/data/run_store.db` (**not**
`measurement/run_store.db`, which is stale and empty).

**Shared filters.** Every figure excludes rows where `run.path_fallback_used = 1`
(invalid per R10) **and** rows where `run.capture_version IS NULL` (written before T112,
Risk Log R48 — unversioned, not "version 1"; see §7), and should state `run_status`
handling in its caption. Join quality via `quality_scores` on `run_id`; join the workload
axis via `query_metadata` on `query_id`.

| Figure | Core fields / computation |
|---|---|
| T1 | `run.*` + `quality_scores.judge_mean`, `judge_mean_scope_adj`; concurrency per rows below |
| F1 / F2 / F3 | x `run.grand_total_cost_usd` or `run.wall_time_s`; y `quality_scores.judge_mean_scope_adj` ?? `judge_mean`; F3 facets on `query_metadata.complexity_tier` |
| F4 | `SUM(tool_call.prompt_tokens + completion_tokens) GROUP BY tool_call.tool_name`, canonicalised via `measurement/dependencies.py::canonical()` |
| F5 | coordinator share = `run.master_total_tokens / run.grand_total_tokens`; specialist share = `SUM(tool_call tokens) / grand_total`. **Assert the identity first** — abort the figure if `master_total + Σ tool_call != grand_total` (R45) |
| F6 / F7 / F16 / F8 | x = `query_metadata.query_complexity_score` (persisted column — read it, do not recompute); no row is NULL as of 5-Sep-26 (the zero-capability query now scores a floor value of 1, see §5), so no exclusion is needed here any more; y = judge quality (F6), `grand_total_cost_usd` on log scale (F7), or `wall_time_s` (F16); F8 groups topologies by the family columns in §2 |
| F9 | `run.grand_total_cost_usd`, cumulative sum over queries in fixed `query_id` order, partitioned by topology |
| F10 / F11 | per-(topology, query) metric matrix → Friedman + Nemenyi (F10); paired effect sizes vs reference condition (F11) |
| F12 | router/planner selection vs `query_metadata.implied_tools_json`; `quality_scores.missing_implied_capabilities_json` gives under-selection directly, over-selection = selected − implied |
| F13 | `tool_call.started_offset_s`, `ended_offset_s`, `tool_name`; anchor with `run.turn_times_json`. Mirrors `measurement/dependencies.py::render_timeline()` |
| F14 | runs where `query_id = 'Q1'` (was `'Q9'` before the 5-Sep-26 renumbering); outcome from `run.plan_presented`, `tool_call_count_actual` (any capability run on Q1 = guardrail failure), `final_answer` |
| F15 | metric spread across reps grouped by `(topology, query_id)` |
| F17 | per-(topology, query_id): x/y/z = `wall_time_s` / `grand_total_cost_usd` / `COALESCE(judge_mean_scope_adj, judge_mean)`, min-max normalized **within each query_id's own subset of topologies**, inverted for x/y; composite = mean(nx,ny,nz)×100; topology score = mean of its per-query composites |
| F18 | same source as F17, undivided (raw, not normalized) for the 3 axes; target-zone box = below/above the midpoint of each axis's observed range |

**Concurrency columns** (T1, and context for F13) are recomputed rather than stored:
`actual_span = max(ended_offset_s) − min(started_offset_s)`; `critical_path` = longest
dependency-respecting chain of per-capability durations over
`measurement/dependencies.py::TRUE_DEPENDENCIES`; `exploited = critical_path /
actual_span`. This is the same arithmetic as `concurrency_report()` and must not be
re-derived differently here — if the two disagree, that is a defect, not a variant.

---

## 9. Open decisions

1. **F15's strip-plot → box-plot threshold.** Proposed N≥5. A general statistical
   convention, not derived from this project's data. Confirm or override.
2. **F10's inclusion in T50's pre-registration.** D5.1 exists, but the Friedman/Nemenyi
   procedure must be written into T50 *before* T54 runs, not after. T50 is still Not
   Started, so this is still open rather than late.
3. **F11's reference condition.** Proposed Monolith, as the simplest baseline ("does
   orchestration help at all?"). Alternative is Planner-Executor as the most conventional
   multi-agent design.
4. **Whether the predicted shapes in §4/§5 are lifted into T50 verbatim.** They are worth
   something only if recorded before the data is seen.
5. **T64's task text still describes only the binary simple/multi_hop split.** The ordinal
   capability-count axis now lives in D2.1/D2.2, so T64 is arguably redundant — decide
   whether to narrow T64 to the tier-facet figure (F3, currently D2.3) or close it in
   favour of the D-series.
6. **Duplicate spec copy.** This file also exists at
   `papers-articles/Analysis_Report_Design_Spec.md`. Kept in sync manually; the `docs/thesis-topology-tradeoffs/reporting/`
   copy is canonical. Decide whether to delete the duplicate.
7. **`judge_mean_scope_adj` NULL for most historical runs — separate from R48.** Found
   30-Aug-26 while building F17. Not yet its own Risk Log entry. Open question: will the
   pilot re-scoring pass apply scope-adjustment to every run uniformly? If yes, F17/F18's
   `COALESCE` fallback becomes moot at pilot and can be dropped; if some runs will still
   lack it (e.g. a scoring model or method that only produces the plain judge_mean), the
   fallback must stay and every figure using it must flag which points are which quality
   source, as F17/F18 already do via `quality_adj` in the point data.
8. **F18's static print form.** Built interactive-only so far (§2's new interactivity
   rule). A PDF results chapter cannot embed rotation — decide whether the print form is
   a fixed-angle snapshot (which angle best shows the target zone?) or three paired 2D
   projections (F1/F2 already cover two of the three pairs).
