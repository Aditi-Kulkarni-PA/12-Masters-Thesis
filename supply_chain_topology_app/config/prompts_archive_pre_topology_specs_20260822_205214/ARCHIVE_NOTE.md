# Archive: prompts as they stood before the topology_specs migration

Taken 2026-08-22 20:52:14 IST, immediately before `topology_specs/` was copied
into the app as the new prompt source of truth.

## What this is
A verbatim snapshot of `config/prompts/` from the single-topology (Planner-Executor
only) codebase — the state every result produced up to this point was generated from.
Keep it: any run recorded before this timestamp was produced by these files, and
reproducing or re-scoring those runs requires them.

## Known defects present in this snapshot (fixed in topology_specs, NOT here)
- `shared/chatbot_behavior.md` leaks the true dependency graph ("Follow prerequisite
  chains (predict before diagnose, diagnose before recommend, predict before email)")
  to every condition — Risk Log R15.
- `shared/chatbot_behavior.md` names the five wrapped tool identifiers in its Query
  Interpretation table — R18.
- `agents/master_expert.md` hardcodes call order in seven places ("SEQUENTIAL
  EXECUTION ONLY", per-section pre-requisite blocks, the §7 numbered list) — R13.
- `agents/master_expert.md` §1 says `"error": "upstream_missing"` (lowercase); the
  payload from `prediction_pipeline/prediction_server.py` is `{"Error": ...}`.
- `agents/email_alert.md` has no `## Error handling` section (its rule sits under
  `## Instructions` item 1), unlike the other four domain prompts.
- Per-field narrative rules for `simulate_summary`/`recommendation_summary`/
  `email_alert_summary` exist only in `master_expert.md`, so no other coordinator
  would have had them.

## Restoring
    rm -rf config/prompts && cp -R config/prompts_archive_pre_topology_specs_20260822_205214 config/prompts
    # then remove ARCHIVE_NOTE.md and load_config.py.orig-reference from the restored tree
