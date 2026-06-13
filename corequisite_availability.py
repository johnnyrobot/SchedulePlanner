"""corequisite_availability.py — F9: AB1705 corequisite co-availability (proxy).

AB1705 requires California community colleges to provide access to academic
support — such as a COREQUISITE support section — for transfer-level English and
math. This detector reports, as an honest co-OFFERING STRUCTURE proxy, whether a
transfer-level gateway course's catalog corequisite is scheduled in the SAME
first-year term as the gateway, so a student could co-enroll. It does NOT measure
whether any student completed the sequence — no student-level outcome exists in
any LACCD source.

Gateway identification is SHARED with F8 (``gateway_common``): the transfer-level
English (GE Area 1A) / Math (Area 2) course, by GE area (verified) or an
ENGL/MATH major course (subject heuristic, unverified). Corequisite LINKAGE comes
from the eLumen catalog requisites (``itemType=Co-Requisite`` leaves, captured by
``elumen_client.corequisites_of``) — catalog structure, NOT this term's
registration rules. Corequisites are excluded from the default prereq fetch, so
this detector is INERT unless a ``coreq_map`` is supplied (run ``--elumen-live``).

Honesty (``COREQUISITE_AVAILABILITY_LABEL`` travels with every report): a
co-OFFERING proxy, not a measured completion/throughput outcome; per AB1705
evidence DIRECT PLACEMENT was the dominant lever and corequisite is one supported
form — co-offering availability does NOT imply the support is required,
sufficient, or causal; placement/prerequisite blocking, seat availability, and a
conflict-free same-term combination are NOT assessed.

Design: pure stdlib + ``buildability`` + ``gateway_common`` + ``sources.mapping``
(no network, no solver); deterministic; JSON-serializable; runs OUTSIDE
``engine.run`` (advisory only).
"""
from __future__ import annotations

from buildability import offered_by_course
from gateway_common import _GATEWAYS, _first_year_terms, _identify_gateway, \
    FIRST_YEAR_TERMS
from sources.elumen_client import normalize_course_code

COREQUISITE_AVAILABILITY_LABEL = (
    "Corequisite Co-Availability (AB1705): a co-OFFERING STRUCTURE proxy for "
    "whether the catalog corequisite SUPPORT course of a transfer-level English "
    "(GE Area 1A) / Math (Area 2) gateway is scheduled in the SAME first-year "
    "term as the gateway. Corequisite linkage is read from the eLumen catalog "
    "requisites (itemType=Co-Requisite) — catalog structure, NOT this term's "
    "registration rules, and NOT a measured completion rate. Per AB1705 evidence, "
    "DIRECT PLACEMENT was the dominant lever and corequisite is one supported "
    "form: co-offering availability does NOT imply the support is required, "
    "sufficient, or causal. Placement/prerequisite blocking, seat availability, "
    "and a conflict-free same-term combination are NOT assessed."
)


def _norm_coreq_map(coreq_map):
    """Re-key a coreq map onto the ``normalize_course_code`` join basis. This is
    the SAME identity the eLumen<->catalog join already uses (see
    ``build_live_workbook._program_subjects``): it strips leading zeros so a
    zero-padded live schedule code ("MATH 0238") and an eLumen-stripped one
    ("MATH 238") match. ``mapping._norm`` does NOT strip zeros, so using it here
    would SILENTLY miss a real corequisite on live data. Drops empty keys / blank
    literals; dedupes."""
    out = {}
    for key, vals in (coreq_map or {}).items():
        k = normalize_course_code(key or "")
        if not k:
            continue
        coreqs = []
        seen = set()
        for v in (vals or []):
            nv = normalize_course_code(v or "")
            if nv and nv not in seen:
                seen.add(nv)
                coreqs.append(nv)
        if coreqs:
            out[k] = coreqs
    return out


def _offered_by_code(sections):
    """``offered_by_course`` re-keyed onto ``normalize_course_code`` (zero-stripped),
    matching ``_norm_coreq_map`` so the join holds on zero-padded live data. Two
    section ids that collapse to the same code (a "MATH 0238"/"MATH 238" data
    anomaly) merge — they ARE the same course."""
    out = {}
    for k, secs in offered_by_course(sections).items():
        out.setdefault(normalize_course_code(k), []).extend(secs)
    return out


def _assess(gw, coreq_by_course, offered, year1_str):
    """Assess one identified gateway's corequisite co-offering in the first year.

    ``offered`` is keyed by ``normalize_course_code``; ``year1_str`` is the
    first-year window coerced to ``str`` so an int/str term mismatch never causes
    a false miss in the same-term intersection."""
    if not gw["identified"]:
        return {"identified": False, "via": "none",
                "reason": "no transfer-level gateway course identified for this area"}
    cid = normalize_course_code(gw["course"])
    base = {"identified": True, "course": gw["display"], "via": gw["via"],
            "transfer_level": gw.get("transfer_level", "unverified")}
    coreqs = coreq_by_course.get(cid, [])
    if not coreqs:
        base.update({"has_corequisite": False,
                     "reason": ("no corequisite linkage for this gateway in the "
                                "eLumen corequisite data")})
        return base

    def _window_terms(course_id):
        return {str(s["term"]) for s in offered.get(course_id, [])
                if str(s["term"]) in year1_str}

    gw_window = _window_terms(cid)
    detail = []
    obstructions = []
    # If the GATEWAY itself has no first-year section, no corequisite CAN be
    # co-offered with it — blame the gateway once, not each support course (which
    # would misattribute the gap to the corequisite).
    if not gw_window:
        obstructions.append(
            f"the gateway {gw['display']} has no section in the first-year window, "
            f"so no corequisite can be co-offered with it")
    for coreq in coreqs:
        all_secs = offered.get(coreq, [])
        coreq_window = _window_terms(coreq)
        co_terms = sorted(gw_window & coreq_window, key=str)
        detail.append({
            "course": coreq,
            "offered": bool(all_secs),
            "co_offered_terms": list(co_terms),
            "co_offered_year1": bool(co_terms),
        })
        if not all_secs:
            obstructions.append(
                f"corequisite {coreq} is not offered in the analyzed schedule")
        elif not coreq_window:
            obstructions.append(
                f"corequisite {coreq} is offered only outside the first-year window")
        elif not co_terms and gw_window:
            obstructions.append(
                f"corequisite {coreq} is offered in year 1 but never in the same "
                f"term as the gateway")
    co_terms_any = sorted({t for d in detail for t in d["co_offered_terms"]}, key=str)
    base.update({
        "has_corequisite": True,
        "corequisites": coreqs,
        "corequisite_detail": detail,
        "co_offered_year1": any(d["co_offered_year1"] for d in detail),
        "all_corequisites_co_offered_year1": all(d["co_offered_year1"] for d in detail),
        "co_offered_terms": co_terms_any,
        "obstructions": obstructions,
    })
    return base


def corequisite_availability_report(sections, *, program=None, coreq_map=None,
                                    horizon_terms=None):
    """AB1705 corequisite co-availability block for one program (active/inert).

    Inert when there are no offered sections, no corequisite linkage was supplied
    (the default path — coreqs are excluded from the prereq fetch), no
    transfer-level gateway can be identified, or none of the identified gateways
    has a corequisite in the map. Active otherwise, with a per-gateway co-offering
    assessment, the first-year window, and an explicit not_assessed list.
    """
    label = COREQUISITE_AVAILABILITY_LABEL
    if not sections:
        return {"status": "inert", "label": label,
                "reason": "no offered sections to assess corequisite co-availability",
                "remedy": "fetch or import a schedule that carries offered sections"}

    coreq_by_course = _norm_coreq_map(coreq_map)
    if not coreq_by_course:
        return {
            "status": "inert", "label": label,
            "reason": ("no corequisite linkage available — corequisites are excluded "
                       "from the default eLumen prerequisite fetch"),
            "remedy": ("run with --elumen-live so the catalog corequisite "
                       "(itemType=Co-Requisite) leaves are captured, or inject a "
                       "coreq_map"),
        }

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
    year1_str = {str(t) for t in year1}
    offered = _offered_by_code(sections)
    english = _assess(gateways["english"], coreq_by_course, offered, year1_str)
    math = _assess(gateways["math"], coreq_by_course, offered, year1_str)
    if not (english.get("has_corequisite") or math.get("has_corequisite")):
        return {
            "status": "inert", "label": label,
            "reason": ("the identified transfer-level gateway course(s) have no "
                       "corequisite in the eLumen corequisite data, so there is no "
                       "co-availability to assess"),
            "remedy": ("verify the catalog lists a corequisite (itemType=Co-Requisite) "
                       "for the transfer-level gateway, or fetch a fuller eLumen slice"),
        }

    block = {
        "status": "active", "label": label,
        "first_year_terms": [str(t) for t in year1],
        "english": english,
        "math": math,
        "both_gateways_coreq_co_offered_year1": bool(english.get("co_offered_year1")
                                                     and math.get("co_offered_year1")),
        "not_assessed": [
            {"check": "placement_prerequisite_blocking", "status": "inert",
             "reason": ("no placement or prerequisite-completion data exists in any "
                        "LACCD source, so a gateway+corequisite pair may be unreachable "
                        "in year 1 even when co-offered")},
            {"check": "corequisite_enrollment_linkage", "status": "inert",
             "reason": ("co_offered means both run in the same term per the CATALOG "
                        "requisites — it does NOT check that registration actually "
                        "links/requires the pair, that a seat is open (live seat counts "
                        "are unreliable), or that the two have a conflict-free "
                        "meeting-time combination")},
            {"check": "student_completion_or_corequisite_effectiveness", "status": "inert",
             "reason": ("no student-level outcome exists; this is a co-OFFERING proxy. "
                        "Per AB1705 evidence direct placement was the dominant lever and "
                        "corequisite is one supported form — availability is not a "
                        "measured or causal throughput gain")},
        ],
    }
    # The window is the earliest FIRST_YEAR_TERMS distinct terms; on a single-term
    # fetch it collapses to one term. Surface that (mirrors F8) so a reader does
    # not over-read "first year" from what may be one published term.
    if len(year1) < FIRST_YEAR_TERMS:
        block["window_note"] = (
            f"the analyzed schedule has only {len(year1)} term(s), so the "
            f"first-year window is narrower than {FIRST_YEAR_TERMS} terms; "
            "co_offered_year1 reflects only that window")
    return block
