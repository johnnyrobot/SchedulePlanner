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
    "no machine-readable academic/holiday calendar is ingested; the live API's "
    "session `dates` are dropped at parse time and `woi` is unused")


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
