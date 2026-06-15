"""gateway_common.py — shared transfer-level gateway-identification primitives.

The "gateway" courses of community-college completion research are transfer-level
English Composition (GE Area 1A) and Math / Quantitative Reasoning (Area 2). Two
detectors reason about them:

  - F8 ``gateway_momentum`` — can the gateway be SCHEDULED in the first year?
  - F9 ``corequisite_availability`` — is the gateway's AB1705 corequisite SUPPORT
    co-offered in the same first-year term?

Both need the SAME identification (which course IS the gateway, and how sure are
we it is transfer-level) and the SAME first-year window. Those primitives live
here so the two detectors share one honest definition rather than drifting.

Identification (most precise first; recorded in each gateway's ``via`` field):
  1. ``ge_area_<code>`` — the program's own ``ge_requirements`` recommended_course
     for the English-Composition (1A) / Quantitative-Reasoning (2) area.
     Transfer-level by definition of the GE area (``transfer_level: area-defined``).
  2. ``major_subject`` — a REQUIRED major course whose subject is ENGL / MATH. We
     CANNOT verify it is transfer-level, so we prefer the HIGHEST course number
     (the earliest-semester course is typically the below-transfer prereq, which
     must NOT be reported as the gateway) and mark it ``transfer_level: unverified``.

Pure stdlib + ``sources.mapping`` (no network, no solver); deterministic.
"""
from __future__ import annotations

import re

from sources import mapping

# Cal-GETC / IGETC area codes for the two gateways. English Composition is Area
# 1A (1B Critical Thinking / 1C Oral are NOT the gateway); Mathematical Concepts
# & Quantitative Reasoning is Area 2 (subarea 2A in some patterns).
_ENGLISH_AREAS = frozenset({"1A", "1"})
_MATH_AREAS = frozenset({"2", "2A"})
# Accepted subject prefixes for the major-course fallback (both the short LACCD
# code and the long spelling — course ids carry the short form, e.g. "ENGL 101").
_ENGLISH_SUBJECTS = frozenset({"ENGL", "ENGLISH"})
_MATH_SUBJECTS = frozenset({"MATH", "MATHEMATICS"})

# Earliest N distinct terms = the "year 1" window proxy.
FIRST_YEAR_TERMS = 2

_GATEWAYS = (
    ("english", _ENGLISH_AREAS, _ENGLISH_SUBJECTS),
    ("math", _MATH_AREAS, _MATH_SUBJECTS),
)


def _subject(course_id):
    """Subject prefix token of a normalized course id ('ENGL 101' -> 'ENGL')."""
    parts = mapping._norm(course_id or "").split()
    return parts[0] if parts else ""


def _course_number(course_id):
    """First integer in a course id ('MATH 245H' -> 245), else 0. A coarse
    transfer-level correlate used only to break major_subject-fallback ties AWAY
    from below-transfer/remedial courses (which carry lower numbers)."""
    m = re.search(r"\d+", mapping._norm(course_id or ""))
    return int(m.group()) if m else 0


def _first_year_terms(sections, horizon_terms):
    """The caller's window, else the earliest FIRST_YEAR_TERMS distinct terms."""
    if horizon_terms:
        return list(horizon_terms)
    terms = sorted({s.get("term") for s in sections if s.get("term") not in (None, "")},
                   key=lambda t: str(t))
    return terms[:FIRST_YEAR_TERMS]


def _identify_gateway(program, areas, subjects):
    """Find a program's gateway course for one discipline.

    Strategy 1 (ge_area_<code>): a ge_requirements entry in ``areas`` whose
    recommended_course is named. Strategy 2 (major_subject): the highest-numbered
    required major course whose subject is in ``subjects``. Returns a dict with
    identified / course (normalized) / display (as written) / via /
    transfer_level / recommended_semester.
    """
    program = program or {}
    for req in (program.get("ge_requirements") or []):
        if str(req.get("area", "")).upper() in areas:
            rec = str(req.get("recommended_course", "") or "").strip()
            if rec:
                # The GE area itself defines the transfer-level requirement, so a
                # course named for it IS the transfer-level gateway (verified).
                return {"identified": True, "course": mapping._norm(rec),
                        "display": rec, "via": f"ge_area_{str(req['area']).upper()}",
                        "transfer_level": "area-defined",
                        "recommended_semester": req.get("recommended_semester")}
    # Fallback: a required ENGL/MATH major course. We CANNOT verify it is
    # transfer-level, so we (a) prefer the HIGHEST course number rather than the
    # earliest semester (the earliest is typically the below-transfer prereq, the
    # exact course we must NOT report as the gateway), and (b) mark it unverified.
    best = None
    for c in (program.get("courses") or []):
        cid = str(c.get("course_id", "") or "").strip()
        if not cid or _subject(cid) not in subjects:
            continue
        sem = c.get("recommended_semester")
        sem_key = sem if isinstance(sem, int) else 10**6  # unspecified sorts last
        # Sort key: highest number first, then earliest semester, then id (stable).
        key = (-_course_number(cid), sem_key, mapping._norm(cid))
        if best is None or key < best[0]:
            best = (key, cid, sem)
    if best is not None:
        return {"identified": True, "course": mapping._norm(best[1]),
                "display": best[1], "via": "major_subject",
                "transfer_level": "unverified",
                "recommended_semester": best[2]}
    return {"identified": False, "via": "none"}
