"""Validate the LIVE prompt tree. Run before any measurement batch.

Was `topology_specs/validate_specs.py`, which gated a staging folder. That folder is
gone — every prompt in it is now byte-identical to the tree under `config/prompts/`,
so the gate has been repointed at the real files. Validating a copy of what ships is
worth nothing; this validates what ships.

Checks, per condition:
  1. every @include resolves to a real file
  2. no @directive survives expansion (needs the recursive loader — load_config.py)
  3. no shared/neutral file names a tool, agent, or other topology (the R13 confound)
  4. no changelog/date/experiment vocabulary inside files the model actually reads
  5. domain agent prompts each carry an '## Error handling' section

Conditions are derived from topologies.registry rather than hardcoded, so a topology
added there cannot silently escape validation.

Usage:
    uv run python supply_chain_topology_app/config/prompts/helper/validate_specs.py

Exit code 1 on any failure, so this can gate a build.
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent          # .../config/prompts/helper
PROMPTS = BASE.parent                           # .../config/prompts
APP_DIR = PROMPTS.parent.parent                 # .../supply_chain_topology_app
COORD = PROMPTS / "coordinators"
SEARCH = [PROMPTS / "shared", PROMPTS / "agents"]

INC = re.compile(r"^@([A-Za-z0-9_]+)[ \t]*$", re.M)
MAX_DEPTH = 5

# Derive conditions from the registry — one source of truth for topology names.
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
try:
    from topologies.registry import REGISTRY
    CONDITIONS = {n: f"{s.coordinator_key}.md" for n, s in REGISTRY.items()}
except Exception as exc:                                    # pragma: no cover
    print(f"FATAL: cannot import topologies.registry ({exc}).", file=sys.stderr)
    sys.exit(1)

TOOL_NAMES = ["predict_delivery_delays_tool", "diagnose_delay_patterns_tool",
              "delay_simulations_tool", "recommendation_tool", "email_alert_tool",
              "get_delay_diagnosis", "simulate_order_delays", "recommend_actions",
              "fetch_delayed_orders_for_email"]
# Experiment vocabulary only. "condition" alone is a false positive — the domain uses
# it for weather ("stormy", "invalid condition value"). Only flag the experimental-arm
# sense.
TOPOLOGY_PATTERNS = [r"\bMonolith\b", r"\bPlanner-Executor\b", r"\bStatic-Graph\b",
                     r"\bDynamic-Graph\b", r"\bSwarm\b", r"\bMesh\b",
                     r"\btopolog(?:y|ies|ical)\b",
                     r"\b(?:this|each|every|other|per|across|the other) conditions?\b",
                     r"\bconditions? (?:gets?|receives?|has|have)\b"]
META = [r"\d{1,2}-[A-Z][a-z]{2}-\d{2}", r"\bR1[0-9]\b", r"\bR2[0-9]\b",
        r"Risk Log", r"\bcorrected\b", r"\brevised\b"]

# README.md files document the prompts for humans; the model never reads them, so they
# are exempt from the neutrality and meta-commentary rules.
SKIP = {"README.md"}

# The capability files exist precisely to name a roster, so the "no tool names in
# shared/" rule cannot apply to them. They are hand-authored (capability_details.md is
# the source core/tool_descriptions.py parses), and must still be free of topology
# vocabulary and meta-commentary. Each is checked for the roster it should carry, so a
# half-written file fails loudly instead of passing by exemption. Section 3b separately
# checks that the names here match what the wiring actually registers.
CAPABILITY_PARTIALS = {
    # capability_details.md holds the ONE copy of the description text, role-labelled.
    # The four variant files are now a single `@@capabilities:<name>` directive each,
    # rendered flat at load time — so none of them contains a tool name on disk.
    "capability_details.md": 0,
    "tool_capabilities.md": 0,
    "raw_tool_capabilities.md": 0,
    "participant_capabilities.md": 0,
    "peer_capabilities.md": 0,
}

failures = []


def read(key, extra=()):
    for d in list(SEARCH) + list(extra):
        p = d / f"{key}.md"
        if p.exists():
            return p.read_text().strip()
    return None


def expand_full(text, extra=(), depth=0):
    if depth >= MAX_DEPTH:
        return text

    def sub(m):
        body = read(m.group(1), extra)
        return expand_full(body, extra, depth + 1) if body is not None else m.group(0)

    return INC.sub(sub, text)


print("=" * 68)
print("1-2. INCLUDE RESOLUTION + FULL EXPANSION")
print("=" * 68)
for cond, fname in CONDITIONS.items():
    src_path = COORD / cond / fname
    if not src_path.exists():
        failures.append(f"{cond}: {fname} missing under coordinators/{cond}/")
        print(f"  [FAIL] {cond:22} {fname} MISSING")
        continue
    extra = (COORD / cond,)
    src = src_path.read_text()
    declared = INC.findall(src)
    missing = [d for d in declared if read(d, extra) is None]
    out = expand_full(src, extra)
    leftover = INC.findall(out)
    if missing:
        failures.append(f"{cond}: unresolved includes {missing}")
    if leftover:
        failures.append(f"{cond}: directives survive expansion {leftover}")
    status = "OK" if not missing and not leftover else "FAIL"
    print(f"  [{status}] {cond:22} includes={len(declared):2}  "
          f"expanded={len(out.splitlines()):4} lines")

print()
print("=" * 68)
print("2b. EVERY CONDITION MUST RECEIVE THE SAME SHARED PARTIALS")
print("=" * 68)
# Checks 1-2 prove each condition's includes RESOLVE. They say nothing about whether two
# conditions making the same decision were given the same text -- and that gap cost three
# separate measurement artifacts (29-Aug-26), each found only by paying for a run:
# a planner missing the capability descriptions omitted a prerequisite; the same planner,
# once given them, took every capability because the scope rule lived inline in two other
# conditions; and chatbot_behavior was excluded outright from a third. All three were
# statically visible as a difference in the shared partials each condition expands to.
#
# CORE_SHARED lists the partials that carry a decision every coordinator makes. A
# condition that legitimately cannot act on one declares it in EXEMPT with the reason,
# so an omission is either justified in writing or a failure -- never silent.
CORE_SHARED = {
    "security_guardrails",     # every coordinator reads user text
    "chatbot_behavior_basic",  # informational-vs-action, query interpretation
    "scope_selection",         # which capabilities this request needs
    "dependency_basics",       # what each capability consumes and produces
    "self_check",              # reflect-retry and the step budget
    "exception_handling",      # never fabricate; report failures
}
EXEMPT = {
    # Mesh B reasons rewritten 30-Aug-26 (T40 correctness pass). The gated file is
    # entry_point.md, which frames ONLY where the request enters (predict); it does not
    # carry mesh_peers.md and never did -- an earlier version of this comment claimed "every
    # agent -- entry included -- carries shared/mesh_peers.md", which was never true. The
    # content this gate looks for is real, but lives one level deeper than this check reads:
    # each of the 5 capabilities gets its OWN small routing agent, built in Python by
    # topologies/mesh.py::_build_router(), and THAT agent's instructions -- not any of the 8
    # domain/narrative agents' -- carry coordinators/mesh/mesh_peers.md verbatim via
    # get_instruction('mesh_peers', topology='mesh'). mesh_peers.md (and mesh_handoff.md,
    # Mesh A's counterpart) moved out of shared/ into coordinators/mesh/ 30-Aug-26: both are
    # Mesh-only content, and this file's own R19/R22 corrections already established that
    # topology-only content does not belong in shared/, however convenient the filename
    # prefix made the old location look.
    # Same one-file-per-condition gate limitation already noted for sequential and
    # static_graph_dag: the exempted content exists, just not reachable by walking
    # entry_point.md's own @include tree, which is all this check can see.
    ("mesh", "self_check"): "no coordinator exists to hold a shared step budget centrally. The SHARED hop budget (HOP_BUDGET, enforced in code by _RunContext.take_hop()) is stated in prose to the decision-maker that actually spends it: coordinators/mesh/mesh_peers.md's 'Hop budget: SHARED' paragraph, carried by each of the 5 per-node routing agents (topologies/mesh.py::_build_router()), not by the 8 domain/narrative agents, which never decide to spend a hop themselves. mesh_peers.md moved out of shared/ into coordinators/mesh/ 30-Aug-26 -- it is Mesh-only content and does not belong in shared/ per this file's own R19/R22 precedent",
    ("mesh", "exception_handling"): "no coordinator. coordinators/mesh/mesh_peers.md's 'malformed or incomplete result' paragraph is carried by each of the 5 routing agents, since a routing agent is the one deciding whether to re-address a peer that returned something unusable. A domain agent's OWN tool failures are handled the same way every other topology's domain agents handle them: an ad hoc '## Error handling' section in its own agents/*.md prompt (validate_specs.py section 5 checks for this directly), not this shared partial",
    ("mesh", "dependency_basics"): "no coordinator derives order, and peer edges do NOT come from TRUE_DEPENDENCIES -- _peer_edges() is a deliberately complete graph (see its docstring) and the import is explicitly absent from topologies/mesh.py. coordinators/mesh/mesh_peers.md (30-Aug-26) directly includes @dependency_basics and @concurrency_policy -- the identical shared text every other order-deciding prompt gets, not a paraphrase -- and each of the 5 routing agents carries it together with that node's own peer list (name + CAPABILITY_DESCRIPTIONS consumes/produces fact, built in _build_router()), so a routing agent has the same interface facts and the same reasoning instruction as every other condition, without any file prescribing a fixed sequence",
    ("sequential", "scope_selection"): "the executor does not choose scope -- the plan does",
    # static_graph_dag's gated prompt (coordinator.md, the turn-1 triage) reads the raw
    # user message, so security_guardrails and chatbot_behavior_basic are NOT exempt here
    # (29-Aug-26) -- only scope and order stay code-decided, which is what the remaining
    # two exemptions cover.
    ("static_graph_dag", "scope_selection"): "the graph runs the full pipeline for any in-scope action request; the triage classifies, it does not narrow capabilities",
    ("static_graph_dag", "dependency_basics"): "the WorkflowBuilder graph encodes prerequisites via fan-out/fan-in edges derived from TRUE_DEPENDENCIES; no model derives order",
    # self_check/exception_handling describe tool-step behaviour (retries, the step
    # budget, reporting tool-level failures) -- the triage calls no tools. That content
    # lives in aggregator.md, the file that actually reports on the completed run, which
    # is not the file this gate checks for static_graph_dag (see coordinator_key in
    # registry.py) -- same situation as sequential's sequence_planner.md.
    ("static_graph_dag", "self_check"): "the triage turn calls no tools; the step budget belongs to aggregator.md, which reports on the completed run",
    ("static_graph_dag", "exception_handling"): "the triage turn calls no tools; aggregator.md already includes it and reports tool-level failures",
    # dynamic_graph's gated prompt is ALSO its triage (coordinator.md, same pattern as
    # static_graph_dag) -- but the reasons differ from DAG's, because DG's scope/order
    # is genuinely ledger-discovered rather than fixed in code. All four live in
    # manager.md instead (not gated, same structural gap as sequential/sequence_planner.md
    # and static_graph_dag/aggregator.md -- CONDITIONS only checks one file per condition).
    ("dynamic_graph", "scope_selection"): "the triage does not choose scope; manager_agent's own plan() call decides it per query, inside the workflow -- see manager.md",
    ("dynamic_graph", "dependency_basics"): "the triage calls no tools and makes no ordering decision; dependency reasoning belongs to manager.md, which the ledger consults on every round",
    ("dynamic_graph", "self_check"): "the triage turn calls no tools; the step budget and reflect-retry belong to manager.md, which drives the actual tool-execution loop",
    ("dynamic_graph", "exception_handling"): "the triage turn calls no tools; manager.md and aggregator.md both carry it and report tool-level failures",
    # static_graph_routed's gated prompt (coordinator.md) is BOTH the triage and the
    # scope/order planner in one turn -- unlike static_graph_dag, it is NOT exempt from
    # scope_selection or dependency_basics: deciding scope and order is exactly this
    # condition's point (T108), so no exemption is entered for either here.
    ("static_graph_routed", "self_check"): "the router turn calls no tools; the step budget belongs to aggregator.md, which reports on the completed run",
    ("static_graph_routed", "exception_handling"): "the router turn calls no tools; aggregator.md already includes it and reports tool-level failures",
}

def shared_partials_of(cond: str, fname: str) -> set[str]:
    """Every shared/ partial that ends up inside this condition's expanded prompt."""
    src_path = COORD / cond / fname
    if not src_path.exists():
        return set()
    extra = (COORD / cond,)
    seen: set[str] = set()

    def walk(text: str, depth: int = 0) -> None:
        if depth >= MAX_DEPTH:
            return
        for name in INC.findall(text):
            body = read(name, extra)
            if body is None:
                continue
            if (PROMPTS / "shared" / f"{name}.md").exists():
                seen.add(name)
            walk(body, depth + 1)

    walk(src_path.read_text())
    return seen

_actual = {c: shared_partials_of(c, f) for c, f in CONDITIONS.items()}
for cond in CONDITIONS:
    missing = sorted(p for p in CORE_SHARED - _actual[cond]
                     if (cond, p) not in EXEMPT)
    exempted = sorted(p for p in CORE_SHARED - _actual[cond] if (cond, p) in EXEMPT)
    if missing:
        failures.append(
            f"{cond}: missing shared partial(s) {missing} that other conditions receive "
            f"— either include them or add an EXEMPT entry in validate_specs.py saying "
            f"why this condition cannot act on them")
    note = f"  exempt: {', '.join(exempted)}" if exempted else ""
    print(f"  [{'FAIL' if missing else 'OK'}] {cond:22} "
          f"core={len(CORE_SHARED)-len(missing)-len(exempted)}/{len(CORE_SHARED)}"
          f"{'  MISSING: ' + ', '.join(missing) if missing else ''}{note}")

print()
print("=" * 68)
print("3-4. SHARED FILES MUST BE TOPOLOGY-NEUTRAL AND FREE OF META-COMMENTARY")
print("=" * 68)
for f in sorted((PROMPTS / "shared").glob("*.md")):
    if f.name in SKIP:
        continue
    t = f.read_text()
    tools = [n for n in TOOL_NAMES if n in t]
    topos = [p for p in TOPOLOGY_PATTERNS if re.search(p, t, re.I)]
    metas = [p for p in META if re.search(p, t)]

    if f.name in CAPABILITY_PARTIALS:
        # Generated roster file: naming tools is its purpose. Assert the roster is
        # complete instead — a partial that silently generated empty would otherwise
        # sail through, which is the failure mode that produced the stale hardcoded
        # bullet list this file replaced.
        want = CAPABILITY_PARTIALS[f.name]
        bad = bool(topos or metas) or len(tools) != want
        if len(tools) != want:
            failures.append(
                f"shared/{f.name}: names {len(tools)} tools, expected {want} "
                f"— check the file against the wiring in core/tool_descriptions.py")
        if topos or metas:
            failures.append(f"shared/{f.name}: topology_words={topos} meta={metas}")
        print(f"  [{'FAIL' if bad else 'OK'}] {f.name:26} "
              f"roster={len(tools)}/{want} (generated)  topo_words={len(topos)} meta={len(metas)}")
        continue

    bad = bool(tools or topos or metas)
    if bad:
        failures.append(f"shared/{f.name}: tools={tools} topology_words={topos} meta={metas}")
    print(f"  [{'FAIL' if bad else 'OK'}] {f.name:26} "
          f"tools={len(tools)} topo_words={len(topos)} meta={len(metas)}")

print()
print("=" * 68)
print("3b. PROMPT TREE AND FUNCTION-CALLING SCHEMA MUST AGREE")
print("=" * 68)
# capability_details.md is the hand-authored source; core/tool_descriptions.py parses it
# and feeds the same strings to tool_description= in the schema. What can still go wrong
# is a variant that stops including the shared body, or a prompt naming a tool the wiring
# never registers -- the model would then be told a name it cannot call.
try:
    from core.tool_descriptions import (check_prompt_schema_agreement, VARIANT_FILES,
                                        CAPABILITY_DESCRIPTIONS)
    problems = check_prompt_schema_agreement(PROMPTS / "shared")
    # Adjacency check: in the rendered list every callable name must sit on the SAME
    # line as its description. Splitting them cost monolith its dependency ordering.
    from core.tool_descriptions import render_flat, CAPABILITY_VARIANTS as _CV
    for _fn, (_tag, _names) in _CV.items():
        if not _names:
            continue
        _rendered = render_flat(_fn)
        for _cap, _label in _names.items():
            _line = next((l for l in _rendered.splitlines() if l.startswith(f"- {_label} ")), "")
            if " -- " not in _line:
                problems.append(f"{_fn}: {_label} has no description on its own line")
    print(f"  [OK] capability_details.md      parsed {len(CAPABILITY_DESCRIPTIONS)} capabilities "
          f"({', '.join(CAPABILITY_DESCRIPTIONS)})")
    for name in VARIANT_FILES:
        bad = any(name in p for p in problems)
        print(f"  [{'FAIL' if bad else 'OK'}] {name:30} "
              f"{'see failures below' if bad else 'includes @capability_details, names agree'}")
    failures.extend(problems)
except Exception as exc:
    print(f"  [FAIL] could not verify: {exc}")
    failures.append(f"prompt/schema agreement check failed: {exc}")

print()
print("=" * 68)
print("4b. COORDINATOR PROMPTS MUST NOT LEAK THE EXPERIMENT")
print("=" * 68)
# Checks 3-4 only ever looked at shared/, on the assumption that a per-topology file is
# allowed to know its own topology. True for mechanics ("you dispatch to specialists"),
# false for experiment framing. A coordinator that reads "every other condition of this
# system", "handicapping this condition", or a design-doc citation is being told it is an
# arm of a benchmark — a demand characteristic, and the R13 confound by another route.
# Naming its own architecture is fine; naming the STUDY is not.
EXPERIMENT_LEAK = [
    r"\b(?:this|each|every|other|per|across|the other|another) conditions?\b",
    r"\bconditions? (?:gets?|receives?|has|have|exists?|is built)\b",
    r"\bhandicap\w*\b", r"\bbaseline condition\b", r"\bthe experiment\b",
    r"\bthesis\b", r"\b[A-Za-z_]+_(?:Spec|Reference)\.md\b", r"Risk Log",
    r"\bR1[0-9]\b", r"\bR2[0-9]\b", r"§\s*[0-9]", r"\bMAST\b",
    r"\bcomparison the\b", r"\bapple-to-apple\b", r"\bcorrected \d{1,2}-[A-Z][a-z]{2}-\d{2}\b",
]
# Files that live under coordinators/ but are design docs, never sent to a model.
# Filename-only entries apply to every condition's folder; (cond, filename) tuples
# apply to one folder only -- dynamic_graph/master.md is retired (29-Aug-26, see its own
# text) but planner_executor/master.md and monolith/master.md are real, live prompts and
# must stay checked.
NOT_PROMPTS = {"README.md", "dispatch_router_spec.md"}
NOT_PROMPTS_SCOPED = {("dynamic_graph", "master.md")}

for cond, fname in CONDITIONS.items():
    d = COORD / cond
    if not d.exists():
        continue
    for f in sorted(d.glob("*.md")):
        if f.name in NOT_PROMPTS or (cond, f.name) in NOT_PROMPTS_SCOPED:
            continue
        t = f.read_text()
        leaks = [p for p in EXPERIMENT_LEAK if re.search(p, t, re.I)]
        if leaks:
            hits = []
            for p in leaks:
                for m in re.finditer(p, t, re.I):
                    line = t[:m.start()].count("\n") + 1
                    hits.append(f"L{line}:{m.group(0)!r}")
            failures.append(f"coordinators/{cond}/{f.name}: experiment leak {hits[:6]}")
        print(f"  [{'FAIL' if leaks else 'OK'}] {cond}/{f.name:26} leaks={len(leaks)}")

print()
print("=" * 68)
print("5. DOMAIN AGENT CONSISTENCY")
print("=" * 68)
for f in sorted((PROMPTS / "agents").glob("*.md")):
    if (f.name in SKIP or f.name.endswith("_output_schema.md")
            or "fallback" in f.name or f.name == "master_expert.md"):
        continue
    t = f.read_text()
    has_err = bool(re.search(r"^## Error handling", t, re.M))
    if not has_err:
        failures.append(f"agents/{f.name}: no '## Error handling' section")
    print(f"  [{'OK' if has_err else 'FAIL'}] {f.name:32} error-handling section")

print()
print("=" * 68)
if failures:
    print(f"FAILED — {len(failures)} problem(s):")
    for x in failures:
        print("  -", x)
    sys.exit(1)
print("ALL CHECKS PASSED")
