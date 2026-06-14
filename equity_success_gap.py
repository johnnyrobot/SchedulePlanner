"""equity_success_gap.py — E13: equity-disaggregated course-success GAP detector.

Consumes the DISAGGREGATED (by demographic subgroup) course retention/success data
loaded offline by ``sources.course_success.load_course_success_disaggregated`` and
surfaces, per offered course, the MEASURED gap between subgroups: a subgroup's
success rate minus a reference rate (the overall / "All" row when present, else the
highest-performing subgroup). Small cells (count < 10) are already SUPPRESSED by the
adapter per the published Cal-PASS rule — this detector never un-suppresses them,
only counts how many were hidden.

Honesty (the #17 no-student-data ceiling + Doctrine 3 evidence honesty): a MEASURED,
AGGREGATE course-success GAP — explicitly NOT a completion gap, NOT a student-level
record, NOT this schedule's outcome (it is historical), and a gap is a measured
DIFFERENCE, never a causal claim about what produced it. The disaggregated subgroup
figures circulated in the roadmap (Kilgore / BCTC) are NOT in the vetted evidence
list, so this detector cites NO external figure and is NOT wired into the F7
evidence trust root — it reports only the numbers in the supplied file.
FIXTURE-VALIDATED only (the real disaggregated-export shape is assumed, not
downloaded).

Privacy: operates only on already-suppressed aggregate subgroup rates (no cell with
count < 10 carries a rate or a count). Pure stdlib + ``sources.mapping``;
deterministic; JSON-serializable; runs OUTSIDE ``engine.run`` (advisory only).
"""
from __future__ import annotations

from sources import mapping

EQUITY_SUCCESS_GAP_LABEL = (
    "Equity Course-Success Gap: a MEASURED, AGGREGATE gap between demographic "
    "subgroups in COURSE retention/success (from a disaggregated CCCCO Data Mart "
    "export; small cells with fewer than 10 are SUPPRESSED per the Cal-PASS rule). "
    "It is NOT a completion gap, NOT student-level, and NOT this schedule's "
    "outcome (it is historical); a gap is a measured DIFFERENCE, not a causal claim "
    "about what produced it. Cites NO external figure — only the supplied file. "
    "FIXTURE-VALIDATED only."
)

_REFERENCE_NAMES = frozenset({"ALL", "OVERALL", "TOTAL", "ALL STUDENTS"})


def _reference(subgroups):
    """Pick the reference (subgroup, rate, basis) from a course's NON-suppressed
    subgroups: the overall/All row if present, else the highest-rate subgroup.
    Returns (None, None, None) when no non-suppressed subgroup has a rate."""
    live = {s: c["success_rate"] for s, c in subgroups.items()
            if not c.get("suppressed") and c.get("success_rate") is not None}
    if not live:
        return None, None, None
    for s, rate in sorted(live.items()):
        if mapping._norm(s) in _REFERENCE_NAMES:
            return s, rate, "all_row"
    # No explicit All row: the highest-performing subgroup is the reference.
    best = max(sorted(live), key=lambda s: live[s])
    return best, live[best], "highest_subgroup"


def equity_success_gap_report(sections, disagg_map, *, granularity=None,
                              suppression_min=10):
    """Equity course-success gap block for the offered courses (active/inert).

    Inert when no disaggregated export was supplied. Active otherwise, listing each
    offered course (sorted by largest below-reference gap first) that has EITHER a
    below-reference subgroup gap OR at least one suppressed subgroup (so the
    suppression itself is surfaced, never hidden).
    """
    label = EQUITY_SUCCESS_GAP_LABEL
    if not disagg_map:
        return {"status": "inert", "label": label,
                "reason": ("no disaggregated course-success export supplied (the live "
                           "LACCD APIs expose no disaggregated outcome data)"),
                "remedy": ("supply a disaggregated CCCCO Data Mart Credit Course "
                           "Retention/Success export (by subgroup) on the offline path")}

    offered = sorted({mapping._norm(s.get("course", "")) for s in sections
                      if s.get("course")})
    courses = []
    for cid in offered:
        subgroups = disagg_map.get(cid)
        if not subgroups:
            continue
        ref_sub, ref_rate, basis = _reference(subgroups)
        suppressed = sum(1 for c in subgroups.values() if c.get("suppressed"))
        below = []
        if ref_rate is not None:
            for sub in sorted(subgroups):
                cell = subgroups[sub]
                if cell.get("suppressed") or cell.get("success_rate") is None:
                    continue
                if sub == ref_sub:
                    continue
                gap = round(cell["success_rate"] - ref_rate, 4)
                if gap < 0:
                    below.append({"subgroup": sub,
                                  "success_rate": cell["success_rate"],
                                  "gap": gap})
        if not below and not suppressed:
            continue   # nothing to surface for this course
        courses.append({
            "course": cid,
            "reference_subgroup": ref_sub,
            "reference_rate": ref_rate,
            "reference_basis": basis,
            "below_reference": below,
            "suppressed_subgroups": suppressed,
        })

    # Largest (most negative) below-reference gap first; suppressed-only courses
    # (no gap) sort last via the 0.0 sentinel; ties broken by course id.
    courses.sort(key=lambda c: (min((g["gap"] for g in c["below_reference"]),
                                    default=0.0), c["course"]))

    return {
        "status": "active", "label": label,
        "granularity": granularity or "unknown",
        "suppression_min": suppression_min,
        "courses": courses,
        "courses_with_gap": sum(1 for c in courses if c["below_reference"]),
        "not_assessed": [
            {"check": "student_completion", "status": "inert",
             "reason": ("an aggregate course-success gap is not a program-completion "
                        "gap and not a student-level outcome")},
            {"check": "causation", "status": "inert",
             "reason": ("a gap is a measured DIFFERENCE between subgroups; it does not "
                        "establish what caused the difference, nor that the schedule "
                        "produced or can close it")},
            {"check": "suppressed_subgroups", "status": "inert",
             "reason": (f"subgroups with fewer than {suppression_min} were suppressed "
                        "(Cal-PASS rule) and are NOT in the gap; the picture is "
                        "deliberately incomplete to protect small cells")},
            {"check": "granularity", "status": "inert",
             "reason": ("the rate is at the export's granularity (course or TOP "
                        "discipline); a discipline-level gap is not course-specific")},
        ],
    }
