"""Transfer-pattern GE: static pattern-rule loading + the GE requirement resolver.

PURE module (no network). ``load_pattern`` reads a reviewed pattern-rule file
from data/ge_patterns/; ``resolve`` (Task 4) turns pattern rules + ASSIST area
courses + offered sections + Program Mapper data into GE requirement rows plus an
honest coverage report. Per-area COUNTS/units/lab rules are POLICY shipped as
reviewed data — never invented here.
"""
from __future__ import annotations

import json
import os

from .elumen_client import normalize_course_code

_PATTERN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "ge_patterns")

# Transfer-goal value -> the shipped pattern filename (current effective year).
_PATTERN_FILES = {
    "cal-getc": "cal-getc-2025-2026.json",
    "igetc": "igetc-2026-2027.json",
    "csu-ge": "csu-ge-2026-2027.json",
}


class PatternError(ValueError):
    """Raised when a GE pattern file is unknown or malformed."""


def load_pattern(transfer_goal, *, path=None):
    """Load a reviewed pattern-rule dict for a transfer goal.

    ``path`` overrides the lookup (tests). Raises PatternError for an unknown
    goal or a file missing the required fields, so a bad pattern fails loudly.
    """
    if path is None:
        key = str(transfer_goal).strip().lower()
        fname = _PATTERN_FILES.get(key)
        if not fname:
            raise PatternError(
                f"unknown transfer goal {transfer_goal!r}; known goals are "
                f"{sorted(_PATTERN_FILES)}.")
        path = os.path.join(_PATTERN_DIR, fname)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise PatternError(f"cannot read GE pattern file {path!r}: {exc}") from exc
    if not isinstance(data, dict) or not data.get("areas"):
        raise PatternError(f"GE pattern file {path!r} has no 'areas'.")
    return data


def is_reviewed(pattern):
    """True only when a human reviewer signed off (``reviewed_by`` is non-empty).

    The shipped data/ge_patterns/*.json files leave ``reviewed_by`` /
    ``reviewed_on`` blank BY DESIGN — a blank reviewer is the gate flag meaning
    the per-area COUNTS/units are UNVERIFIED placeholders, not authoritative
    policy. Consumers MUST surface a draft warning while this is False so an
    unreviewed pattern is never mistaken for a signed-off transfer plan; the
    warning self-clears the moment a qualified reviewer fills the field in.
    """
    return bool(str((pattern or {}).get("reviewed_by", "")).strip())


def _canon(course_id):
    """Canonical join key (leading zeros stripped), matching the eLumen join."""
    return normalize_course_code(course_id)


def _flatten_areas(pattern):
    """Yield (area_code, title, count, units_min, attributes) per schedulable area.

    A pattern area with subareas is expanded into one requirement per subarea
    (count 1 each) plus, if the parent count exceeds the subarea minimums, a
    parent 'any' requirement over the union of subarea codes for the remainder.
    Areas without subareas yield a single requirement.
    """
    for area in pattern.get("areas", []):
        code = area["code"]
        title = area.get("title", "")
        attrs = area.get("attributes", {}) or {}
        subareas = area.get("subareas")
        if not subareas:
            yield {"area": code, "title": title, "count": int(area.get("count", 1)),
                   "units_min": float(area.get("units_min", 3)), "attributes": attrs,
                   "member_codes": [code]}
            continue
        assigned = 0
        for sub in subareas:
            yield {"area": sub["code"], "title": title, "count": int(sub.get("min", 1)),
                   "units_min": float(area.get("units_min", 3)) / max(1, int(area.get("count", 1))),
                   "attributes": attrs, "member_codes": [sub["code"]]}
            assigned += int(sub.get("min", 1))
        remainder = int(area.get("count", 1)) - assigned
        if remainder > 0:
            yield {"area": code, "title": title, "count": remainder,
                   "units_min": float(area.get("units_min", 3)) / max(1, int(area.get("count", 1))),
                   "attributes": attrs,
                   "member_codes": [s["code"] for s in subareas],
                   "reserve_only": True}


def _pattern_codes(pattern):
    """Every area + subarea code the pattern names (the codes resolve() owns)."""
    codes = set()
    for area in pattern.get("areas", []):
        codes.add(area["code"])
        for sub in area.get("subareas") or []:
            codes.add(sub["code"])
    return codes


def _code_system(code):
    """Which GE coding system a code belongs to: 'numeric' (IGETC / Cal-GETC,
    e.g. '1A', '2', '5C') or 'alpha' (CSU GE-Breadth, e.g. 'A2', 'B4', 'D0')."""
    return "numeric" if str(code)[:1].isdigit() else "alpha"


def _pattern_system(pattern):
    """The pattern's single coding system, or None if mixed/empty.

    IGETC and Cal-GETC NUMBER their areas ('1A','2','3A'); CSU GE-Breadth LETTERS
    them ('A2','B4','D'). ASSIST's Cal-GETC ``listType`` bundles BOTH systems'
    codes for the same courses, so a single-system pattern must ignore codes from
    the OTHER system (see _assist_courses_by_area) instead of mis-reporting them
    as unknown areas. Returns None for a mixed/empty pattern so the caller falls
    back to no cross-system filtering (safe default)."""
    systems = {_code_system(c) for c in _pattern_codes(pattern)}
    return next(iter(systems)) if len(systems) == 1 else None


def _assist_courses_by_area(assist_areas, pattern):
    """Reconcile ASSIST's subarea-grained codes onto the pattern's area codes.

    ASSIST tags transferability at SUBAREA granularity — it emits '2A', '4B',
    '6A', never a bare '2'/'4'/'6'. A pattern that states a PARENT area would
    therefore find nothing under that bare code and mis-report ``no_assist_data``
    even though ASSIST has the courses. So a parent code absorbs every finer
    ASSIST code beneath it (``startswith``), but ONLY when the pattern declares no
    explicit subareas for that area — an area WITH subareas ('3' -> 3A/3B,
    '5' -> 5A/5B) matches exactly, so a reserve parent never swallows its own
    subareas or an unrelated lab code ('5C'). Exact matches always win.

    ASSIST's Cal-GETC ``listType`` also bundles the legacy IGETC + CSU GE-Breadth
    alias codes for the SAME courses (e.g. a math course is tagged '2' AND '2A'
    AND 'B4'). A single-system pattern IGNORES codes from the OTHER system: those
    are returned in ``cross_system`` (honestly "ignored", never matched, never
    counted as unknown), because every real course is already credited via the
    pattern's own-system code — so this is coverage-neutral, it only stops the
    bundled aliases from inflating ``unknown`` (the live Cal-GETC 27-unknown bug).
    Same-system codes that match no pattern area remain ``unknown``.

    Returns (by_area, unknown, cross_system): ``by_area`` maps each pattern code
    to its merged ASSIST course list; ``unknown`` lists same-system ASSIST codes
    that matched no pattern code; ``cross_system`` lists the other-system codes
    that were ignored.
    """
    all_codes = _pattern_codes(pattern)
    # Only subarea-free ("leaf") areas absorb ASSIST's finer codes.
    absorbing = {a["code"] for a in pattern.get("areas", []) if not a.get("subareas")}
    psys = _pattern_system(pattern)
    by_area = {code: [] for code in all_codes}
    matched, cross_system = set(), []
    for acode, info in assist_areas.items():
        # Codes from a different GE coding system than the pattern are bundled
        # aliases of the same courses — ignore them (never match, never unknown).
        if psys is not None and _code_system(acode) != psys:
            cross_system.append(acode)
            continue
        for code in all_codes:
            absorb = (code in absorbing and acode != code
                      and acode.startswith(code) and acode not in all_codes)
            if acode == code or absorb:
                by_area[code].extend(info.get("courses", []))
                matched.add(acode)
    in_system = ({a for a in assist_areas if _code_system(a) == psys}
                 if psys is not None else set(assist_areas))
    unknown = sorted(in_system - matched)
    return by_area, unknown, sorted(cross_system)


def resolve(pattern, assist_areas, offered, program, *, concrete_threshold=3):
    """Resolve GE requirements for a program under a pattern. See module docstring.

    Returns (rows, coverage). A subarea-bearing area whose required count exceeds
    its subarea minimums emits a RESERVE-ONLY "remainder" requirement (an
    additional course from that area) — it never goes concrete and is excluded
    from the disjoint candidate sweep, so it cannot drain or mis-flag subareas.
    """
    offered_by_canon = {}
    for cid in sorted(offered):  # sorted -> deterministic first-wins on canonical collisions
        offered_by_canon.setdefault(_canon(cid), cid)

    major_canon = {_canon(c["course_id"]) for c in program.get("courses", [])}
    recs = {g["area"]: g.get("recommended_course", "")
            for g in program.get("ge_requirements", [])}

    # Reconcile ASSIST's subarea-grained codes onto the pattern's area codes so a
    # parent-coded area ('2'/'6') sees ASSIST's '2A'/'6A' courses (else it would
    # mis-flag no_assist_data). by_area is keyed by PATTERN code throughout.
    by_area, unknown, cross_system = _assist_courses_by_area(assist_areas, pattern)

    shared = []
    shared_areas = {}
    for area_code in sorted(by_area):  # sorted -> deterministic shared-list order
        eligible_canon = {_canon(c) for c in by_area[area_code]}
        for c in program.get("courses", []):
            if _canon(c["course_id"]) in eligible_canon:
                shared.append({"area": area_code, "course": c["course_id"]})
                shared_areas[area_code] = shared_areas.get(area_code, 0) + 1

    requirements = sorted(_flatten_areas(pattern), key=lambda r: r["area"])

    offered_eligible = {}
    for req in requirements:
        if req.get("reserve_only"):
            offered_eligible[req["area"]] = []
            continue
        cands = []
        for member in req["member_codes"]:
            for c in by_area.get(member, []):
                ca = _canon(c)
                if ca in offered_by_canon and ca not in major_canon:
                    cands.append(offered_by_canon[ca])
        offered_eligible[req["area"]] = sorted(set(cands))

    used = set()
    for req in sorted(requirements, key=lambda r: (len(offered_eligible[r["area"]]), r["area"])):
        if req.get("reserve_only"):
            continue
        offered_eligible[req["area"]] = [c for c in offered_eligible[req["area"]] if c not in used]
        used.update(offered_eligible[req["area"]])

    rows, area_cov = [], []
    for req in requirements:
        area = req["area"]
        if req.get("reserve_only"):
            required = req["count"]
            if required <= 0:
                continue
            rows.append({"area": area, "area_title": req["title"],
                         "required_count": required, "resolution": "reserve",
                         "candidates": [], "recommended": "", "units": req["units_min"]})
            area_cov.append({"area": area, "title": req["title"], "required": required,
                             "resolution": "reserve", "eligible_count": None,
                             "offered_count": 0, "flags": []})
            continue
        flags = []
        required = max(0, req["count"] - shared_areas.get(area, 0))
        eligible_any = bool(by_area.get(area))
        offered_cands = offered_eligible[area]
        if not eligible_any:
            flags.append("no_assist_data")
        if required <= 0:
            area_cov.append({"area": area, "title": req["title"], "required": 0,
                             "resolution": "shared", "flags": flags})
            continue
        rec = recs.get(area, "")
        area_elig_canon = {_canon(c) for c in by_area.get(area, [])}
        rec_canon = _canon(rec) if rec else ""
        rec_offered_id = offered_by_canon.get(rec_canon, "")
        # A rec is usable only if ASSIST says it satisfies THIS area AND it wasn't
        # already claimed by another area's disjoint set (so we never double-place it).
        rec_usable = bool(rec) and rec_canon in area_elig_canon and rec_offered_id in offered_cands
        # Only go concrete if there are at least `required` OFFERED candidates to
        # choose from; otherwise reserve the area (an infeasible sum(taken)==required
        # would blank the whole cohort, not just this area).
        concrete = (len(offered_cands) >= required) and (
            rec_usable or 0 < len(offered_cands) <= concrete_threshold)
        if not offered_cands:
            flags.append("no_offering")
        resolution = "concrete" if concrete else "reserve"
        rows.append({
            "area": area, "area_title": req["title"], "required_count": required,
            "resolution": resolution,
            "candidates": offered_cands if resolution == "concrete" else [],
            "recommended": rec_offered_id if rec_usable else "",
            "units": req["units_min"],
        })
        area_cov.append({"area": area, "title": req["title"], "required": required,
                         "resolution": resolution,
                         "eligible_count": len(by_area.get(area, [])),
                         "offered_count": len(offered_cands), "flags": flags})

    coverage = {"areas": area_cov, "shared_with_major": shared,
                "unknown_areas": unknown, "cross_system_areas": cross_system}
    return rows, coverage
