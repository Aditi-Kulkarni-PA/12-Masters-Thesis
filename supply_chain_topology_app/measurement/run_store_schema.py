"""
Run-record store schema (T14) — creates the SQLite database used to persist
topology-comparison results: one row per query execution (run), one row per
tool call within a run (tool_call), one row per query's fixed metadata
(query_metadata), and one row per run's quality scores (quality_scores,
written later/separately by T42's decoupled RAGAS/LLM-judge pipeline).

Usage:
    python measurement/run_store_schema.py --migrate        # safe, additive
    python measurement/run_store_schema.py --list-locked    # what is protected
    python measurement/run_store_schema.py --lock <run_id>  # protect a run
    python measurement/run_store_schema.py                  # DESTRUCTIVE recreate

DESTRUCTIVE — the bare invocation drops and recreates all tables (SQLite has no
CREATE OR REPLACE TABLE). That was tolerable while the schema churned and the data
was disposable; it is not tolerable now that runs cost real money and represent
results the thesis will cite.

`lock_rows` is the guard. Every table carries it (0 = disposable, 1 = protected), and
create_schema() REFUSES to run while any locked row exists — the caller must unlock
deliberately first. A locked run is one whose numbers are, or may become, evidence;
the cost of an accidental wipe is a re-run at real API expense, or worse, silently
reporting figures that were regenerated under different conditions.
"""
import os
import sqlite3
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent  # measurement/ -> supply_chain_topology_app/

DB_PATH = os.getenv(
    "SC_RUN_STORE_DB_PATH",
    str(_APP_DIR / "data" / "run_store.db"),
)

DDL = """
-- ---------------------------------------------------------------------
-- query_metadata: built once (T16), one row per frozen eval query.
-- held_out lives here (not on `run`) so every repetition of the same
-- query is consistently train or held-out.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS query_metadata;
CREATE TABLE query_metadata (
    query_id            TEXT PRIMARY KEY,
    query_text          TEXT NOT NULL,
    complexity_tier     TEXT,             -- 'simple' | 'multi_hop'
    implied_tools_json  TEXT,             -- JSON array of full tool names, e.g. ["predict_delivery_delays_tool","diagnose_delay_patterns_tool"]
    scenario_tags_json  TEXT,             -- JSON array, e.g. ["weather_scenario","regional_impact"]
    held_out            INTEGER DEFAULT 0, -- 0/1 -- T89's adaptive-selector split
    query_set_version   TEXT
);

-- ---------------------------------------------------------------------
-- run: one row per (topology, query_id, run_n) execution.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS run;
CREATE TABLE run (
    run_id                     TEXT PRIMARY KEY,
    query_id                   TEXT REFERENCES query_metadata(query_id),
    topology                   TEXT NOT NULL,   -- monolith|sequential|planner_executor|static_graph_dag|static_graph_routed|dynamic_graph|mesh|swarm|swarm_constrained_adaptive (see topologies/registry.py)
    selected_topology          TEXT,            -- unused: the adaptive-selector stretch goal was dropped (Risk Log R11)
    run_n                      INTEGER NOT NULL,
    model                      TEXT NOT NULL,
    config_hash                TEXT NOT NULL,
    prompt_versions_json       TEXT,            -- {"predict_delivery_delays": "<hash>", ...}
    payload_policy             TEXT,
    started_at                 TEXT,
    wall_time_s                REAL,
    master_prompt_tokens       INTEGER,
    master_completion_tokens   INTEGER,
    master_total_tokens        INTEGER,
    master_cost_usd            REAL,
    grand_total_tokens         INTEGER,
    grand_total_cost_usd       REAL,
    final_answer               TEXT,
    run_status                 TEXT,            -- success|partial|failed
    failure_category           TEXT,
    plan_presented             INTEGER,         -- 0/1, nullable
    turn1_plan_text            TEXT,            -- T94: turn-1 chat_response verbatim; NULL when no plan was produced
    path_fallback_used         INTEGER NOT NULL DEFAULT 0,  -- must be 0 for a valid measurement run (Risk Log R10)
    tool_call_count_expected   INTEGER,
    tool_call_count_actual     INTEGER,
    missing_expected_tools_json TEXT,            -- JSON array -- expected tools that never got called
    fabricated_narrative_json  TEXT,             -- JSON array -- MasterOutput narrative fields written despite the backing tool returning no data
    dependency_violations_json TEXT,             -- JSON array -- T97: capabilities that ran before their prerequisite finished, or without it
    query_text_actual          TEXT,             -- the exact prompt sent, incl. any SC_QUERY override and the appended orders path
    env_config_json            TEXT,             -- measurement-mode toggles as they ACTUALLY were: {"SC_NO_CACHE":1,"SC_DEV_PATH_FALLBACK":"0",...}
    turn_times_json            TEXT,             -- per-turn wall times, so plan-turn vs execution-turn cost is separable
    all_turns_json             TEXT              -- T42b: every turn's raw MasterOutput (or text), in order -- final_answer alone
                                                  -- only captures the LAST turn, which for a condition that finishes its work
                                                  -- inside turn 1 (monolith, observed 23-Aug-26) is a near-empty confirmation
                                                  -- ("already completed...") while the real synthesis sits in turn 1. Scoring
                                                  -- needs every turn to find it.
);

-- ---------------------------------------------------------------------
-- tool_call: many rows per run.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS tool_call;
CREATE TABLE tool_call (
    tool_call_id             TEXT PRIMARY KEY,
    run_id                   TEXT REFERENCES run(run_id),
    tool_name                TEXT NOT NULL,
    call_order               INTEGER,
    duration_s               REAL,
    input_chars              INTEGER,
    output_chars             INTEGER,
    output_text              TEXT,             -- the actual tool payload (predict_summary, delayed_orders, etc.)
    valid_json                INTEGER,          -- 0/1
    error                    TEXT,
    prompt_tokens            INTEGER,
    completion_tokens        INTEGER,
    total_tokens              INTEGER,
    cost_usd                 REAL,
    unprompted               INTEGER,          -- 0/1 -- tool_name not in query_metadata.implied_tools_json
    empty_payload            INTEGER DEFAULT 0, -- 0/1 -- returned valid JSON but its primary list was empty (no data produced)
    started_offset_s         REAL,             -- seconds from first tool start; needed to judge concurrency, not just order
    ended_offset_s           REAL,             -- seconds from first tool start
    retrieved_contexts_json  TEXT              -- nullable -- only for RAG-backed calls
);

-- ---------------------------------------------------------------------
-- quality_scores: one row per run, written later/separately by T42.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS quality_scores;
CREATE TABLE quality_scores (
    run_id                    TEXT PRIMARY KEY REFERENCES run(run_id),
    -- LLM-as-judge (judge.py -- 1-5 scale, gpt-4.1-mini fixed judge, any agent)
    judge_relevance           REAL,
    judge_faithfulness        REAL,
    judge_safety              REAL,
    judge_mean                REAL,
    judge_model               TEXT,
    -- RAGAS -- INTENTIONALLY UNPOPULATED for real runs (decision, 23-Aug-26, T42). These
    -- columns stay NULL on every row in this store, on purpose. evals/test_eval_rag.py
    -- computes real RAGAS faithfulness/answer_relevancy scores, but against a standalone
    -- recommendation_agent invocation (pre-refactor thesis_target/agent_adapter baseline),
    -- not against a real topology run -- its scores can't be tied to a run_id or topology,
    -- so they were never wired to write here. The LLM-judge columns above
    -- (judge_relevance/judge_faithfulness/judge_safety) already cover this comparison's
    -- quality axis per capability per run; duplicating that with RAGAS specifically for the
    -- recommend capability was judged not worth the added retrieved_contexts_json capture +
    -- scorer work it would need. Columns kept (harmless, nullable) rather than dropped, in
    -- case that calculus changes later -- but do not read NULL here as a bug or an
    -- unfinished T42.
    ragas_faithfulness        REAL,
    ragas_answer_relevancy    REAL,
    ragas_context_precision   REAL,
    ragas_hallucination_rate  REAL,
    -- Human eval (T52/T58, later)
    human_eval_score          REAL,
    scored_at                 TEXT
);

-- ---------------------------------------------------------------------
-- Indexes for T62's paired tests and T63's Pareto figures.
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_run_topology_query ON run(topology, query_id);
CREATE INDEX IF NOT EXISTS idx_toolcall_run ON tool_call(run_id);
"""


class LockedRowsError(RuntimeError):
    """Raised when a destructive operation would destroy rows marked lock_rows=1."""


def create_schema(db_path: str = DB_PATH, force: bool = False) -> None:
    """Drop and recreate every table. DESTRUCTIVE.

    Refuses while any locked row exists. `force=True` overrides — but the override is
    deliberately awkward to reach, because the failure mode it guards against (losing
    paid-for runs the thesis cites) is unrecoverable, while the inconvenience of
    unlocking first is a few seconds.
    """
    if not force:
        locked = locked_counts(db_path)
        if locked:
            detail = ", ".join(f"{t}={n}" for t, n in locked.items())
            raise LockedRowsError(
                f"REFUSING to recreate schema at {db_path}: locked rows present ({detail}).\n"
                f"  Inspect : python measurement/run_store_schema.py --list-locked\n"
                f"  Migrate : python measurement/run_store_schema.py --migrate   "
                f"(additive, keeps data — this is almost always what you want)\n"
                f"  Unlock  : python measurement/run_store_schema.py --unlock <run_id>"
            )
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(DDL)
        conn.commit()
    finally:
        conn.close()
    migrate_schema(db_path)          # give the fresh database its lock_rows columns

    # Wiping the store orphans every log file: nothing left maps a log to a topology,
    # model or cost. Leaving them behind is worse than deleting them, because a stale
    # log still reads like a record of a run that the database no longer contains.
    removed = 0
    if LOG_DIR.exists():
        for lp in LOG_DIR.glob("*.log"):
            try:
                lp.unlink()
                removed += 1
            except OSError as exc:
                print(f"  warning: could not delete log {lp}: {exc}")
    print(f"Schema (re)created at: {db_path} -- all prior data in these tables was wiped.")
    if removed:
        print(f"Also removed {removed} now-orphaned log file(s) from {LOG_DIR}.")


# ---------------------------------------------------------------------------
# lock_rows helpers
# ---------------------------------------------------------------------------
def ensure_lock_triggers(db_path: str = DB_PATH) -> list[str]:
    """Make the run_id cascade an INVARIANT, not a one-time action.

    set_lock() cascades at the moment it runs, which is not enough: quality_scores
    (T42's RAGAS/judge pipeline) and row_output are written *after* a run is locked, so
    without this they would arrive as lock_rows=0 on a locked run. The result would be
    a run that looks protected while its scores are silently disposable — precisely the
    half-protected state that makes an aggregate wrong rather than absent.

    A child row therefore inherits its parent run's lock state on INSERT, in the
    database, regardless of which tool wrote it.
    """
    created = []
    conn = sqlite3.connect(db_path)
    try:
        for table in _RUN_SCOPED:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "lock_rows" not in cols or "run_id" not in cols:
                continue
            name = f"trg_{table}_inherit_lock"
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
            conn.execute(f"""
                CREATE TRIGGER {name} AFTER INSERT ON {table}
                BEGIN
                    UPDATE {table} SET lock_rows = COALESCE(
                        (SELECT lock_rows FROM run WHERE run_id = NEW.run_id), 0)
                    WHERE rowid = NEW.rowid;
                END;
            """)
            created.append(name)
        conn.commit()
    finally:
        conn.close()
    return created


def locked_counts(db_path: str = DB_PATH) -> dict[str, int]:
    """{table: n_locked} for tables that have any. Empty dict means nothing is locked."""
    if not Path(db_path).exists():
        return {}
    out: dict[str, int] = {}
    conn = sqlite3.connect(db_path)
    try:
        for table in LOCK_TABLES:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "lock_rows" not in cols:
                continue
            n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE lock_rows = 1").fetchone()[0]
            if n:
                out[table] = n
    finally:
        conn.close()
    return out


def set_lock(run_ids, locked: bool = True, db_path: str = DB_PATH) -> dict[str, int]:
    """Lock or unlock the given run_ids and everything hanging off them.

    Cascades to tool_call / row_output / quality_scores by run_id, so a run can never
    end up locked while its own tool calls are disposable.
    """
    if isinstance(run_ids, str):
        run_ids = [run_ids]
    run_ids = list(run_ids)
    if not run_ids:
        return {}
    val = 1 if locked else 0
    marks = ",".join("?" * len(run_ids))
    touched: dict[str, int] = {}
    conn = sqlite3.connect(db_path)
    try:
        migrate_schema(db_path)
        cur = conn.execute(
            f"UPDATE run SET lock_rows = ? WHERE run_id IN ({marks})", [val, *run_ids])
        touched["run"] = cur.rowcount
        for table in _RUN_SCOPED:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "lock_rows" not in cols:
                continue
            cur = conn.execute(
                f"UPDATE {table} SET lock_rows = ? WHERE run_id IN ({marks})", [val, *run_ids])
            if cur.rowcount:
                touched[table] = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return touched


LOG_DIR = _APP_DIR / "log"


def _resolve_log(raw: str | None) -> Path | None:
    """run.log_path may be absolute or app-relative; return an existing Path or None."""
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = _APP_DIR / p
    return p if p.exists() else None


def orphan_logs(db_path: str = DB_PATH) -> list[Path]:
    """Log files in log/ that no surviving run claims.

    A log whose run row is gone is not evidence of anything — it cannot be tied to a
    topology, a model, or a cost. Keeping it just grows the folder and invites someone
    to read a stale file as if it described a current run.
    """
    if not LOG_DIR.exists():
        return []
    claimed = set()
    if Path(db_path).exists():
        conn = sqlite3.connect(db_path)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(run)")}
            if "log_path" in cols:
                for (raw,) in conn.execute("SELECT log_path FROM run WHERE log_path IS NOT NULL"):
                    p = _resolve_log(raw)
                    if p:
                        claimed.add(p.resolve())
        finally:
            conn.close()
    return sorted(p for p in LOG_DIR.glob("*.log") if p.resolve() not in claimed)


def _logs_for(conn, run_ids) -> list[Path]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(run)")}
    if "log_path" not in cols or not run_ids:
        return []
    marks = ",".join("?" * len(run_ids))
    out = []
    for (raw,) in conn.execute(
            f"SELECT log_path FROM run WHERE run_id IN ({marks}) AND log_path IS NOT NULL",
            list(run_ids)):
        p = _resolve_log(raw)
        if p:
            out.append(p)
    return out


def delete_runs(scope: str = "unlocked", db_path: str = DB_PATH,
                run_ids=None, dry_run: bool = False, purge_orphan_logs: bool = False) -> dict:
    """Delete runs and everything hanging off them. Default scope spares locked runs.

    scope="unlocked" -> every run with lock_rows = 0   (the safe default)
    scope="all"      -> every run, locked included     (caller must mean it)

    Children go first so a foreign-key-enabled database never sees an orphan, and the
    whole thing runs in one transaction: a half-deleted run would still read as valid
    data, which is worse than either outcome.
    """
    if scope not in ("unlocked", "all"):
        raise ValueError(f"scope must be 'unlocked' or 'all', got {scope!r}")
    if not Path(db_path).exists():
        return {"runs": 0}

    conn = sqlite3.connect(db_path)
    try:
        if run_ids:
            marks = ",".join("?" * len(run_ids))
            where, params = f"run_id IN ({marks})", list(run_ids)
            if scope == "unlocked":
                where += " AND lock_rows = 0"
        elif scope == "unlocked":
            where, params = "lock_rows = 0", []
        else:
            where, params = "1=1", []

        targets = [r[0] for r in conn.execute(f"SELECT run_id FROM run WHERE {where}", params)]
        locked_total = conn.execute("SELECT COUNT(*) FROM run WHERE lock_rows = 1").fetchone()[0]
        report = {"runs": len(targets), "spared_locked": locked_total if scope == "unlocked" else 0}

        if not targets:
            # No rows to drop, but an orphan sweep must still happen: "nothing to
            # delete" is the normal state when the only thing left to clean is logs.
            report["children"] = {}
            report["logs"] = []
            if purge_orphan_logs:
                orphans = orphan_logs(db_path)
                if dry_run:
                    report["orphan_logs"] = [str(x) for x in orphans]
                else:
                    for lp in orphans:
                        try:
                            lp.unlink()
                            report["logs"].append(str(lp))
                        except OSError as exc:
                            print(f"  warning: could not delete orphan log {lp}: {exc}")
            return report
        if dry_run:
            marks = ",".join("?" * len(targets))
            report["children"] = {
                t: conn.execute(
                    f"SELECT COUNT(*) FROM {t} WHERE run_id IN ({marks})", targets).fetchone()[0]
                for t in _RUN_SCOPED
                if "run_id" in {c[1] for c in conn.execute(f"PRAGMA table_info({t})")}
            }
            report["logs"] = [str(x) for x in _logs_for(conn, targets)]
            if purge_orphan_logs:
                report["orphan_logs"] = [str(x) for x in orphan_logs(db_path)]
            report["run_ids"] = targets
            return report

        marks = ",".join("?" * len(targets))
        # Resolve log paths BEFORE the rows naming them are deleted — afterwards the
        # mapping is gone and the files would silently become orphans.
        doomed_logs = _logs_for(conn, targets)
        children = {}
        conn.execute("BEGIN")
        for table in _RUN_SCOPED:                      # children first
            cols = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
            if "run_id" not in cols:
                continue
            cur = conn.execute(f"DELETE FROM {table} WHERE run_id IN ({marks})", targets)
            if cur.rowcount:
                children[table] = cur.rowcount
        conn.execute(f"DELETE FROM run WHERE run_id IN ({marks})", targets)
        conn.commit()

        removed_logs = []
        for lp in doomed_logs:
            try:
                lp.unlink()
                removed_logs.append(str(lp))
            except OSError as exc:
                print(f"  warning: could not delete log {lp}: {exc}")
        if purge_orphan_logs:
            for lp in orphan_logs(db_path):
                try:
                    lp.unlink()
                    removed_logs.append(str(lp))
                except OSError as exc:
                    print(f"  warning: could not delete orphan log {lp}: {exc}")
        report["children"] = children
        report["logs"] = removed_logs
        report["run_ids"] = targets
        return report
    finally:
        conn.close()


def list_locked(db_path: str = DB_PATH) -> list[tuple]:
    """(run_id, topology, model, run_n, started_at, cost_usd) for every locked run."""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(run)")}
        if "lock_rows" not in cols:
            return []
        cost = "grand_total_cost_usd" if "grand_total_cost_usd" in cols else "NULL"
        return list(conn.execute(
            f"SELECT run_id, topology, model, run_n, started_at, {cost} "
            f"FROM run WHERE lock_rows = 1 ORDER BY started_at"))
    finally:
        conn.close()


# Columns added after the initial schema, as (table, column, type). Applied by
# migrate_schema() with ALTER TABLE so an existing database picks them up WITHOUT
# the destructive drop-and-recreate above. Every entry must be nullable or carry a
# DEFAULT — SQLite cannot add a NOT NULL column without one.
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("run", "turn1_plan_text", "TEXT"),                      # T94
    ("tool_call", "empty_payload", "INTEGER DEFAULT 0"),     # empty-result detection
    ("run", "fabricated_narrative_json", "TEXT"),            # narrative written over no data
    ("tool_call", "started_offset_s", "REAL"),               # T97
    ("tool_call", "ended_offset_s", "REAL"),                 # T97
    ("run", "dependency_violations_json", "TEXT"),           # T97
    ("run", "query_text_actual", "TEXT"),                    # reproducibility
    ("run", "env_config_json", "TEXT"),                      # reproducibility
    ("run", "turn_times_json", "TEXT"),                      # reproducibility
    # lock_rows: 0 = disposable, 1 = protected. Defined here rather than in DDL so
    # there is exactly one definition of the column; create_schema() calls
    # migrate_schema() so a freshly created database gets it too.
    # Seconds from the START OF TURN 1 to the first tool call. Tool offsets are
    # relative to the first tool start, so without this anchor every timeline began at
    # the first tool and the model's reasoning before it was invisible -- which is
    # exactly where a single-context condition spends its extra time reading a 50k-char
    # prompt. A timeline that hides the dominant cost of the condition under test is
    # worse than no timeline.
    ("run", "tool_base_offset_s", "REAL"),
    ("run", "log_path", "TEXT"),                             # log file this run wrote
    ("run", "all_turns_json", "TEXT"),                        # T42b: every turn's raw MasterOutput, in order
    # Orchestration turns that are NOT specialist calls: a coordinator turn recorded
    # through record_sub_agent_usage under a name matching no tool_call row. Sequential
    # produces one per planned step (it drives the coordinator itself rather than
    # returning those turns to the harness), and they dominate its cost -- ~190k prompt
    # tokens against ~73k for the specialists on run 6cae417b. Without these columns
    # they are only visible inside grand_total, so a coordinator-vs-specialist cost
    # split cannot be computed from the store at all. NULL for topologies that make no
    # such turns.
    ("run", "orchestration_prompt_tokens", "INTEGER"),
    ("run", "orchestration_completion_tokens", "INTEGER"),
    ("run", "orchestration_total_tokens", "INTEGER"),
    ("run", "orchestration_cost_usd", "REAL"),
    # How many messages the specialist's own run produced. prompt_tokens rise with the
    # number of internal round-trips, since each re-sends the conversation so far, so
    # tokens alone cannot separate one large prompt from several ordinary ones repeated
    # -- the open question in R36. NULL for raw-tool calls, which run no agent.
    ("tool_call", "agent_messages", "INTEGER"),
    ("run", "lock_rows", "INTEGER NOT NULL DEFAULT 0"),
    ("tool_call", "lock_rows", "INTEGER NOT NULL DEFAULT 0"),
    ("row_output", "lock_rows", "INTEGER NOT NULL DEFAULT 0"),
    ("quality_scores", "lock_rows", "INTEGER NOT NULL DEFAULT 0"),
    ("query_metadata", "lock_rows", "INTEGER NOT NULL DEFAULT 0"),
    # Scope-adjusted quality score (T42i, 29-Aug-26, R42.4 follow-up): judge_mean only
    # ever averages capabilities that produced a judged artifact, so a run that silently
    # skips a capability the query implied is judged only on what it DID attempt --
    # never penalized for the gap. These columns zero-fill any implied-but-never-
    # attempted capability into the same average instead, alongside (not replacing)
    # judge_mean, so both "quality of what was delivered" and "quality including scope
    # misses" stay independently queryable.
    ("quality_scores", "judge_relevance_scope_adj", "REAL"),
    ("quality_scores", "judge_faithfulness_scope_adj", "REAL"),
    ("quality_scores", "judge_safety_scope_adj", "REAL"),
    ("quality_scores", "judge_mean_scope_adj", "REAL"),
    ("quality_scores", "missing_implied_capabilities_json", "TEXT"),
    # T112 / Risk Log R48 (30-Aug-26): nothing on a run row said which version of the
    # capture path produced it. config_hash cannot substitute -- it hashes model +
    # prompt versions and churns for reasons unrelated to instrumentation (23 distinct
    # values across 49 runs), so "same config_hash" does not mean "same capture code".
    # Concretely: dynamic_graph's runs before 29-Aug-26 14:15 recorded zero specialist
    # tokens (agent_completion_middleware not yet attached); its runs after record
    # 94-96k. Indistinguishable except by eyeballing started_at. capture_version is a
    # small monotonic int, bumped in instrumentation.py (CAPTURE_VERSION) whenever the
    # capture MECHANISM changes -- not on every prompt/model change, which is what
    # config_hash is already for. Analysis code should filter/group on this rather than
    # on date. NULL on every run written before this column existed; those rows predate
    # versioning entirely and should be excluded from any figure, not treated as
    # version 1.
    ("run", "capture_version", "INTEGER"),
    # Persisted query complexity score (30-Aug-26, Aditi's request following the
    # progression-axis correction, docs/Analysis_Report_Design_Spec.md). Computed once
    # by measurement/build_query_metadata.py::query_complexity_score() -- weight table
    # and rationale live there, not duplicated here. NULL for a query needing no
    # capabilities (Q9): it is not the lightest point on this axis, it is a different
    # kind of query (an out-of-scope/refusal probe), and 0 would misleadingly read as
    # "the lightest real query" -- exactly what Aditi flagged.
    ("query_metadata", "query_complexity_score", "INTEGER"),
    # Dev-vs-pilot run phase (T113 / Risk Log R49, 30-Aug-26). Nothing in the schema
    # distinguished an exploratory dev-mode run from an official pilot run -- the only
    # proxy was started_at, which breaks the moment a pilot run is redone for a fix or
    # a dev run happens after the pilot start date. Sourced from the new SC_RUN_PHASE
    # env var at write time (execute_topology.py), values "dev" / "pilot" by
    # convention -- this file does not enforce the vocabulary, same as payload_policy.
    # NULL for every run written before this column existed AND for any run where the
    # env var was left unset: NULL means "phase unknown", not "assume dev" -- an
    # unset env var during a real pilot run must not silently mislabel as dev.
    ("run", "run_phase", "TEXT"),
    # Harness-assigned grouping and ordering (5-Sep-26, run_experiment.py). One harness
    # invocation stamps every run it launches with the same batch_id, so a checkpoint or
    # an interrupted run is auditable: which runs belong together, and in what order they
    # actually executed. execution_order is the position of this run within its batch's
    # (randomized, by default) sequence, distinct from run_n -- run_n identifies WHICH
    # repetition of a (topology, query_id) pair this is; execution_order identifies WHEN,
    # relative to every other run in the same batch, it executed. Both NULL for any run
    # not launched through run_experiment.py (e.g. a manual execute_topology.sh call).
    ("run", "batch_id", "TEXT"),
    ("run", "execution_order", "INTEGER"),
    # Query-complexity bucket for the stratified quality table (5-Sep-26, Aditi's
    # mapping): low / medium / high / very_high. Synced from query_set_v1.xlsx by
    # build_query_metadata.py, alongside query_complexity_score -- kept as a separate
    # column rather than derived from the score at analysis time, so the bucket
    # boundaries have exactly one definition, same reasoning as query_complexity_score
    # itself. NULL for a query not yet assigned a bucket.
    ("query_metadata", "complexity_bin", "TEXT"),
    # Scheduling efficiency, persisted rather than recomputed at report time
    # (6-Sep-26, Risk Log R65). The measure is critical_path_s / busy, where busy is
    # the span minus coordinator idle time. 1.0 is the ceiling: it is the floor the
    # dependency graph implies, so a value ABOVE 1.0 is arithmetically unreachable
    # while respecting that graph and means a dependent pair ran concurrently.
    #
    # Three columns rather than one, because a single ratio cannot carry the
    # distinction the analysis needs:
    #   scheduling_ratio       the raw figure, kept for traceability
    #   scheduling_deviation   abs(1 - ratio); 0 is optimal, and this is the value
    #                          aggregated across runs
    #   infeasible_overlap     1 when ratio > 1.0, i.e. the overlap exceeded what the
    #                          dependency graph permits; aggregated as a rate and
    #                          reported alongside the deviation
    #
    # All three are NULL for a run with no capability tool calls (a correct decline on
    # the out-of-scope probe, for example), where the measure is undefined rather than
    # zero.
    ("run", "scheduling_ratio", "REAL"),
    ("run", "scheduling_deviation", "REAL"),
    ("run", "infeasible_overlap", "INTEGER"),

    # Execution-timing measures the proposal lists in Sections 7.2.1 and 7.2.2 but that
    # were never persisted (6-Sep-26). concurrency_report() computed all four from the
    # tool_call offsets at report time and kept only the scheduling ratio derived from
    # them, so critical-path latency and the two concurrency measures could not be
    # aggregated at all.
    #
    #   critical_path_s      longest dependency-constrained sequence of tool calls. The
    #                        floor the dependency graph implies. Proposal: Critical-path
    #                        latency.
    #   actual_span_s        wall-clock from first tool start to last tool end.
    #   fully_serial_s       sum of every capability call's duration, i.e. the time the
    #                        run would take with no overlap at all.
    #   coordinator_idle_s   time inside the span where no tool was running. For a
    #                        model-driven coordinator this is deliberation, not a
    #                        scheduling failure, so it is kept separate.
    #
    # Achievable concurrency (fully_serial_s / critical_path_s) and actual concurrency
    # (fully_serial_s / actual_span_s) are derived from these in analysis/measures.py
    # rather than stored, because both are ratios of columns already here.
    ("run", "critical_path_s", "REAL"),
    ("run", "actual_span_s", "REAL"),
    ("run", "fully_serial_s", "REAL"),
    ("run", "coordinator_idle_s", "REAL"),

    # Analysis exclusion, per proposal Section 7.4. The proposal requires four
    # pre-specified categories, three of which are excluded from analysis and logged,
    # but the store had no way to mark a run as excluded (6-Sep-26). Without it the only
    # way to remove a contaminated run from the aggregates was to delete it, which
    # destroys real API spend and is irreversible.
    #
    # NULL means the run is included. A non-NULL value is the reason string and removes
    # the run from every aggregate while leaving the row and its children intact.
    ("run", "excluded_reason", "TEXT"),
)

# Every table carrying lock_rows. `run` is the unit a human locks; the rest are
# locked by cascade because a run's tool calls and scores are meaningless without it
# — and, more to the point, a half-deleted run is worse than a deleted one, because
# it still reads as valid data.
LOCK_TABLES: tuple[str, ...] = (
    "run", "tool_call", "row_output", "quality_scores", "query_metadata",
)
_RUN_SCOPED: tuple[str, ...] = ("tool_call", "row_output", "quality_scores")


def migrate_schema(db_path: str = DB_PATH, verbose: bool = False) -> list[str]:
    """Add any missing post-initial columns in place. Idempotent and non-destructive.

    Returns the list of columns actually added. Safe to call on every write: the
    PRAGMA check is cheap and skipping it risks a run failing at INSERT time on a
    database created before the column existed.
    """
    if not Path(db_path).exists():
        return []
    added: list[str] = []
    conn = sqlite3.connect(db_path)
    try:
        for table, column, coltype in _MIGRATIONS:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue                      # table absent -> create_schema handles it
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                added.append(f"{table}.{column}")
        if added:
            conn.commit()
    finally:
        conn.close()
    ensure_lock_triggers(db_path)      # cascade must hold for rows written later
    if verbose and added:
        print(f"Schema migrated at {db_path}: added {', '.join(added)}")
    return added


if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]

    def _ids(flag):
        i = argv.index(flag)
        return [a for a in argv[i + 1:] if not a.startswith("-")]

    if "--migrate" in argv:
        changed = migrate_schema(verbose=True)
        print("Schema already current — nothing to add." if not changed else "Migration complete.")

    elif "--list-locked" in argv:
        rows = list_locked()
        if not rows:
            print("No locked rows. Every run in this store is disposable.")
        else:
            print(f"{len(rows)} locked run(s) — protected from create_schema():\n")
            print(f"  {'run_id':10} {'topology':18} {'model':14} {'n':>2}  {'cost':>9}  started")
            for rid, topo, model, n, started, cost in rows:
                c = f"${cost:.4f}" if isinstance(cost, (int, float)) else "-"
                print(f"  {str(rid)[:8]:10} {str(topo):18} {str(model):14} {n:>2}  {c:>9}  {started}")
            print()
            for t, n in locked_counts().items():
                print(f"  {t:16} {n} locked row(s)")

    elif "--lock" in argv or "--unlock" in argv:
        lock = "--lock" in argv
        ids = _ids("--lock" if lock else "--unlock")
        if not ids:
            print("Usage: --lock <run_id> [run_id ...]   (8-char prefixes accepted)")
            sys.exit(2)
        conn = sqlite3.connect(DB_PATH)
        full = []
        for want in ids:
            hit = conn.execute(
                "SELECT run_id FROM run WHERE run_id = ? OR run_id LIKE ?", (want, want + "%")
            ).fetchall()
            if len(hit) != 1:
                print(f"  {want!r}: {'no such run' if not hit else 'ambiguous prefix'}")
                sys.exit(2)
            full.append(hit[0][0])
        conn.close()
        touched = set_lock(full, locked=lock)
        verb = "Locked" if lock else "Unlocked"
        print(f"{verb} {len(full)} run(s): " + ", ".join(t[:8] for t in full))
        for t, n in touched.items():
            print(f"  {t:16} {n} row(s)")

    else:
        try:
            create_schema()
        except LockedRowsError as e:
            print(e, file=sys.stderr)
            sys.exit(1)