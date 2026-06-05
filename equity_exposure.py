"""F6 — Equity / Archetype Exposure View (pure, offline, deterministic).

A THIN WRAPPER over the F1 buildability audit: re-run ``buildability_report`` on
section lists FILTERED to a constrained availability window — evening-only,
online-only, or two-days-a-week — and report, per program, the constrained score,
the signed delta vs the unconstrained baseline, and which required courses become
unavailable ("collapse") under the constraint.

Everything here is a STRUCTURAL availability PROXY, never a measured equity
outcome. 'Collapse' means the published required path is not schedulable from the
sections that fit a window — it is NOT a claim that any real working / parent /
URM student fails, drops out, or is harmed (no student-level outcome exists in any
LACCD source).

Design (mirrors buildability.py / grid_pressure.py / demand_supply.py): stdlib
only — no network, no solver, no pandas. It operates on the raw section dicts the
live/import pipeline already builds and runs OUTSIDE engine.run. The baseline
buildability audit is recomputed self-contained (it does NOT depend on F1's
analysis_key), so the baseline F6 score equals the F1 score exactly.

Archetype computability is honest and path-dependent:
  * "evening" and "two_day" read days/times (present on BOTH the live and import
    paths), so they are always computable.
  * "online" needs the live schedule API's per-section modality (the ``classType``
    list, carried as ``modality``). The offline schedule-export importer does not
    carry modality, so on an imported schedule the online archetype reports
    ``computable: False`` with a reason — never a guess.
"""
from __future__ import annotations

from buildability import audit_program, buildability_report
from sources import timeblocks

# Evening = a section whose earliest meeting starts at/after 5:00 PM. The value
# reuses grid_pressure.AFTERNOON_END (the codebase already calls >= 5 PM "evening";
# see grid_pressure._bucket), kept local so this module stays import-cheap.
EVENING_START = 1020          # 5:00 PM (minutes since midnight)
MAX_DAYS_PER_WEEK = 2         # "two days a week" upper bound on distinct meeting days
MAX_NEWLY_UNAVAILABLE = 30    # cap the per-program newly-unavailable list (no silent truncation)

# HYBRID / HYFLEX still require in-person attendance, so they are NOT available to
# an online-only student — only a section explicitly flagged ONLINE qualifies.
_ONLINE_TOKENS = {"ONLINE"}

# Honesty caveat that travels with every report (see module docstring).
EQUITY_LABEL = (
    "Archetype exposure — a STRUCTURAL availability PROXY, not a measured "
    "equity outcome. It re-runs the buildability audit using ONLY the offered "
    "sections that fit a constrained window (evening start ≥ 5 PM, online-only, "
    "or ≤ 2 meeting days). 'Collapse' means the published required path is not "
    "schedulable from sections in that window — it does NOT claim that any real "
    "working / parent / URM student fails, drops out, or is harmed (no "
    "student-level outcome exists in any LACCD source). Online detection needs the "
    "live API's modality (classType) field; on an imported schedule export modality "
    "is absent, so the online archetype is reported NOT ASSESSED, never guessed.")

_DEFAULT_ARCHETYPES = ("evening", "online", "two_day")
_ARCHETYPE_NAMES = {
    "evening": "Evening-only (start ≥ 5:00 PM)",
    "online": "Online-only",
    "two_day": "Two days a week (≤ 2 meeting days)",
}
_ARCHETYPE_PREDICATES = {
    "evening": "section's earliest meeting starts at or after 5:00 PM (async/TBA kept)",
    "online": "section modality is ONLINE (HYBRID/HYFLEX excluded; in-person not available online)",
    "two_day": "section meets on ≤ 2 distinct weekdays (async/TBA kept)",
}


# ----------------------------------------------------------------- predicates
def _start_min(r):
    """Earliest meeting start minute for a section, or None when it has no parsed
    meeting (async / TBA / unparseable)."""
    meeting = timeblocks.parse_meeting(r.get("days", ""), r.get("times", ""))
    if not meeting:
        return None
    return min(b[1] for b in meeting)


def _fits_evening(r):
    """An async/TBA section (no meeting) fits the evening window — it imposes no
    morning constraint, mirroring how async sections never conflict
    (timeblocks.pairwise_hard_conflict). A timed section fits iff it starts >= 5 PM.

    Note: a row with a time but blank/unparseable days yields no meeting blocks
    (parse_meeting maps over parse_days), so it reads as async/TBA and is kept —
    intentional (an unschedulable-day row is not a morning lock), not a parse gap."""
    start = _start_min(r)
    return start is None or start >= EVENING_START


def _fits_two_day(r):
    """A section meeting on <= 2 distinct weekdays fits. Async/TBA -> 0 days -> fits.

    Note: a time-present-but-blank-days row parses to 0 days here too, so it is
    treated as async/TBA (kept by every window) — intentional, not a parse gap."""
    return len(set(timeblocks.parse_days(r.get("days", "")))) <= MAX_DAYS_PER_WEEK


def _is_online(r):
    """True when a section is explicitly ONLINE. Reads the live API's ``modality``
    (classType) token list; HYBRID/HYFLEX are excluded (they require in-person
    attendance).

    The room-label fallback (a roomless ``Mission-Online`` / ``MONLINE`` sentinel)
    only applies when modality is ABSENT / EMPTY — never when an explicit HYBRID /
    HYFLEX (or any non-ONLINE) modality is present. A HYBRID section that happens to
    carry an online room label for its async half is still in-person-bearing and
    must lose the online-only window; inferring online from its room would
    over-count availability (real fixture: 9 such non-ONLINE rows)."""
    mods = {str(t).strip().upper() for t in (r.get("modality") or [])}
    if mods & _ONLINE_TOKENS:
        return True
    if mods:
        return False  # explicit non-ONLINE modality (e.g. HYBRID/HYFLEX) -> not online
    # modality absent/empty: fall back to the online room sentinel on a roomless row.
    if not timeblocks.parse_meeting(r.get("days", ""), r.get("times", "")):
        if "ONLINE" in str(r.get("room", "") or "").upper():
            return True
    return False


def _online_computable(sections):
    """Online is computable only when the section set carries modality signal at
    all (the live path). On an imported schedule export no row has a ``modality``
    key or an online room label, so this is False and the archetype reports
    NOT ASSESSED rather than guessing from days/times."""
    for r in sections or []:
        if r.get("modality"):
            return True
        if "ONLINE" in str(r.get("room", "") or "").upper():
            return True
    return False


_FILTERS = {
    "evening": _fits_evening,
    "online": _is_online,
    "two_day": _fits_two_day,
}


# ----------------------------------------------------------------- assembly
def _missing_set(audit_block):
    """The set of required course ids reported missing by a buildability audit's
    single program scorecard (status-active block)."""
    progs = audit_block.get("programs") or []
    return {p.get("code"): set(p.get("missing") or []) for p in progs}


def _score_by_code(audit_block):
    return {p.get("code"): p.get("score") for p in (audit_block.get("programs") or [])}


def _assess_archetype(programs, key, sections, baseline, *, ge_coverage,
                      active_courses, horizon_terms, by_design):
    """Build one archetype's block by re-running buildability on the filtered
    sections and diffing each program against the baseline audit."""
    name = _ARCHETYPE_NAMES.get(key, key)
    if key == "online" and not _online_computable(sections):
        return {
            "key": key, "name": name, "computable": False,
            "reason": ("section modality (classType) is not present on the imported "
                       "records; online-only sections cannot be reliably detected "
                       "— see the label"),
        }
    predicate = _FILTERS[key]
    kept = [r for r in sections if predicate(r)]
    filtered = buildability_report(
        programs, kept, ge_coverage=ge_coverage, active_courses=active_courses,
        horizon_terms=horizon_terms, by_design=by_design)

    base_missing = _missing_set(baseline)
    base_score = _score_by_code(baseline)
    # A filtered run can go fully inert (e.g. an archetype that strands every
    # required course). Treat an inert filtered run as "every baseline-available
    # required course is now missing" by re-auditing per program directly, so the
    # collapse signal is honest rather than swallowed by the inert envelope.
    if filtered.get("status") == "active":
        filt_missing = _missing_set(filtered)
        filt_audits = {p.get("code"): p for p in filtered.get("programs", [])}
    else:
        filt_missing, filt_audits = {}, {}

    truncated = 0
    rows = []
    for p in baseline.get("programs", []):
        code = p.get("code")
        if code in filt_audits:
            fa = filt_audits[code]
            score = fa.get("score")
            still = fa.get("available")
            fmiss = filt_missing.get(code, set())
        else:
            # Per-program self-contained re-audit on the filtered sections so an
            # inert filtered envelope still yields an honest score, not a None.
            fa = audit_program(_program_by_code(programs, code), kept,
                               ge_coverage=ge_coverage, active_courses=active_courses,
                               horizon_terms=horizon_terms, by_design=by_design)
            score = fa.get("score")
            still = fa.get("available")
            fmiss = set(fa.get("missing") or [])
        base = base_score.get(code, p.get("score"))
        newly = sorted(fmiss - base_missing.get(code, set()))
        if len(newly) > MAX_NEWLY_UNAVAILABLE:
            truncated += len(newly) - MAX_NEWLY_UNAVAILABLE
            newly = newly[:MAX_NEWLY_UNAVAILABLE]
        rows.append({
            "code": code, "title": p.get("title", ""),
            "score": score, "baseline_score": base,
            "score_delta": (score - base) if (score is not None and base is not None) else None,
            "collapsed": bool(newly),          # OQ4: collapse == any newly-unavailable required course
            "newly_unavailable": newly,
            "still_available": still, "required_total": p.get("required_total"),
        })
    return {
        "key": key, "name": name, "predicate": _ARCHETYPE_PREDICATES.get(key, ""),
        "computable": True,
        "sections_kept": len(kept), "sections_total": len(sections),
        "programs": rows, "truncated_newly_unavailable": truncated,
    }


def _program_by_code(programs, code):
    for p in programs:
        if p.get("code", "") == code:
            return p
    return {"code": code, "courses": []}


def equity_exposure_report(programs, sections, *, ge_coverage=None,
                           active_courses=None, horizon_terms=None,
                           by_design=None, archetypes=None):
    """Re-run the F1 buildability audit under constrained availability windows.

    Honest active/inert envelope, mirroring buildability_report:
      * inert (no program / no sections / inert baseline) carries a specific reason;
      * active carries one block per assessed archetype — computable ones with a
        per-program collapse diff, the online archetype NOT ASSESSED on the import
        path. Never a silent empty 'all clear'.
    """
    if not programs:
        return {"status": "inert", "label": EQUITY_LABEL,
                "reason": "no program supplied to audit"}
    if not sections:
        return {"status": "inert", "label": EQUITY_LABEL,
                "reason": "no offered sections to assess archetype exposure against"}

    # Self-contained baseline (OQ6): F6 does NOT read F1's analysis_key.
    baseline = buildability_report(
        programs, sections, ge_coverage=ge_coverage, active_courses=active_courses,
        horizon_terms=horizon_terms, by_design=by_design)
    if baseline.get("status") != "active":
        return {"status": "inert", "label": EQUITY_LABEL,
                "reason": ("the baseline buildability audit is itself inert ("
                           + str(baseline.get("reason", "no buildable path"))
                           + "), so there is no score to constrain")}

    keys = list(archetypes) if archetypes is not None else list(_DEFAULT_ARCHETYPES)
    horizon = baseline.get("horizon_terms")
    by_design_count = len({str(c) for c in (by_design or set())})
    blocks = [_assess_archetype(programs, k, sections, baseline,
                                ge_coverage=ge_coverage, active_courses=active_courses,
                                horizon_terms=horizon, by_design=by_design)
              for k in keys]
    return {
        "status": "active", "label": EQUITY_LABEL,
        "horizon_terms": horizon, "archetypes": blocks,
        "by_design_count": by_design_count,
        "truncated": {"newly_unavailable": sum(b.get("truncated_newly_unavailable", 0)
                                               for b in blocks)},
    }
