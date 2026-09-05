"""True dependency graph and violation checking (T97).

The graph below is what the CODE actually requires, verified against the pipeline
rather than taken from any prompt — see papers-articles/Multi_Agent_Topology_Reference.md
section 7. No prompt states it; every model-driven topology has to derive the ordering
for itself from what each capability consumes and produces. This module is how we check
whether it derived it correctly.

Why a violation is not the same as a failure
--------------------------------------------
A dependent capability that starts too early gets `upstream_missing` and can recover by
obtaining the prerequisite and retrying. The run may still end with every result
present. So "did it succeed" and "did it respect the dependencies" are different
questions, and only the second one measures orchestration quality. A topology that
blunders into the right answer after three wasted calls should not score the same as
one that sequenced correctly first time.

Why start ORDER is not enough
-----------------------------
Checking `call_order` alone silently passes the most interesting failure. When a
coordinator dispatches everything at once, start order can look perfectly correct while
a dependent capability is in fact reading state its prerequisite has not finished
writing. Observed 22-Aug-26: all five dispatched together, `call_order` ascending and
apparently fine, yet diagnose and recommend both ran against a system prediction had
not finished populating. The check therefore compares a dependent's START against its
prerequisite's END, which needs timing, not just sequence.
"""
from __future__ import annotations

# capability -> the capabilities whose output it consumes.
# Keyed by the wrapped tool names used in the run store's tool_call.tool_name.
TRUE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "predict_delivery_delays_tool": (),
    "diagnose_delay_patterns_tool": ("predict_delivery_delays_tool",),
    # simulate reads predict's delayed-orders CSV. T95 proposes decoupling it to read
    # the raw input orders instead; if that lands, this becomes () and simulate joins
    # predict at level 0.
    "delay_simulations_tool": ("predict_delivery_delays_tool",),
    "recommendation_tool": ("predict_delivery_delays_tool", "diagnose_delay_patterns_tool"),
    "email_alert_tool": ("predict_delivery_delays_tool",),
}

# Raw pipeline tool names (Monolith attaches these directly) mapped onto the same graph.
_RAW_ALIASES: dict[str, str] = {
    "predict_delivery_delays": "predict_delivery_delays_tool",
    "get_delay_diagnosis": "diagnose_delay_patterns_tool",
    "simulate_order_delays": "delay_simulations_tool",
    "recommend_actions": "recommendation_tool",
    "fetch_delayed_orders_for_email": "email_alert_tool",
}


def canonical(tool_name: str) -> str:
    """Map a topology-specific tool name onto the canonical capability name."""
    return _RAW_ALIASES.get(tool_name, tool_name)


def _capability_calls(calls: list[dict]) -> list[dict]:
    """Drop any call whose canonical name is not one of the five real capabilities.

    Fixed 30-Aug-26 (build defect, not a finding): Mesh's three narrative-peer calls
    (simulate_summary_tool/recommend_summary_tool/email_summary_tool) are recorded as
    ordinary tool_call rows, but they are not graph capabilities -- each is a synchronous
    continuation of its own capability inside _act(), consuming that capability's output,
    not an independently schedulable node. With no entry in TRUE_DEPENDENCIES they fell
    back to `.get(name, ())`, reading as zero-prerequisite and independent of everything --
    producing claims like "email_summary_tool could have run alongside
    predict_delivery_delays_tool (ready at 0.00s)", which is not something the run could
    ever have done. No other topology has this problem because none of them capture their
    narrative-writing step as a separate tool_call row at all (it is one field on the
    closing/aggregator turn's own structured output) -- so filtering these rows out here,
    rather than adding them to TRUE_DEPENDENCIES, is what keeps the comparison fair:
    Mesh's narrative calls become invisible to this analysis the same way every other
    topology's narrative-writing turn already is.
    """
    return [c for c in calls if canonical(c["tool_name"]) in TRUE_DEPENDENCIES]


# The longest chain: predict -> diagnose -> recommend. This is the real critical path
# for latency in any topology, however the calls are arranged.
CRITICAL_PATH: tuple[str, ...] = (
    "predict_delivery_delays_tool",
    "diagnose_delay_patterns_tool",
    "recommendation_tool",
)


def check_dependencies(calls: list[dict]) -> list[dict]:
    """Return one entry per dependency violation.

    *calls* is a list of dicts with: tool_name, started_offset_s, ended_offset_s.
    Offsets are relative to the first tool start; absolute values do not matter, only
    their ordering relative to one another.

    Two violation kinds:
      - "missing"   the prerequisite never ran at all in this run
      - "premature" the dependent STARTED before the prerequisite ENDED

    Only the FIRST attempt at each capability is judged. A retry after an
    `upstream_missing` is the documented recovery path, and counting it as a second
    violation would penalise a topology twice for one mistake.
    """
    first_attempt: dict[str, dict] = {}
    for c in _capability_calls(calls):
        name = canonical(c["tool_name"])
        if name not in first_attempt:
            first_attempt[name] = c

    violations: list[dict] = []
    for name, call in first_attempt.items():
        for prereq in TRUE_DEPENDENCIES.get(name, ()):
            prereq_call = first_attempt.get(prereq)
            if prereq_call is None:
                violations.append({
                    "tool": name, "requires": prereq, "kind": "missing",
                    "detail": f"{name} ran but {prereq} never did",
                })
                continue
            dep_start = call.get("started_offset_s")
            pre_end = prereq_call.get("ended_offset_s")
            if dep_start is None or pre_end is None:
                continue  # timing unavailable (older run) — cannot judge, do not guess
            if dep_start < pre_end:
                violations.append({
                    "tool": name, "requires": prereq, "kind": "premature",
                    "detail": (f"{name} started at {dep_start:.2f}s, before {prereq} "
                               f"finished at {pre_end:.2f}s"),
                })
    return violations


def describe(violations: list[dict]) -> str:
    """One-line human summary for the run-validity report."""
    if not violations:
        return "none"
    return "; ".join(f"{v['tool']} before {v['requires']} ({v['kind']})" for v in violations)


def critical_path_s(calls: list[dict]) -> float:
    """Shortest possible tool-execution span given the dependency graph.

    The longest chain of dependent work: nothing can finish sooner than this, however
    the calls are arranged. Computed from THIS run's actual durations, so it is a fair
    floor for this run rather than a fixed number.
    """
    dur: dict[str, float] = {}
    for c in _capability_calls(calls):
        name = canonical(c["tool_name"])
        if name not in dur:                     # first attempt only, as elsewhere
            s = c.get("started_offset_s"); e = c.get("ended_offset_s")
            dur[name] = (e - s) if (s is not None and e is not None) else 0.0

    memo: dict[str, float] = {}

    def longest_to(name: str) -> float:
        if name in memo:
            return memo[name]
        prereqs = [p for p in TRUE_DEPENDENCIES.get(name, ()) if p in dur]
        memo[name] = dur.get(name, 0.0) + (max((longest_to(p) for p in prereqs), default=0.0))
        return memo[name]

    return round(max((longest_to(n) for n in dur), default=0.0), 2)


def concurrency_report(calls: list[dict]) -> dict:
    """How much of the available concurrency this run actually exploited.

    actual_span   wall-clock from first tool start to last tool end
    critical_path floor implied by the dependency graph
    exploited     critical_path / actual_span, 1.0 = perfectly parallel

    A code-scheduled topology (Static-Graph DAG) should sit at ~1.0 by construction; a
    model-driven one only gets there by working the independence out for itself. That
    gap is the point of comparing them, so it is recorded rather than prompted away.

    One topology sits at the opposite extreme for a structural reason rather than a
    reasoning failure: Sequential forces exactly one tool per turn, so it cannot overlap
    anything, even steps the graph leaves independent. A near-zero `exploited` figure
    there is the condition working as designed, not a scheduling defect — read it
    against `coordinator_idle_s`, which separates serialized tool execution from the
    extra model round-trips that shape costs.
    """
    # Filtered once here (not just inside critical_path_s) so every figure in this report
    # -- span, serial total, idle time, and the CP floor they are compared against -- is
    # computed over the same set of real capabilities. Leaving narrative-peer rows in
    # only the span/serial arithmetic would have let their duration inflate actual_span_s
    # (denominator) while critical_path_s (numerator) ignored them, understating
    # "exploited" for no orchestration reason.
    cap_calls = _capability_calls(calls)
    starts = [c["started_offset_s"] for c in cap_calls if c.get("started_offset_s") is not None]
    ends = [c["ended_offset_s"] for c in cap_calls if c.get("ended_offset_s") is not None]
    if not starts or not ends:
        return {}
    actual = round(max(ends) - min(starts), 2)
    cp = critical_path_s(cap_calls)
    serial = round(sum((c["ended_offset_s"] - c["started_offset_s"]) for c in cap_calls
                       if c.get("started_offset_s") is not None), 2)
    # Idle time inside the span: intervals where no tool was running. For a
    # model-driven coordinator this is deliberation — receiving results, reasoning,
    # emitting the next call. It is NOT a scheduling failure, and conflating the two
    # was wrong: a run can overlap everything it possibly could and still show headroom
    # purely because the coordinator paused to think between waves. A code-scheduled
    # topology pays almost none of this, so isolating it is what makes the comparison
    # between reasoning-driven and code-driven scheduling legible.
    intervals = sorted((c["started_offset_s"], c["ended_offset_s"]) for c in calls
                       if c.get("started_offset_s") is not None
                       and c.get("ended_offset_s") is not None)
    merged: list[list[float]] = []
    for st, en in intervals:
        if merged and st <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], en)
        else:
            merged.append([st, en])
    busy = sum(en - st for st, en in merged)
    idle = round(max(0.0, actual - busy), 2)

    return {
        "actual_span_s": actual,
        "critical_path_s": cp,
        "fully_serial_s": serial,
        "coordinator_idle_s": idle,
        "exploited": round(cp / actual, 3) if actual else None,
        "headroom_s": round(actual - cp, 2),
    }


def _independent(a: str, b: str) -> bool:
    """True if neither capability consumes the other's output, directly or transitively."""
    def reaches(src: str, dst: str, seen=None) -> bool:
        seen = seen or set()
        if src in seen:
            return False
        seen.add(src)
        for pre in TRUE_DEPENDENCIES.get(src, ()):
            if pre == dst or reaches(pre, dst, seen):
                return True
        return False
    return not reaches(a, b) and not reaches(b, a)


def missed_concurrency(calls: list[dict]) -> list[dict]:
    """Pairs that were free to overlap but ran one after the other.

    Names the specific opportunity rather than only reporting a percentage, so a low
    exploited-concurrency figure can be read without reconstructing the timeline by
    hand. Only counts a pair as missed if the later one could actually have started
    when the earlier did — i.e. its own prerequisites were already complete by then.
    """
    first: dict[str, dict] = {}
    for c in _capability_calls(calls):
        name = canonical(c["tool_name"])
        if name not in first:
            first[name] = c

    def ready_at(name: str) -> float:
        """Earliest time this capability could have started: when its last prereq ended."""
        ends = [first[p]["ended_offset_s"] for p in TRUE_DEPENDENCIES.get(name, ())
                if p in first and first[p].get("ended_offset_s") is not None]
        return max(ends, default=0.0)

    missed: list[dict] = []
    names = list(first)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ca, cb = first[a], first[b]
            if None in (ca.get("started_offset_s"), ca.get("ended_offset_s"),
                        cb.get("started_offset_s"), cb.get("ended_offset_s")):
                continue
            if not _independent(a, b):
                continue
            overlapped = (ca["started_offset_s"] < cb["ended_offset_s"]
                          and cb["started_offset_s"] < ca["ended_offset_s"])
            if overlapped:
                continue
            later, earlier = (b, a) if cb["started_offset_s"] >= ca["started_offset_s"] else (a, b)
            # Could the later one have gone when the earlier one did?
            if ready_at(later) <= first[earlier]["started_offset_s"] + 1e-6:
                missed.append({
                    "a": earlier, "b": later,
                    "detail": (f"{later} could have run alongside {earlier} "
                               f"(ready at {ready_at(later):.2f}s, actually started "
                               f"{first[later]['started_offset_s']:.2f}s)"),
                })
    return missed


# Short display names — the "_tool" suffix adds nothing to a timeline.
_SHORT = {
    "predict_delivery_delays_tool": "predict",
    "diagnose_delay_patterns_tool": "diagnose",
    "delay_simulations_tool": "simulate",
    "recommendation_tool": "recommend",
    "email_alert_tool": "email",
}


def render_timeline(calls: list[dict], indent: str = "    ",
                    base_offset_s: float | None = None,
                    turn_times: list | None = None) -> list[str]:
    """Execution timeline as lines: waves bracketed, idle gaps called out, tool calls
    attributed to the turn they actually happened in.

    Reading five start/end pairs and working out which overlapped is exactly the sort
    of thing a reader should not have to do by hand. Grouping concurrent calls into a
    bracketed wave and marking the gaps between waves makes the shape of a run legible
    at a glance -- and makes two topologies visually comparable without arithmetic.

    *base_offset_s* is the gap between the start of turn 1 and the first tool call, and
    *turn_times* the per-turn wall times. Both are needed because tool offsets are
    measured from the FIRST TOOL START: without them the timeline silently begins at the
    first tool and every second of model reasoning before it disappears. For a
    single-context condition that reasoning is the dominant cost -- monolith spent ~10s
    reading a 50k-char prompt before calling anything, none of which the timeline showed.
    Rendering tool time alone made the condition look faster than it is and hid the
    quantity under measurement.

    Tool calls are NOT assumed to all happen inside turn 1. An earlier version hardcoded
    that (every wave printed before "turn 1 end", turn 2+ always labelled "no tools"),
    which was true only for a run that fires every tool during the plan-presenting turn.
    Observed 23-Aug-26, run 1d267c8e: plan_presented=True, turn_times=[4.22, 157.38] --
    turn 1 was JUST the plan (4.22s), every tool call happened during turn 2 (the "Yes,
    proceed" turn), and the old code printed "turn 1 end 4.22" after tool offsets that
    ran up to 33.64s -- an impossible, backwards-looking timestamp. Waves are now
    attributed to whichever turn's [start, end) window their own start falls inside, so
    the printed turn boundaries are never earlier than the tool calls that preceded them.

    Everything is shifted onto a single axis anchored at turn-1 start, so the numbers
    read as "seconds into the run" rather than "seconds into the tool window".
    """
    rows = [c for c in calls
            if c.get("started_offset_s") is not None and c.get("ended_offset_s") is not None]
    if not rows:
        return []
    rows.sort(key=lambda c: c["started_offset_s"])

    base = base_offset_s or 0.0
    if base_offset_s is not None:
        rows = [dict(c, started_offset_s=c["started_offset_s"] + base,
                     ended_offset_s=c["ended_offset_s"] + base) for c in rows]

    # Group into waves: consecutive calls that overlap in time belong to one wave.
    waves: list[list[dict]] = []
    for c in rows:
        if waves and c["started_offset_s"] < max(x["ended_offset_s"] for x in waves[-1]):
            waves[-1].append(c)
        else:
            waves.append([c])

    width = max(len(_SHORT.get(canonical(c["tool_name"]), c["tool_name"])) for c in rows)
    width = max(width, len("turn 1 start"), len("turn 1 end"))
    lines: list[str] = []

    def _emit_wave(wave: list[dict], prev_end: float) -> float:
        start = min(c["started_offset_s"] for c in wave)
        gap = start - prev_end
        if gap > 0.05:
            lines.append(f"{indent}{'.' * 7} {gap:.2f}s idle {'.' * 7}")
        for i, c in enumerate(wave):
            name = _SHORT.get(canonical(c["tool_name"]), c["tool_name"])
            bar = ""
            if len(wave) > 1:
                bar = " |" if 0 < i < len(wave) - 1 else (" +" if i == 0 else " +")
            note = ""
            if len(wave) > 1 and i == len(wave) // 2:
                note = f"-- {len(wave)} at once"
            lines.append(
                f"{indent}{name:<{width}} {c['started_offset_s']:7.2f} -> "
                f"{c['ended_offset_s']:7.2f}{bar}{note}"
            )
        return max(c["ended_offset_s"] for c in wave)

    if base_offset_s is None or not turn_times:
        # No turn information available (older run, or a topology with no turn
        # concept) -- fall back to a flat wave listing, same as always.
        prev_end = None
        if base_offset_s is not None:
            lines.append(f"{indent}{'turn 1 start':<{width}} {0.0:7.2f}")
            if base > 0.05:
                lines.append(f"{indent}{'.' * 5} {base:.2f}s model reasoning (no tool running) {'.' * 5}")
            prev_end = 0.0
        for wave in waves:
            prev_end = _emit_wave(wave, prev_end if prev_end is not None else
                                  min(c["started_offset_s"] for c in wave))
        return lines

    # Turn boundaries on the SAME axis as the (now base-shifted) tool offsets: turn 1
    # spans [0, turn_times[0]], turn 2 spans [turn_times[0], turn_times[0]+turn_times[1]],
    # and so on.
    boundaries: list[tuple[float, float]] = []
    clock = 0.0
    for t in turn_times:
        boundaries.append((clock, clock + float(t)))
        clock += float(t)

    remaining_waves = list(waves)
    prev_end = 0.0
    tools_started = False
    for turn_idx, (t_start, t_end) in enumerate(boundaries, start=1):
        # Every turn gets its own "start" line, not just turn 1 -- an earlier version
        # printed "turn 1 start" once before the loop and nothing else, so turn 2 (and
        # any later turn) had an "end" boundary with no matching "start" anywhere in
        # the output. Confirmed 23-Aug-26 (user): the printed timeline went straight
        # from tool calls to "turn 2 end" with no "turn 2 start" line at all.
        lines.append(f"{indent}{f'turn {turn_idx} start':<{width}} {t_start:7.2f}")

        # A wave belongs to this turn if it started before this turn's end -- waves are
        # already time-ordered, so this consumes them in order as turn boundaries advance.
        this_turn_waves = []
        while remaining_waves and remaining_waves[0][0]["started_offset_s"] < t_end + 1e-6:
            this_turn_waves.append(remaining_waves.pop(0))

        if not this_turn_waves:
            # ALWAYS print a boundary line for this turn, even an empty one -- an
            # earlier version only did this for turn_idx > 1, which silently dropped
            # turn 1 entirely whenever turn 1 was plan-only (no tools). Confirmed
            # 23-Aug-26 (user): a real run's timeline jumped straight from "turn 1
            # start 0.00" to tool calls and "turn 2 end 169.64" with no "turn 1 end"
            # anywhere -- looked like a typo, was actually this branch never firing
            # for turn_idx == 1. The note distinguishes WHY the turn was empty: a
            # leading plan turn (no tools have run yet) reads differently from a
            # trailing confirmation turn after everything already happened.
            note = "no tools this turn" if tools_started else "plan / no tools yet"
            lines.append(f"{indent}{f'turn {turn_idx} end':<{width}} {t_end:7.2f}   ({note})")
            prev_end = max(prev_end, t_end)
            continue

        if not tools_started:
            # First tool call in the whole run: the gap from absolute run start (which
            # may span turn 1 entirely plus part of this turn) is model reasoning, not
            # "idle" -- nothing has run yet for the model to be idle between. Printed
            # once here instead of as a separate, overlapping header line at the top.
            first_start = this_turn_waves[0][0]["started_offset_s"]
            if first_start > 0.05:
                lines.append(f"{indent}{'.' * 5} {first_start:.2f}s model reasoning "
                             f"(no tool running) {'.' * 5}")
            prev_end = first_start
            tools_started = True

        for wave in this_turn_waves:
            prev_end = _emit_wave(wave, prev_end)

        tail = t_end - prev_end
        if tail > 0.05:
            lines.append(f"{indent}{'.' * 5} {tail:.2f}s model composing answer {'.' * 5}")
        elif turn_idx > 1:
            lines.append(f"{indent}{'.' * 5} tools ran in turn {turn_idx} {'.' * 5}")
        lines.append(f"{indent}{f'turn {turn_idx} end':<{width}} {t_end:7.2f}")
        prev_end = t_end

    # Any waves left over (tool offset fell after the last recorded turn -- should not
    # happen, but do not silently drop them) get appended rather than lost.
    for wave in remaining_waves:
        prev_end = _emit_wave(wave, prev_end)

    lines.append(f"{indent}{'run end':<{width}} {prev_end:7.2f}")
    return lines
