"""gateway_momentum.py — F8: First-Year Gateway-Momentum detector (offering proxy).

Completing the transfer-level English (GE Area 1A) and Math (GE Area 2) "gateway"
courses in the FIRST YEAR is the single strongest momentum signal in community-
college completion research. This detector reports, as an honest OFFERING PROXY,
whether a program's gateway English + Math can be SCHEDULED in the first year of
the analyzed schedule. It does NOT measure whether any student completed them —
no student-level outcome exists in any LACCD source.

Identification (most precise first; recorded in each gateway's ``via`` field):
  1. ``ge_area_1A`` / ``ge_area_2`` — the program's own ``ge_requirements``
     ``recommended_course`` for the English-Composition (1A) / Quantitative-
     Reasoning (2) area. Transfer-level by definition of the GE area.
  2. ``major_subject`` — a REQUIRED major course whose canonical subject is
     ENGLISH / MATH (earliest ``recommended_semester`` wins). Discipline-level,
     NOT verified transfer-level — labeled as such.
  When neither yields a course for an area, that gateway is ``identified: False``
  (reported, never guessed). When BOTH areas fail, the whole detector is inert.

First-year window: the earliest two distinct terms in the analyzed schedule (or
the caller's ``horizon_terms``). A PROXY for "year 1", not a student's actual
enrollment timeline.

Honesty (``GATEWAY_MOMENTUM_LABEL`` travels with every report): an OFFERING proxy,
not a measured completion rate; subject identification is discipline-level, not
verified transfer-level; placement / prerequisite blocking and actual student
momentum are NOT assessed (no such data exists).

Design: pure stdlib + ``buildability`` + ``sources`` (no network, no solver);
deterministic; JSON-serializable; runs OUTSIDE ``engine.run`` (advisory only).
"""
from __future__ import annotations

import re

from buildability import _in_horizon, offered_by_course, single_section_required
from sources import mapping

GATEWAY_MOMENTUM_LABEL = (
    "First-Year Gateway-Momentum: an OFFERING PROXY for whether a program's "
    "transfer-level English (GE Area 1A) and Math (GE Area 2) gateway courses can "
    "be SCHEDULED in the first year of the analyzed schedule. NOT a measured "
    "completion rate — no student-level outcome exists in any LACCD source. "
    "Subject-based identification (the major_subject fallback) is discipline-level "
    "(ENGL / MATH), not verified transfer-level. Placement / prerequisite blocking "
    "and actual student momentum are NOT assessed."
)

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
    recommended_course is named. Strategy 2 (major_subject): the earliest-
    recommended required major course whose canonical subject is in ``subjects``.
    Returns a dict with identified / course (normalized) / display (as written) /
    via / recommended_semester.
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


def _assess(gw, offered, year1):
    """Assess one identified gateway's first-year schedulability + obstructions."""
    if not gw["identified"]:
        return {"identified": False, "via": "none",
                "reason": "no transfer-level gateway course identified for this area"}
    cid = gw["course"]
    horizon = set(year1)
    all_secs = offered.get(cid, [])
    in_window = _in_horizon(all_secs, horizon)
    obstructions = []
    if not all_secs:
        obstructions.append("not offered in the analyzed schedule")
    elif not in_window:
        obstructions.append("offered only after the first-year window")
    elif single_section_required({cid}, offered, list(year1)):
        obstructions.append("only a single section in some first-year term")
    return {"identified": True, "course": gw["display"], "via": gw["via"],
            "transfer_level": gw.get("transfer_level", "unverified"),
            "recommended_semester": gw["recommended_semester"],
            "schedulable_year1": bool(in_window),
            "sections_in_window": len(in_window),
            "obstructions": obstructions}


def gateway_momentum_report(sections, *, program=None, horizon_terms=None):
    """First-year gateway-momentum block for one program (active/inert envelope).

    Inert when there are no offered sections, or when neither an English nor a
    Math gateway course can be identified for the program. Active otherwise, with
    a per-gateway assessment, the first-year term window, and an explicit
    not_assessed list for the checks no LACCD data supports.
    """
    label = GATEWAY_MOMENTUM_LABEL
    if not sections:
        return {"status": "inert", "label": label,
                "reason": "no offered sections to assess first-year gateway momentum",
                "remedy": "fetch or import a schedule that carries offered sections"}

    offered = offered_by_course(sections)
    gateways = {name: _identify_gateway(program, areas, subjects)
                for name, areas, subjects in _GATEWAYS}
    if not any(g["identified"] for g in gateways.values()):
        return {
            "status": "inert", "label": label,
            "reason": ("neither a transfer-level English (GE Area 1A) nor Math "
                       "(GE Area 2) gateway course could be identified for this program"),
            "remedy": ("supply a program whose ge_requirements name a recommended "
                       "English (Area 1A) / Math (Area 2) course, or a required "
                       "ENGL / MATH major course"),
        }

    year1 = _first_year_terms(sections, horizon_terms)
    english = _assess(gateways["english"], offered, year1)
    math = _assess(gateways["math"], offered, year1)
    block = {
        "status": "active", "label": label,
        "first_year_terms": [str(t) for t in year1],
        "english": english,
        "math": math,
        "both_gateways_year1": bool(english.get("schedulable_year1")
                                    and math.get("schedulable_year1")),
        "not_assessed": [
            {"check": "placement_prerequisite_blocking", "status": "inert",
             "reason": ("no placement or prerequisite-completion data exists in any "
                        "LACCD source, so a gateway may be unreachable in year 1 even "
                        "when scheduled")},
            {"check": "seat_availability_and_time_conflict", "status": "inert",
             "reason": ("schedulable_year1 means a section EXISTS in the window — it "
                        "does NOT check that a seat is open (live seat counts are "
                        "unreliable) or that English and Math have a conflict-free "
                        "combination in the same term")},
            {"check": "student_completion", "status": "inert",
             "reason": ("no student-level outcome exists; this is an OFFERING proxy, "
                        "not a measured gateway-completion rate")},
        ],
    }
    # The window is the earliest FIRST_YEAR_TERMS distinct terms; on a single-term
    # fetch it collapses to one term. Surface that so a reader does not over-read
    # "first year" from what may be one published term.
    if len(year1) < FIRST_YEAR_TERMS:
        block["window_note"] = (
            f"the analyzed schedule has only {len(year1)} term(s), so the "
            f"first-year window is narrower than {FIRST_YEAR_TERMS} terms; "
            "schedulable_year1 reflects only that window")
    return block
