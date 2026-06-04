"""F5 — Demand-vs-Supply Action List (pure, offline, deterministic).

A ranked "add a section" action list for offered courses whose seat SUPPLY
visibly falls short of enrolled+waitlisted DEMAND, plus a neutral capacity-slack
observation (under-filled courses worth a review — never a cut order). A
structural supply-vs-demand PROXY from IR enrollment counts (Cap/Tot/Wait), NOT a
measured completion rate and NOT a causal claim (no student-level outcome exists
in any LACCD source).

Seat counts reach this module two honest, already-plumbed ways:
  * the LIVE path joins an uploaded IR PeopleSoft export onto the fetched sections
    (enrollment.enrich_sections) — the live class-schedule API has NO Cap/Tot/Wait,
    so without that upload F5 stays INERT;
  * the OFFLINE import path's schedule export typically carries Cap/Tot/Wait
    columns natively, so F5 activates from the export itself.

Either way F5 reads the counts off the section records via
``buildability.offered_by_course`` (deduped on (term, class_nbr)); it adds NO
network and runs OUTSIDE engine.run (determinism gate untouched).

Honesty rules baked in:
  * waitlist is weak alone -> a course joins the ADD list only on fill >= 0.95 OR
    (Wait > 15 AND (fill >= 0.90 OR a closed/waitlisted section));
  * under-filled is an OBSERVATION, never a cut recommendation (a false cut can
    strip an evening / online / cohort section students depend on);
  * cross-program demand (F2's ProgramDemand) is an OPTIONAL weight, not a gate;
  * a course with no usable Cap is simply not assessed (silence, never a flag).
"""
from __future__ import annotations

from buildability import _is_closed, offered_by_course
from sources import facility as facility_mod

# Honesty caveat that travels with every report (see module docstring).
DEMAND_SUPPLY_LABEL = (
    "Demand-vs-supply action list — a structural supply-vs-demand PROXY from IR "
    "enrollment counts, not a measured completion rate or causal claim. "
    "'Add a section' is prioritized by demand depth (fill + waitlist) times "
    "cross-program impact; waitlist counts only when paired with high fill or a "
    "closed status. Capacity-slack is a review observation, never a cut order.")

# Documented thresholds (not magic numbers).
FULL_FILL = 0.95            # fill at/over capacity -> qualifies for add on its own
TIGHT_FILL = 0.90           # the high-fill half of the defensive waitlist pairing
WAIT_MIN = 15               # waitlist headcount threshold (roadmap: > 15)
LOW_FILL = 0.40             # capacity-slack (under-filled) threshold
MIN_SLACK_SECTIONS = 2      # need >= 2 sections before consolidation is conceivable
IMPACT_PER_PROGRAM = 0.1    # cross-program impact multiplier slope
ADD_TOP = 20
SLACK_TOP = 15

_SLACK_NOTE = ("review only — this proxy cannot see evening / online / cohort "
               "intent; not a cut recommendation")


def _is_lab(secs, facility):
    """True when any of a course's sections sits in a scarce lab/computer-lab
    room (mirrors cross_program_bottleneck._course_metrics)."""
    if not facility:
        return False
    for s in secs:
        meta = facility.get(facility_mod.norm_facil(s.get("facil_id", "")))
        if facility_mod.is_lab(meta):
            return True
    return False


def _metrics(secs, facility):
    """Aggregate one course's deduped sections (the list value from
    offered_by_course) into demand/supply metrics. cap/tot/wait may be None
    (tolerant ints) so each sum guards falsy values, exactly like seat_pressure."""
    per_term = {}
    for s in secs:
        per_term[s["term"]] = per_term.get(s["term"], 0) + 1
    cap = sum(s["cap"] for s in secs if s["cap"])
    tot = sum(s["tot"] for s in secs if s["tot"])
    wait = sum(s["wait"] for s in secs if s["wait"])
    return {
        "cap_total": cap, "tot_total": tot, "wait_total": wait,
        "n_sections": len(secs),
        "min_sections_per_term": (min(per_term.values()) if per_term else 0),
        "closed": any(_is_closed(s["status"]) for s in secs),
        "is_lab": _is_lab(secs, facility),
        "fill": (tot / cap) if cap > 0 else None,
        "demand_ratio": ((tot + wait) / cap) if cap > 0 else None,
    }


def _qualifies_add(m):
    """Defensive over-subscription gate: high fill alone, OR a real waitlist
    paired with high fill / a closed section (waitlist is weak alone)."""
    fill = m["fill"]
    if fill is None:
        return False
    if fill >= FULL_FILL:
        return True
    return m["wait_total"] > WAIT_MIN and (fill >= TIGHT_FILL or m["closed"])


def _add_reasons(m, n_programs):
    out = []
    if m["fill"] is not None:
        out.append(f"fill {m['fill']:.2f}")
    if m["wait_total"] > 0:
        out.append(f"{m['wait_total']} waitlisted")
    if m["closed"]:
        out.append("a section is closed/waitlisted")
    if m["min_sections_per_term"] <= 1:
        out.append("single section in some term")
    if m["is_lab"]:
        out.append("scarce lab room — adding is capacity-constrained")
    if n_programs:
        out.append(f"required by {n_programs} program(s)")
    return out


def demand_supply_report(sections, *, program_demand=None, facility=None,
                         top=ADD_TOP):
    """Honest active/inert envelope for the demand-vs-supply action list.

    Inert (with a reason) when there are no sections, or when no course carries
    usable seat counts (Cap) — the live class-schedule API has none, so a bare
    live fetch stays inert until an IR enrollment export is joined or a
    counts-carrying schedule export is imported. Never an empty 'all good'.

    Active payload: ``{status, label, add_list, capacity_slack,
    sections_with_counts, program_weighted, not_assessed, truncated}``.
    """
    if not sections:
        return {"status": "inert", "label": DEMAND_SUPPLY_LABEL,
                "reason": "no offered sections to assess demand against"}

    offered = offered_by_course(sections)
    required = getattr(program_demand, "required", None) or {}
    program_weighted = bool(required)

    add_rows, slack_rows = [], []
    sections_with_counts = 0
    assessed = set()
    for course, secs in offered.items():
        sections_with_counts += sum(1 for s in secs if s["cap"])
        m = _metrics(secs, facility)
        if m["cap_total"] <= 0:
            continue
        assessed.add(course)
        n_programs = len(required.get(course, ())) if program_weighted else 0
        if _qualifies_add(m):
            impact_mult = 1.0 + IMPACT_PER_PROGRAM * n_programs
            add_rows.append({
                "course": course,
                "demand_ratio": round(m["demand_ratio"], 2),
                "fill": round(m["fill"], 2),
                "cap_total": m["cap_total"], "tot_total": m["tot_total"],
                "wait_total": m["wait_total"], "closed": m["closed"],
                "n_sections": m["n_sections"],
                "min_sections_per_term": m["min_sections_per_term"],
                "is_lab": m["is_lab"], "n_programs": n_programs,
                "required": bool(program_weighted and course in required),
                "action_score": round(m["demand_ratio"] * impact_mult, 2),
                "reasons": _add_reasons(m, n_programs),
            })
        elif m["fill"] is not None and m["fill"] <= LOW_FILL \
                and m["n_sections"] >= MIN_SLACK_SECTIONS:
            slack_rows.append({
                "course": course, "fill": round(m["fill"], 2),
                "cap_total": m["cap_total"], "tot_total": m["tot_total"],
                "n_sections": m["n_sections"], "note": _SLACK_NOTE,
            })

    if not assessed:
        return {"status": "inert", "label": DEMAND_SUPPLY_LABEL,
                "reason": ("no course carries usable seat counts — the live "
                           "schedule API has no Cap/Tot/Wait; upload an IR "
                           "enrollment export whose term matches, or import a "
                           "schedule export that carries counts")}

    add_rows.sort(key=lambda r: (-r["action_score"], -r["demand_ratio"],
                                 -r["n_programs"], r["course"]))
    slack_rows.sort(key=lambda r: (r["fill"], -r["n_sections"], r["course"]))

    not_assessed = (sum(1 for c in required if c not in assessed)
                    if program_weighted else 0)

    return {
        "status": "active", "label": DEMAND_SUPPLY_LABEL,
        "add_list": add_rows[:top],
        "capacity_slack": slack_rows[:SLACK_TOP],
        "sections_with_counts": sections_with_counts,
        "program_weighted": program_weighted,
        "not_assessed": not_assessed,
        "truncated": {"add_list": max(0, len(add_rows) - top),
                      "capacity_slack": max(0, len(slack_rows) - SLACK_TOP)},
    }
