"""demand_success.py — E9: demand-vs-success escalation detector.

Crosses the MEASURED, AGGREGATE course retention/success data (loaded offline from
a CCCCO Data Mart export by ``sources.course_success``) with the supply-constraint
signals (F2 cross-program bottlenecks / F5 demand-vs-supply) to ESCALATE a course
that is BOTH hard-to-get-into AND historically lower-success — the strongest
"add-a-section / intervene here" double signal the public data can support.

Honesty (the #17 no-student-data ceiling): the rate is a MEASURED aggregate COURSE
outcome (retention / success), at the granularity of the supplied export (course or
TOP discipline — reported in ``granularity``). It is NOT a program-completion label,
NOT a student-level record, and NOT this schedule's outcome (it is historical). A
low success rate next to a supply constraint is a co-occurrence, NOT a causal claim.
Unmatched offered courses are surfaced (``offered_without_outcome``), never silently
dropped. Inert with remedy when no success export is supplied.

Pure stdlib + ``sources.mapping`` (no network, no solver); deterministic; JSON-
serializable; runs OUTSIDE ``engine.run`` (advisory only).
"""
from __future__ import annotations

from sources import mapping

DEMAND_SUCCESS_LABEL = (
    "Course Success Signal: a MEASURED, AGGREGATE retention/success outcome from a "
    "CCCCO Data Mart export (public, no student rows) — NOT a program-completion "
    "label, NOT a student-level record, and NOT this schedule's outcome (it is "
    "historical, at the export's granularity). Courses that are BOTH "
    "supply-constrained AND historically lower-success are escalated; a low rate "
    "next to a supply constraint is a CO-OCCURRENCE, not a causal claim. "
    "FIXTURE-VALIDATED only — the real export shape is assumed, not downloaded."
)


def demand_success_report(sections, success_map, *, supply_constrained=None,
                          granularity=None):
    """Cross the measured success data with the supply signals (active/inert).

    Inert when no success export was supplied (the common path — the live LACCD
    APIs expose no success data). Active otherwise, listing each offered course
    that HAS measured data, the count of offered courses WITHOUT data (honest join
    coverage), and the escalated subset (supply-constrained + has a rate, lowest
    success first).
    """
    label = DEMAND_SUCCESS_LABEL
    if not success_map:
        return {"status": "inert", "label": label,
                "reason": ("no course-success export supplied (the live LACCD APIs "
                           "expose no retention/success data)"),
                "remedy": ("supply a CCCCO Data Mart Credit Course Retention/Success "
                           "export (public, aggregate) on the offline import path")}

    offered = sorted({mapping._norm(s.get("course", "")) for s in sections
                      if s.get("course")})
    supply = {mapping._norm(c) for c in (supply_constrained or [])}

    with_outcome = []
    offered_without = 0
    for cid in offered:
        rec = success_map.get(cid)
        if rec is None:
            offered_without += 1
            continue
        with_outcome.append({
            "course": cid,
            "success_rate": rec.get("success_rate"),
            "retention_rate": rec.get("retention_rate"),
            "enrollment": rec.get("enrollment"),
            "supply_constrained": cid in supply,
        })

    escalated = sorted(
        (r for r in with_outcome
         if r["supply_constrained"] and r["success_rate"] is not None),
        key=lambda r: (r["success_rate"], r["course"]))

    return {
        "status": "active", "label": label,
        "granularity": granularity or "unknown",
        "with_outcome": with_outcome,
        "escalated": escalated,
        "matched": len(with_outcome),
        "offered_without_outcome": offered_without,
        "not_assessed": [
            {"check": "student_completion", "status": "inert",
             "reason": ("an aggregate COURSE retention/success rate is not a "
                        "program-completion rate and not a student-level outcome")},
            {"check": "causation", "status": "inert",
             "reason": ("a low success rate next to a supply constraint is a "
                        "co-occurrence; this does not establish that the constraint "
                        "causes the lower success")},
            {"check": "granularity", "status": "inert",
             "reason": ("the rate is at the export's granularity (course or TOP "
                        "discipline); a discipline-level rate is not course-specific")},
        ],
    }
