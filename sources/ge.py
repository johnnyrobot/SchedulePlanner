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

    shared = []
    shared_areas = {}
    for area_code, info in assist_areas.items():
        eligible_canon = {_canon(c) for c in info.get("courses", [])}
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
            for c in assist_areas.get(member, {}).get("courses", []):
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
        eligible_any = bool(assist_areas.get(area, {}).get("courses"))
        offered_cands = offered_eligible[area]
        if not eligible_any:
            flags.append("no_assist_data")
        if required <= 0:
            area_cov.append({"area": area, "title": req["title"], "required": 0,
                             "resolution": "shared", "flags": flags})
            continue
        rec = recs.get(area, "")
        area_elig_canon = {_canon(c) for c in assist_areas.get(area, {}).get("courses", [])}
        rec_canon = _canon(rec) if rec else ""
        rec_offered_id = offered_by_canon.get(rec_canon, "")
        # A rec is usable only if ASSIST says it satisfies THIS area AND it wasn't
        # already claimed by another area's disjoint set (so we never double-place it).
        rec_usable = bool(rec) and rec_canon in area_elig_canon and rec_offered_id in offered_cands
        concrete = bool(offered_cands) and (
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
                         "eligible_count": len(assist_areas.get(area, {}).get("courses", [])),
                         "offered_count": len(offered_cands), "flags": flags})

    unknown = sorted(set(assist_areas) - {a["code"] for a in pattern.get("areas", [])}
                     - {m for a in pattern.get("areas", []) for m in
                        [s["code"] for s in a.get("subareas", []) or []]})
    coverage = {"areas": area_cov, "shared_with_major": shared, "unknown_areas": unknown}
    return rows, coverage
