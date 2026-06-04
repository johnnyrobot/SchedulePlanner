"""
bottleneck.py — Cross-Program Bottleneck Leaderboard (feature F2).

Ranks the required courses most likely to be a completion bottleneck across the
WHOLE institution: a course required by many programs but offered in few
sections — amplified when those sections sit in scarce lab rooms or are already
full. The one-line pitch: *"fix this one course, help N programs."*

Two signals are joined:

  * **demand** — how many programs require each course — from a Program Course
    Lists export (:class:`sources.program_lists.ProgramDemand`). This is the
    headline dimension and exists ONLY in that file: the live path resolves one
    program per run, so a bare live fetch carries no cross-program counts.
  * **supply** — sections / seats / lab rooms — from the same offered-section
    dicts the live / import pipeline already builds (deduped via
    :func:`buildability.offered_by_course`).

Pure Python — no network, no solver, no pandas — and runs OUTSIDE ``engine.run``,
attaching to ``results["analysis"]["bottlenecks"]`` alongside the other advisory
detectors, honestly labelled active / inert like them.

Honesty: this is a structural supply-vs-demand PROXY, never a measured completion
rate. The demand map is a static snapshot. The required<->offered join uses
:func:`sources.mapping._norm` (which does NOT collapse leading zeros), so
program-list courses that don't join an offered course are reported as
``unmatched_program_courses`` rather than silently dropped. The risk score (see
:func:`leaderboard`) is a transparent, documented heuristic — a triage ranking,
not a metric.
"""
from __future__ import annotations

from buildability import offered_by_course
from sources import facility as facility_mod

# Honesty caveat that travels with every report (see module docstring).
LABEL = (
    "Cross-program bottleneck ranking — a structural supply-vs-demand PROXY, "
    "NOT a measured completion rate. Demand = how many programs require each "
    "course (a static Program Course Lists snapshot); supply = offered sections, "
    "seats, and lab rooms. The risk score is a transparent triage heuristic."
)

# Risk-score amplifiers (documented constants, not magic numbers — see _risk_score).
LAB_MULT = 1.3       # any section in a scarce lab / computer-lab room
FILL_MULT = 1.3      # aggregate fill >= FILL_THRESHOLD, or a closed/waitlisted section
FILL_THRESHOLD = 0.85

# How many program titles to sample into each leaderboard row (deterministic, sorted).
_TITLE_SAMPLE = 5


def _is_closed(status):
    s = str(status or "").strip().lower()
    return s.startswith("clos") or s.startswith("wait")


def _course_metrics(secs, facility):
    """Supply metrics for one course's deduped sections (the list value from
    :func:`offered_by_course`): section count, the minimum sections in any
    offered term, aggregate fill, a closed/waitlisted flag, and whether any
    section sits in a scarce lab room.

    ``_fill`` (raw ratio, or ``None``) is internal — used for scoring/reasons and
    never emitted into a leaderboard row."""
    per_term = {}
    for s in secs:
        per_term[s["term"]] = per_term.get(s["term"], 0) + 1
    min_per_term = min(per_term.values()) if per_term else 0

    cap = sum(s["cap"] for s in secs if s["cap"])
    tot = sum(s["tot"] for s in secs if s["tot"])
    fill = (tot / cap) if cap > 0 else None
    closed = any(_is_closed(s["status"]) for s in secs)

    is_lab = False
    if facility:
        for s in secs:
            meta = facility.get(facility_mod.norm_facil(s.get("facil_id", "")))
            if facility_mod.is_lab(meta):
                is_lab = True
                break

    return {"n_sections": len(secs), "min_sections_per_term": min_per_term,
            "fill_pct": (round(fill * 100) if fill is not None else None),
            "closed": closed, "is_lab": is_lab, "_fill": fill}


def _has_seat_pressure(m):
    return bool(m["closed"]) or (m["_fill"] is not None and m["_fill"] >= FILL_THRESHOLD)


def _risk_score(n_programs, m):
    """Transparent triage score: programs competing per available section, amplified
    by lab scarcity and seat pressure.

    ``round(n_programs / max(1, min_sections_per_term) * lab_mult * fill_mult, 1)``
    where ``lab_mult`` = :data:`LAB_MULT` if any section is a scarce lab and
    ``fill_mult`` = :data:`FILL_MULT` under seat pressure (else 1.0 each)."""
    base = n_programs / max(1, m["min_sections_per_term"])
    lab_mult = LAB_MULT if m["is_lab"] else 1.0
    fill_mult = FILL_MULT if _has_seat_pressure(m) else 1.0
    return round(base * lab_mult * fill_mult, 1)


def _reasons(n_programs, m):
    """Human-readable explanation strings for one leaderboard row."""
    out = [f"required by {n_programs} program{'s' if n_programs != 1 else ''}"]
    mpt = m["min_sections_per_term"]
    if mpt == 1:
        out.append("single section in at least one offered term")
    elif mpt > 1:
        out.append(f"as few as {mpt} sections in an offered term")
    if m["is_lab"]:
        out.append("taught in a scarce lab room")
    if m["closed"]:
        out.append("a section is closed / waitlisted")
    elif m["_fill"] is not None and m["_fill"] >= FILL_THRESHOLD:
        out.append(f"at {m['fill_pct']}% fill")
    return out


def _sample_titles(plans, titles):
    return sorted(titles.get(p, p) for p in plans)[:_TITLE_SAMPLE]


def leaderboard(demand, sections, facility=None, *, top=20, offered=None):
    """Rank required-AND-offered courses by bottleneck risk.

    Returns ``(rows, truncated)`` where ``rows`` is the top-``top`` leaderboard
    (sorted by ``risk_score`` desc, then ``n_programs`` desc, then ``course`` asc
    — fully deterministic) and ``truncated`` is how many ranked rows were dropped
    past the cap (never a silent truncation).

    ``offered`` is an already-computed :func:`offered_by_course` map; passing it
    lets :func:`bottleneck_report` parse the meeting patterns once and share the
    result (mirrors :func:`buildability.audit_program`)."""
    offered = offered_by_course(sections) if offered is None else offered
    rows = []
    for course, plans in demand.required.items():
        secs = offered.get(course)
        if not secs:
            continue  # required but not offered -> see cross_program_gaps
        m = _course_metrics(secs, facility)
        n_programs = len(plans)
        rows.append({
            "course": course,
            "n_programs": n_programs,
            "n_listed": len(demand.listed.get(course, plans)),
            "programs": _sample_titles(plans, demand.titles),
            "n_sections": m["n_sections"],
            "min_sections_per_term": m["min_sections_per_term"],
            "fill_pct": m["fill_pct"],
            "closed": m["closed"],
            "is_lab": m["is_lab"],
            "risk_score": _risk_score(n_programs, m),
            "reasons": _reasons(n_programs, m),
        })
    rows.sort(key=lambda r: (-r["risk_score"], -r["n_programs"], r["course"]))
    return rows[:top], max(0, len(rows) - top)


def cross_program_gaps(demand, sections, *, top=20, offered=None):
    """Required-by-many courses with NO offered section in the window — the
    'missing across N programs' companion to the leaderboard. Returns
    ``(rows, truncated)`` sorted by ``n_programs`` desc, then ``course`` asc.
    ``offered`` may be a pre-computed :func:`offered_by_course` map (shared by
    :func:`bottleneck_report` to avoid re-parsing meeting patterns)."""
    offered = offered_by_course(sections) if offered is None else offered
    rows = [{"course": course, "n_programs": len(plans),
             "programs": _sample_titles(plans, demand.titles)}
            for course, plans in demand.required.items()
            if not offered.get(course)]
    rows.sort(key=lambda r: (-r["n_programs"], r["course"]))
    return rows[:top], max(0, len(rows) - top)


def bottleneck_report(demand, sections, facility=None, *, top=20):
    """Honest active / inert envelope for the cross-program bottleneck audit.

    Inert (with a reason) when no demand map is supplied, when there are no
    offered sections, or when no program-required course matches an offered
    section (the join is empty) — never an empty 'all good'. Active payload:
    ``{status, label, leaderboard, gaps, unmatched_program_courses, truncated}``.
    """
    if demand is None or not getattr(demand, "required", None):
        return {"status": "inert", "label": LABEL,
                "reason": ("no Program Course Lists demand map supplied — F2 needs "
                           "a program-lists export to count cross-program demand "
                           "(the live path resolves one program per run, so a bare "
                           "live fetch has no cross-program counts)")}
    if not sections:
        return {"status": "inert", "label": LABEL,
                "reason": "no offered sections to rank the required courses against"}

    # Parse the meeting patterns ONCE and share the map across both passes + the
    # unmatched count (mirrors buildability.audit_program — F2 is institution-wide,
    # so re-parsing thousands of sections three times would be wasteful).
    offered = offered_by_course(sections)
    board, board_trunc = leaderboard(demand, sections, facility, top=top, offered=offered)
    if not board:
        return {"status": "inert", "label": LABEL,
                "reason": ("no program-required course matched an offered section — "
                           "the required<->offered join is empty (are the demand map "
                           "and the schedule for the same campus / term?)")}

    gaps, gaps_trunc = cross_program_gaps(demand, sections, top=top, offered=offered)
    unmatched = sum(1 for c in demand.required if not offered.get(c))
    return {"status": "active", "label": LABEL,
            "leaderboard": board,
            "gaps": gaps,
            "unmatched_program_courses": unmatched,
            "truncated": {"leaderboard": board_trunc, "gaps": gaps_trunc}}


if __name__ == "__main__":  # pragma: no cover - manual operator check
    import json
    import sys

    from sources import program_lists

    if len(sys.argv) < 3:
        print("usage: python -m cross_program_bottleneck <engine_workbook.xlsx> "
              "<program_lists.(xlsx|csv)> [facility.(xlsx|csv)]")
        raise SystemExit(2)
    import pandas as pd

    xl = pd.ExcelFile(sys.argv[1])
    sec_rows = xl.parse("sections").to_dict("records")
    sections = [{"course": r.get("CLASS"), "term": r.get("Term"),
                 "class_nbr": r.get("CLASS Nbr", r.get("Class Nbr", "")),
                 "days": r.get("Days", ""), "times": r.get("Times", ""),
                 "Cap Enrl": r.get("Cap Enrl"), "Tot Enrl": r.get("Tot Enrl"),
                 "status": r.get("Avail Status", ""),
                 "facil_id": r.get("Facil ID", "")} for r in sec_rows]
    demand = program_lists.load_program_lists(sys.argv[2])
    facility = (facility_mod.load_facility(sys.argv[3]) if len(sys.argv) > 3 else None)
    print(json.dumps(bottleneck_report(demand, sections, facility),
                     indent=2, default=str))
