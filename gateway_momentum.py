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

from buildability import _in_horizon, offered_by_course, single_section_required
# Gateway identification + the first-year window are SHARED with F9
# (corequisite_availability); they live in gateway_common so the two detectors
# keep one honest definition rather than drifting.
from gateway_common import (FIRST_YEAR_TERMS, _GATEWAYS, _first_year_terms,
                            _identify_gateway)

GATEWAY_MOMENTUM_LABEL = (
    "First-Year Gateway-Momentum: an OFFERING PROXY for whether a program's "
    "transfer-level English (GE Area 1A) and Math (GE Area 2) gateway courses can "
    "be SCHEDULED in the first year of the analyzed schedule. NOT a measured "
    "completion rate — no student-level outcome exists in any LACCD source. "
    "Subject-based identification (the major_subject fallback) is discipline-level "
    "(ENGL / MATH), not verified transfer-level. Placement / prerequisite blocking "
    "and actual student momentum are NOT assessed."
)

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
