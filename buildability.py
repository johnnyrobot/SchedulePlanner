"""
buildability.py — Program-Map Buildability Audit (feature F1).

For each program, answer the *honest* completion question the LACCD data can
actually support: **is the program's required path schedulable** against a set of
offered sections — every required course offered, time-conflict-free, on its
recommended season, seat-available, with no dead (de-catalogued) requirements?

This is the "structural-feasibility + seat-supply SCORE" that the All Courses
Data analysis named as the only honest completion target (no student-level
outcome exists in any LACCD source — so this is a PROXY, never a measured
completion rate).

Design (mirrors timeblocks.py): pure Python, no network, no solver, no pandas —
it operates on the raw section dicts the live/import pipeline already builds
(``course`` / ``term`` / ``class_nbr`` / ``days`` / ``times`` / ``Cap Enrl`` /
``Tot Enrl`` / ``status``) and runs OUTSIDE ``engine.run``. The result is
attached to ``results["analysis"]["buildability"]`` alongside the other advisory
detectors, and is honestly labelled active/inert like them.

The score is intentionally transparent (see ``_score``): availability dominates;
a hard time conflict, an unsatisfiable choice bucket, a dead requirement, or a
recommended-season miss each deduct a fixed amount. It is a *triage* signal, not
a precise metric.
"""
from __future__ import annotations

from sources import mapping, timeblocks

# Honesty caveat that travels with every report (see module docstring).
LABEL = (
    "Structural-feasibility + seat-supply PROXY per program. NOT a measured "
    "completion rate — no student-level outcome exists in any LACCD source. "
    "Time-conflict feasibility covers TIMED sections only (async/TBA never "
    "conflict). Recommended-season and prerequisite checks are advisory. "
    "Seat pressure is a demand proxy, not causation."
)

# Default planning cadence -> season per abstract recommended-semester index.
# Mirrors engine.term_season's default (Fall/Spring) and engine.season_of_code,
# kept local so this module stays solver-free / import-cheap.
_SEASON_CADENCE = ("Fall", "Spring")
_TERM_DIGIT_SEASON = {"8": "Fall", "1": "Winter", "2": "Spring", "6": "Summer"}


def _season_of_code(t):
    """LACCD term code -> season (last digit). Unknown -> 'Spring'. Mirrors
    engine.season_of_code."""
    return _TERM_DIGIT_SEASON.get(str(t).strip()[-1:], "Spring")


def _term_season(sem):
    """Abstract 1-based recommended-semester index -> season under the default
    Fall/Spring cadence. Mirrors engine.term_season's default."""
    return _SEASON_CADENCE[(int(sem) - 1) % len(_SEASON_CADENCE)]


def _int(v):
    """Tolerant int (handles None / '' / float / NaN-ish), else None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return int(f)


def _is_closed(status):
    s = str(status or "").strip().lower()
    return s.startswith("clos") or s.startswith("wait")


# ----------------------------------------------------------------- inputs

def required_set(program):
    """Normalized set of a program's hard-required (major-core) course ids.

    Disjunctive ``major_choices`` are handled separately (see ``choice_slack``),
    so they are NOT in this set."""
    return {mapping._norm(c["course_id"])
            for c in (program or {}).get("courses", []) if c.get("course_id")}


def offered_by_course(sections):
    """Map normalized course id -> list of section dicts, **deduped on
    (term, class_nbr)** so meeting-pattern rows never double-count.

    Each section dict: ``{term, class_nbr, meeting, status, cap, tot}``. Rows
    without a class number fall back to a (term, days, times) dedup key."""
    out, seen = {}, {}
    for r in sections:
        cid = mapping._norm(r.get("course", ""))
        if not cid:
            continue
        cls = str(r.get("class_nbr", "") or "").strip()
        term = r.get("term")
        key = (term, cls) if cls else (term, r.get("days", ""), r.get("times", ""))
        s = seen.setdefault(cid, set())
        if key in s:
            continue
        s.add(key)
        out.setdefault(cid, []).append({
            "term": term,
            "class_nbr": cls,
            "meeting": timeblocks.parse_meeting(r.get("days", ""), r.get("times", "")),
            "status": str(r.get("status", "") or r.get("Avail Status", "") or "").strip(),
            "cap": _int(r.get("Cap Enrl")),
            "tot": _int(r.get("Tot Enrl")),
        })
    return out


def _in_horizon(secs, horizon):
    return secs if horizon is None else [s for s in secs if s["term"] in horizon]


# ----------------------------------------------------------------- sub-checks

def availability(required, offered, horizon_terms=None):
    """``(available, missing)`` — required courses with >=1 offered section vs none."""
    horizon = set(horizon_terms) if horizon_terms else None
    avail, missing = [], []
    for cid in sorted(required):
        secs = _in_horizon(offered.get(cid, []), horizon)
        (avail if secs else missing).append(cid)
    return avail, missing


def choice_slack(program, offered, horizon_terms=None):
    """Per disjunctive ``major_choices`` bucket: how many options are offered vs
    needed. ``slack < 0`` means the bucket is structurally unsatisfiable."""
    horizon = set(horizon_terms) if horizon_terms else None
    out = []
    for ch in (program or {}).get("major_choices", []):
        opts = sorted({mapping._norm(o) for o in ch.get("options", []) if o})
        need = _int(ch.get("need", ch.get("required_count", 1))) or 1
        cnt = sum(1 for o in opts if _in_horizon(offered.get(o, []), horizon))
        out.append({"options": opts, "need": need, "offered": cnt, "slack": cnt - need})
    return out


def time_conflict(program, required, offered, horizon_terms=None):
    """Time-conflict feasibility for the required path.

    ``pairwise_hard``: required course pairs whose every section overlaps (a
    student literally cannot take both) — plan-independent.
    ``term_clashes``: courses the program recommends for the SAME semester that
    have no conflict-free section combination.
    ``feasible`` is True iff neither fires."""
    horizon = set(horizon_terms) if horizon_terms else None
    meetings = {}
    for cid in required:
        secs = _in_horizon(offered.get(cid, []), horizon)
        if secs:
            meetings[cid] = [s["meeting"] for s in secs]

    hard = []
    cids = sorted(meetings)
    for i in range(len(cids)):
        for j in range(i + 1, len(cids)):
            if timeblocks.pairwise_hard_conflict(meetings[cids[i]], meetings[cids[j]]):
                hard.append([cids[i], cids[j]])

    by_sem = {}
    for c in (program or {}).get("courses", []):
        sem = _int(c.get("recommended_semester"))
        cid = mapping._norm(c.get("course_id", ""))
        if sem is None or cid not in meetings:
            continue
        by_sem.setdefault(sem, []).append(cid)

    term_clashes = []
    for sem, group in sorted(by_sem.items()):
        if len(group) < 2:
            continue
        feasible, culprits = timeblocks.feasible_selection({c: meetings[c] for c in group})
        if not feasible:
            term_clashes.append({"recommended_semester": sem, "courses": culprits})

    return {"feasible": not hard and not term_clashes,
            "pairwise_hard": hard, "term_clashes": term_clashes}


def single_section_required(required, offered, horizon_terms=None):
    """Required courses that run a single section in at least one offered term
    (a bottleneck — one cancellation/conflict strands the path). Mirrors
    engine.analyze's ``single_section`` rule, intersected with the required set."""
    horizon = set(horizon_terms) if horizon_terms else None
    out = []
    for cid in sorted(required):
        secs = _in_horizon(offered.get(cid, []), horizon)
        if not secs:
            continue
        per_term = {}
        for s in secs:
            per_term[s["term"]] = per_term.get(s["term"], 0) + 1
        if per_term and min(per_term.values()) == 1:
            out.append(cid)
    return out


def season_mismatches(program, offered, horizon_terms=None):
    """Required courses whose recommended-semester season is never an offered
    season (e.g. mapped to a Spring slot but only ever offered in Fall).
    Mirrors engine.official_map_issues."""
    horizon = set(horizon_terms) if horizon_terms else None
    out = []
    for c in (program or {}).get("courses", []):
        sem = _int(c.get("recommended_semester"))
        cid = mapping._norm(c.get("course_id", ""))
        secs = _in_horizon(offered.get(cid, []), horizon)
        if sem is None or not secs:
            continue
        offered_seasons = sorted({_season_of_code(s["term"]) for s in secs})
        want = _term_season(sem)
        if want not in offered_seasons:
            out.append({"course": cid, "recommended_semester": sem,
                        "recommended_season": want, "offered_seasons": offered_seasons})
    return out


def seat_pressure(required, offered, horizon_terms=None):
    """Required courses under seat pressure: aggregate fill >= 85% or any
    closed/waitlisted section. Demand PROXY, not causation."""
    horizon = set(horizon_terms) if horizon_terms else None
    out = []
    for cid in sorted(required):
        secs = _in_horizon(offered.get(cid, []), horizon)
        if not secs:
            continue
        cap = sum(s["cap"] for s in secs if s["cap"])
        tot = sum(s["tot"] for s in secs if s["tot"])
        closed = any(_is_closed(s["status"]) for s in secs)
        if cap > 0:
            fill = tot / cap
            if fill >= 0.85 or closed:
                out.append({"course": cid, "fill_pct": round(fill * 100), "closed": closed})
        elif closed:
            out.append({"course": cid, "fill_pct": None, "closed": True})
    return out


def dead_requirements(required, active_courses):
    """``(dead, note)`` — required courses absent from the active catalog set.
    Without an active set (no course master), returns ``([], note)`` and never a
    false positive."""
    if not active_courses:
        return [], ("active course set unknown (no course master supplied) — "
                    "dead-requirement check skipped")
    return sorted(c for c in required if c not in active_courses), None


def ge_summary(ge_rows):
    """F4 seam: a coarse GE-denominator view from ge.resolve rows. ``None`` when
    no GE pattern was requested. ``gaps`` = concrete areas with no offered
    candidate (cannot be scheduled); reserve areas are deferred, not gaps."""
    if not ge_rows:
        return None
    required = len(ge_rows)
    concrete = sum(1 for r in ge_rows
                   if r.get("resolution") == "concrete" and r.get("candidates"))
    reserved = sum(1 for r in ge_rows if r.get("resolution") == "reserve")
    gaps = sorted(str(r.get("area")) for r in ge_rows
                  if r.get("resolution") == "concrete" and not r.get("candidates"))
    return {"areas_required": required, "areas_schedulable": concrete,
            "areas_reserved": reserved, "gaps": gaps}


# ----------------------------------------------------------------- assembly

def _score(required_total, missing, dead, tc, choices, seasons):
    """Transparent triage score in [0, 100]. Availability dominates; structural
    blockers deduct fixed amounts."""
    if not required_total:
        return 0
    avail_ratio = (required_total - len(missing)) / required_total
    score = 100.0 * avail_ratio
    if not tc["feasible"]:
        score -= 15
    if any(c["slack"] < 0 for c in choices):
        score -= 10
    if dead:
        score -= 5
    if seasons:
        score -= 5
    return max(0, min(100, round(score)))


def audit_program(program, sections, *, ge_rows=None, active_courses=None,
                  horizon_terms=None, by_design=None):
    """Score one program's required path against the offered sections. Returns a
    JSON-serializable scorecard (see module docstring for the field meanings)."""
    by_design = {mapping._norm(c) for c in (by_design or set())}
    offered = offered_by_course(sections)
    required = required_set(program)

    avail, missing = availability(required, offered, horizon_terms)
    excluded = sorted(c for c in missing if c in by_design)
    missing = [c for c in missing if c not in by_design]

    dead, dead_note = dead_requirements(required, active_courses)
    dead = [c for c in dead if c not in by_design]
    tc = time_conflict(program, required, offered, horizon_terms)
    choices = choice_slack(program, offered, horizon_terms)
    single = single_section_required(required, offered, horizon_terms)
    seasons = season_mismatches(program, offered, horizon_terms)
    seats = seat_pressure(required, offered, horizon_terms)
    ge = ge_summary(ge_rows)

    score = _score(len(required), missing, dead, tc, choices, seasons)
    parts = [f"{len(avail)}/{len(required)} required courses offered"]
    if missing:
        parts.append(f"{len(missing)} missing")
    parts.append("time-conflict-free" if tc["feasible"] else "has time conflicts")
    if single:
        parts.append(f"{len(single)} single-section risk")
    if dead:
        parts.append(f"{len(dead)} de-catalogued")
    summary = "; ".join(parts) + "."

    return {
        "code": program.get("code", ""),
        "title": program.get("title", ""),
        "required_total": len(required),
        "available": len(avail),
        "missing": missing,
        "dead_requirements": dead,
        "dead_requirements_note": dead_note,
        "single_section_required": single,
        "choice_groups": choices,
        "time_conflict": tc,
        "season_mismatches": seasons,
        "seat_pressure": seats,
        "ge": ge,
        "by_design_excluded": excluded,
        "score": score,
        "summary": summary,
    }


def buildability_report(programs, sections, *, ge_rows=None, active_courses=None,
                        horizon_terms=None, by_design=None):
    """Audit one or more programs. Honest active/inert envelope: inert (with a
    reason) when there is no program, no section, or no required course is offered
    at all — never an empty 'all good'."""
    horizon = (horizon_terms if horizon_terms is not None
               else sorted({r.get("term") for r in sections if r.get("term") is not None},
                           key=lambda t: str(t)))
    if not programs:
        return {"status": "inert", "label": LABEL,
                "reason": "no program supplied to audit"}
    if not sections:
        return {"status": "inert", "label": LABEL,
                "reason": "no offered sections to audit the required path against"}

    audits = [audit_program(p, sections, ge_rows=ge_rows, active_courses=active_courses,
                            horizon_terms=horizon, by_design=by_design)
              for p in programs]
    if not any(a["available"] for a in audits):
        return {"status": "inert", "label": LABEL,
                "reason": ("none of the program's required courses are offered in the "
                           "audited terms (the required<->offered join is empty)")}
    return {"status": "active", "label": LABEL,
            "horizon_terms": horizon, "programs": audits}


if __name__ == "__main__":  # pragma: no cover - manual operator check
    import json
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m buildability <engine_workbook.xlsx>  "
              "(audits each program's required path vs the sections sheet)")
        raise SystemExit(2)
    import pandas as pd

    xl = pd.ExcelFile(sys.argv[1])
    sec = xl.parse("sections").to_dict("records")
    sections = [{"course": r.get("CLASS"), "term": r.get("Term"),
                 "class_nbr": r.get("CLASS Nbr", r.get("Class Nbr", "")),
                 "days": r.get("Days", ""), "times": r.get("Times", ""),
                 "Cap Enrl": r.get("Cap Enrl"), "Tot Enrl": r.get("Tot Enrl"),
                 "status": r.get("Avail Status", "")} for r in sec]
    prog = xl.parse("programs")
    programs = []
    for code, g in prog.groupby("Program Code"):
        programs.append({
            "code": str(code),
            "title": str(g["Program Title"].iloc[0]) if "Program Title" in g else str(code),
            "courses": [{"course_id": r["Course ID"],
                         "recommended_semester": r.get("Recommended Semester")}
                        for _, r in g.iterrows()],
        })
    print(json.dumps(buildability_report(programs, sections), indent=2, default=str))
