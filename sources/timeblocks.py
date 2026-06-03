"""
timeblocks.py — class meeting-time parsing, overlap detection, and conflict feasibility.

Pure Python (stdlib only): no network, no JVM, no solver dependency. It operates on the
``days`` / ``times`` strings the live schedule source already returns (see
``sources/schedule.py``) — e.g. days ``"M W F"`` / ``"MW"`` / ``"T Th"`` / ``"Th"`` / ``""``
and times ``"10:35 AM - 11:25 AM"`` / ``""`` (async/TBA).

This is the shared foundation for the time-block COLLISION detector (build_live_workbook) and
the GRID-conformance check. The deterministic solver's slot-assignment lives in engine.py and
reuses the same overlap primitives conceptually, but this module stays solver-free so it is
trivially unit-testable and import-cheap.

Model: a "meeting" is a list of ``(day, start_min, end_min)`` blocks (minutes since midnight).
A section with no scheduled meeting (async / TBA / unparseable time) has an EMPTY meeting list
and therefore never conflicts with anything.
"""
from __future__ import annotations

import json
import os
import re

# Canonical weekday tokens, ordered longest-first so the tokenizer consumes the
# two-char "Su"/"Th" before the single-letter "S"/"T".
_DAY_TOKENS = ["Su", "Th", "M", "T", "W", "F", "S"]
_DAY_CANON = {"M": "M", "T": "T", "W": "W", "TH": "Th", "F": "F", "S": "S", "SU": "Su"}
_NO_MEETING = {"", "TBA", "ARR", "ARRANGED", "N/A", "ONLINE", "ASYNC"}

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([AaPp][Mm])")


def parse_days(s):
    """Ordered list of canonical day tokens from a days string.

    Handles space-separated (``"M W F"``, ``"T Th"``) and concatenated (``"MW"``, ``"TTh"``)
    forms. Empty / TBA / ARR -> ``[]`` (no scheduled day). Duplicates are dropped, order kept.
    """
    if not s:
        return []
    text = str(s).strip()
    if not text or text.upper() in _NO_MEETING:
        return []
    compact = re.sub(r"[\s,]+", "", text)
    days, i = [], 0
    while i < len(compact):
        for tok in _DAY_TOKENS:
            if compact[i:i + len(tok)].upper() == tok.upper():
                days.append(_DAY_CANON[tok.upper()])
                i += len(tok)
                break
        else:
            i += 1  # skip an unrecognized character rather than spin forever
    seen, out = set(), []
    for d in days:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _to_min(h, m, ap):
    h = int(h) % 12
    if ap.upper() == "PM":
        h += 12
    return h * 60 + int(m)


def parse_times(s):
    """Return ``(start_min, end_min)`` from ``"10:35 AM - 11:25 AM"``, else ``None``.

    Empty / TBA / ARR / unparseable / non-positive span -> ``None`` (treated as no meeting,
    so it never conflicts).
    """
    if not s:
        return None
    found = _TIME_RE.findall(str(s))
    if len(found) < 2:
        return None
    start, end = _to_min(*found[0]), _to_min(*found[1])
    return (start, end) if end > start else None


def parse_meeting(days, times):
    """List of ``(day, start_min, end_min)`` blocks. Empty list = no scheduled meeting."""
    span = parse_times(times)
    if span is None:
        return []
    return [(d, span[0], span[1]) for d in parse_days(days)]


def _blocks_overlap(a, b):
    # (day, start, end) tuples: same day and time intervals intersect.
    return a[0] == b[0] and a[1] < b[2] and b[1] < a[2]


def meetings_overlap(m1, m2):
    """True if any block of meeting ``m1`` overlaps any block of ``m2`` (same day + time)."""
    return any(_blocks_overlap(a, b) for a in m1 for b in m2)


def sections_conflict(sec_a, sec_b):
    """``sec_a`` / ``sec_b`` are meeting lists (from ``parse_meeting``). True if they cannot
    coexist in one student's week."""
    return meetings_overlap(sec_a, sec_b)


def pairwise_hard_conflict(sections_a, sections_b):
    """True iff EVERY section of A conflicts with EVERY section of B — i.e. a student literally
    cannot take both courses as scheduled.

    Both courses must have at least one section. A section with no meeting (async/TBA) is always
    a conflict-free choice, so if either course has any no-meeting section the answer is False.
    """
    if not sections_a or not sections_b:
        return False
    if any(len(m) == 0 for m in sections_a) or any(len(m) == 0 for m in sections_b):
        return False
    return all(sections_conflict(x, y) for x in sections_a for y in sections_b)


def feasible_selection(course_to_sections):
    """Can we pick exactly one section per course with no pairwise time overlap?

    ``course_to_sections``: ``{course_id: [meeting_list, ...]}`` — each course's candidate
    sections. Returns ``(feasible, conflict_courses)``. Pure backtracking: a term holds only a
    handful of courses, so this is instant and fully deterministic (no solver needed).

    On infeasibility, ``conflict_courses`` is a best-effort culprit set — the courses that
    pairwise-hard-conflict, or (for a joint/3-way infeasibility with no single hard pair) all
    courses involved.
    """
    courses = [c for c, secs in course_to_sections.items() if secs]
    chosen = {}

    def bt(i):
        if i == len(courses):
            return True
        c = courses[i]
        for sec in course_to_sections[c]:
            if all(not sections_conflict(sec, chosen[o]) for o in chosen):
                chosen[c] = sec
                if bt(i + 1):
                    return True
                del chosen[c]
        return False

    if bt(0):
        return True, []

    culprits = set()
    items = list(course_to_sections.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if pairwise_hard_conflict(items[i][1], items[j][1]):
                culprits.add(items[i][0])
                culprits.add(items[j][0])
    return False, (sorted(culprits) or sorted(course_to_sections))


# --- standardized time-block grid (conformance) -------------------------------
_GRID_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "time_blocks", "lamc_blocks.json")
_GRID_CACHE = None


def _clock_to_min(s):
    """'7:15 AM' -> minutes since midnight, else None."""
    m = _TIME_RE.search(str(s))
    return _to_min(*m.groups()) if m else None


def term_length(term_code):
    """Map a LACCD term code to a grid key. Last digit: 8/2 -> '16-week' (Fall/
    Spring), 1 -> 'winter', 6 -> 'summer'. Unknown -> '16-week' (dominant default).
    """
    d = str(term_code).strip()[-1:] if term_code is not None else ""
    return {"8": "16-week", "2": "16-week", "1": "winter", "6": "summer"}.get(d, "16-week")


def load_grid(path=None):
    """Load standardized START times (as minute-sets) per term length. Cached.

    Returns {} on any read/parse failure so callers fail OPEN (never flag when we
    cannot load the grid)."""
    global _GRID_CACHE
    if _GRID_CACHE is not None and path is None:
        return _GRID_CACHE
    try:
        with open(path or _GRID_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        return {}
    grid = {}
    for key, val in raw.items():
        if isinstance(val, dict) and "starts" in val:
            grid[key] = {m for m in (_clock_to_min(s) for s in val["starts"]) if m is not None}
    if path is None:
        _GRID_CACHE = grid
    return grid


def on_grid(term_code, meeting, *, tolerance_min=5, grid=None):
    """True if a section's start time is on the standard grid for its term length
    (within ``tolerance_min``). No meeting (async/TBA) is vacuously on-grid. Fails
    OPEN: returns True when no grid exists for the term length (never a false flag).
    """
    if not meeting:
        return True
    starts = (grid if grid is not None else load_grid()).get(term_length(term_code))
    if not starts:
        return True
    sec_start = min(b[1] for b in meeting)
    return any(abs(sec_start - s) <= tolerance_min for s in starts)
