"""F3 — Grid-conformance + morning-compression pressure.

Pure / deterministic (stdlib only; no network, solver, or pandas), mirroring
buildability.py (F1) and cross_program_bottleneck.py (F2). From the raw section
days/times the engine workbook drops, it computes a START-time grid-conformance
rate (reusing timeblocks.on_grid, which already fails open) and a 9 AM-1 PM
"morning-compression" distribution plus the required-course pairs that are
structurally mutually exclusive because every one of their sections is
morning-locked.

Everything here is a STRUCTURAL PROXY, never a measured completion rate. End-time /
duration conformance and holiday / session-date awareness are reported INERT (in
``not_assessed``) with plain reasons: the schedule exposes no honest per-section
contact category (only a binary LEC/LAB token, stripped on the import path) or
contact hours, and no machine-readable academic calendar is ingested.
"""
from buildability import required_set
from sources import mapping, timeblocks

PRIME_START = 540       # 9:00 AM (minutes since midnight)
PRIME_END = 780         # 1:00 PM (exclusive upper bound on a section's START minute)
AFTERNOON_END = 1020    # 5:00 PM
MAX_LOCKED_CANDIDATES = 200

LABEL = ("Grid-conformance & morning-compression — a structural time-block PROXY, "
         "not a measured completion rate.")

_END_TIME_REASON = (
    "no honest per-section contact category (the schedule exposes only a binary "
    "LEC/LAB token, stripped on the import path) and no contact-hours / meetings-"
    "per-week, so meeting duration cannot be derived from units — it would false-"
    "flag labs, clinicals and activities")
_CALENDAR_REASON = (
    "no machine-readable academic/holiday calendar is ingested; the per-section "
    "session `dates` and `woi` are now CAPTURED on the section records but remain "
    "unused — activating a holiday/session-date check still needs that calendar")


def _bucket(start_min):
    if start_min < PRIME_START:
        return "early"
    if start_min < PRIME_END:
        return "prime"
    if start_min < AFTERNOON_END:
        return "afternoon"
    return "evening"


def _timed(sections):
    """Deduped rows that have a real meeting, sorted for determinism. Async/TBA/
    unparseable rows (empty meeting) are EXCLUDED (fail open)."""
    seen, out = set(), []
    for r in sections or []:
        days, times = r.get("days", ""), r.get("times", "")
        meeting = timeblocks.parse_meeting(days, times)
        if not meeting:
            continue
        cid = mapping._norm(r.get("course", ""))
        key = (cid, days, times, r.get("term"))
        if key in seen:
            continue
        seen.add(key)
        start = min(b[1] for b in meeting)
        out.append({"course": cid, "term": r.get("term"), "days": days,
                    "times": times, "meeting": meeting, "start": start})
    out.sort(key=lambda s: (s["course"], str(s["term"]), s["days"], s["times"]))
    return out


def _by_course(timed):
    by = {}
    for s in timed:
        by.setdefault(s["course"], []).append(s["meeting"])
    return by


def _async_courses(sections):
    """Courses with at least one async/TBA section (a non-morning escape valve)."""
    out = set()
    for r in sections or []:
        if not timeblocks.parse_meeting(r.get("days", ""), r.get("times", "")):
            cid = mapping._norm(r.get("course", ""))
            if cid:
                out.add(cid)
    return out


def conformance(sections, *, top=20, grid=None):
    """On-grid START-time rate over deduped timed sections. on_grid fails OPEN, so
    sections whose term length has NO loaded grid are counted as ``skipped`` (not
    evaluated, never flagged), keeping the rate honest."""
    timed = _timed(sections)
    grid = timeblocks.load_grid() if grid is None else grid
    evaluated = on_grid = skipped = 0
    off = []
    for s in timed:
        starts = grid.get(timeblocks.term_length(s["term"]))
        if not starts:
            skipped += 1
            continue
        evaluated += 1
        if timeblocks.on_grid(s["term"], s["meeting"], grid=grid):
            on_grid += 1
        else:
            off.append({"course": s["course"], "term": s["term"],
                        "days": s["days"], "times": s["times"]})
    off.sort(key=lambda o: (o["course"], str(o["term"]), o["days"], o["times"]))
    return {"evaluated": evaluated, "on_grid": on_grid,
            "off_grid": evaluated - on_grid,
            "on_grid_rate": (round(on_grid / evaluated, 3) if evaluated else None),
            "off_grid_sample": off[:top], "off_grid_truncated": max(0, len(off) - top),
            "skipped": skipped}


def morning_locked_courses(sections):
    """Courses whose EVERY timed section starts in the 9 AM-1 PM window AND which
    have no async/TBA section. A course with any non-prime or async section is
    excluded (it has a non-morning option)."""
    timed = _timed(sections)
    by = {}
    for s in timed:
        by.setdefault(s["course"], []).append(s)
    escapes = _async_courses(sections)
    out = {}
    for cid, secs in by.items():
        if cid in escapes:
            continue
        if all(PRIME_START <= s["start"] < PRIME_END for s in secs):
            out[cid] = {"n_sections": len(secs)}
    return out


def compression(sections):
    timed = _timed(sections)
    buckets = {"early": 0, "prime": 0, "afternoon": 0, "evening": 0}
    for s in timed:
        buckets[_bucket(s["start"])] += 1
    total = len(timed)
    return {"buckets": buckets, "total_timed": total,
            "prime_share": (round(buckets["prime"] / total, 3) if total else None),
            "morning_locked_count": len(morning_locked_courses(sections))}


def mutual_exclusions(sections, locked, *, relevant=None, top=20):
    """Among morning-locked courses (optionally intersected with a ``relevant`` set
    of required course ids), the pairs that pairwise-hard-conflict — every section
    of one overlaps every section of the other, so a student needing both is shut
    out. Deterministically sorted and bounded."""
    by = _by_course(_timed(sections))
    candidates = sorted(locked)
    if relevant is not None:
        candidates = [c for c in candidates if c in relevant]
    candidates_truncated = max(0, len(candidates) - MAX_LOCKED_CANDIDATES)
    candidates = candidates[:MAX_LOCKED_CANDIDATES]
    pairs = []
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a, b = candidates[i], candidates[j]
            if timeblocks.pairwise_hard_conflict(by.get(a, []), by.get(b, [])):
                pairs.append({"courses": [a, b],
                              "reason": ("both meet only in the 9 AM-1 PM window; "
                                         "every section pair overlaps")})
    pairs.sort(key=lambda p: (p["courses"][0], p["courses"][1]))
    return pairs[:top], {"candidates": candidates_truncated,
                         "pairs": max(0, len(pairs) - top)}


def _relevant_courses(program, program_demand):
    rel = required_set(program) if program else set()
    if not rel and program_demand is not None:
        rel = set(getattr(program_demand, "required", {}) or {})
    return rel or None


def grid_pressure_report(sections, *, program=None, program_demand=None,
                         fetch_partial=False, top=20, grid=None):
    """Honest active/inert envelope. INERT when there are no timed sections; ACTIVE
    otherwise, carrying the conformance rate, the morning-compression distribution,
    the mutual-exclusion pairs, and an explicit ``not_assessed`` block for the
    deliberately-unbuilt end-time/duration and holiday/session-date checks."""
    if not _timed(sections):
        return {"status": "inert", "label": LABEL,
                "reason": ("no timed sections to analyze (all async/TBA or no "
                           "days/times)")}
    conf = conformance(sections, top=top, grid=grid)
    comp = compression(sections)
    locked = morning_locked_courses(sections)
    relevant = _relevant_courses(program, program_demand)
    pairs, trunc = mutual_exclusions(sections, locked, relevant=relevant, top=top)
    caveat = ("among the sections in THIS analysis; a non-morning section of either "
              "course WOULD break the conflict, but room/instructor feasibility is "
              "not verified")
    if fetch_partial:
        caveat += " (the section set may be incomplete — partial fetch)"
    return {
        "status": "active", "label": LABEL,
        "conformance": conf,
        "morning_compression": comp,
        "mutual_exclusions": pairs,
        "what_if_caveat": caveat,
        "not_assessed": {
            "end_time_duration": {"status": "inert", "reason": _END_TIME_REASON},
            "holidays_session_dates": {"status": "inert", "reason": _CALENDAR_REASON},
        },
        "truncated": {"pairs": trunc["pairs"], "candidates": trunc["candidates"],
                      "off_grid": conf["off_grid_truncated"]},
        "fetch_partial": bool(fetch_partial),
    }


if __name__ == "__main__":   # pragma: no cover - honest CLI summary
    import sys
    from sources import schedule_import
    if len(sys.argv) < 2:
        print("usage: python3 -m grid_pressure <schedule_export.xlsx>")
        raise SystemExit(2)
    records, _summary = schedule_import.load_schedule_export(sys.argv[1])
    rep = grid_pressure_report(records)
    if rep["status"] != "active":
        print(f"inert: {rep['reason']}")
        raise SystemExit(0)
    c, m = rep["conformance"], rep["morning_compression"]
    print(f"on-grid start times: {c['on_grid_rate']} "
          f"({c['on_grid']}/{c['evaluated']}; {c['skipped']} skipped)")
    print(f"time-of-day: {m['buckets']}  prime_share={m['prime_share']}  "
          f"morning_locked={m['morning_locked_count']}")
    for p in rep["mutual_exclusions"]:
        print(f"  mutually exclusive: {p['courses'][0]} <-> {p['courses'][1]}")
