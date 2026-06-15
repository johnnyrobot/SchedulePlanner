"""perturbation.py — E14: minimal-perturbation offering recommender.

The INVERSE of E11 (``infeasibility.py``). Where E11 isolates the minimal set of
required courses that make a cohort plan infeasible, E14 answers the scheduling
office's question: **what is the fewest set of OFFERING changes that flips a
program's required path from structurally not-buildable to buildable?**

It reads the same F1 buildability audit (``buildability.audit_program``) and the
SAME inputs the F1 detector uses, and recommends the minimum-cardinality set of
offering actions that clear the program's offering-fixable structural gaps:

  * ``add_section`` — one per required course with NO offered section in the
    audited terms (currently ``missing``);
  * ``add_choice_option`` — for a disjunctive ``major_choices`` bucket that is
    short, the shortfall ``need - offered`` (offer that many more distinct
    currently-unoffered options);
  * ``add_alt_time_section`` — a deterministic minimum **vertex cover** of the
    pairwise-hard time-conflict graph among required courses (every offered
    section of two co-required courses overlaps): adding one alternate-time (or
    async) section to each covered course breaks all its conflicts.

It then RE-AUDITS the program against a synthetic section set with those offerings
added and reports ``buildable_after`` honestly — a deterministic re-solve that
VERIFIES the recommendation actually clears the gaps (and never overclaims when a
bucket's ``need`` exceeds its option set, or when a non-offering blocker remains).

Honesty (doctrines 2 + #17 ceiling):
  * Recommends OFFERINGS, never a student outcome — there is no completion label
    in any LACCD source. "Buildable" here is the F1 structural-feasibility PROXY
    (required courses offered, choice buckets fillable, no all-section time
    conflict), NOT the engine cohort plan and NOT prerequisite-horizon feasibility
    (that is E11), and NOT a prediction that students will complete.
  * The minimum count is over a SPECIFIC repair model (one added alternate-time /
    async section per gap); other repairs exist. Seat / instructor / room
    feasibility of the recommended sections, and the prerequisite-horizon plan,
    are NOT assessed (surfaced in ``not_assessed``).
  * De-catalogued (dead) required courses are EXCLUDED — re-offering a course the
    catalog dropped is a curriculum decision, not an offering change.

Determinism: pure Python + a CP-SAT vertex cover with ``num_search_workers=1``,
``random_seed=42``, NO wall-clock budget, and a two-phase lexicographic objective
(minimize the cover size, then prefer lexicographically-earlier courses) so the
chosen cover is bit-for-bit reproducible. Runs OUTSIDE ``engine.run``;
JSON-serializable.
"""
from __future__ import annotations

from ortools.sat.python import cp_model

import buildability
from sources import mapping

MIN_PERTURBATION_LABEL = (
    "Minimal-perturbation recommender — the fewest OFFERING changes (add a "
    "section / add an alternate-time section) that flip a program's required path "
    "from structurally not-buildable to buildable. A structural OFFERING "
    "recommendation, NOT a student outcome and NOT a completion claim — no "
    "student-level outcome exists in any LACCD source. 'Buildable' is the F1 "
    "structural-feasibility PROXY (required courses offered, choice buckets "
    "fillable, no all-section time conflict), NOT the engine cohort plan and NOT "
    "prerequisite-horizon feasibility (that is the infeasibility explainer). The "
    "count is the minimum under one repair model (an added alternate-time/async "
    "section per gap); seat, instructor, and room feasibility of the recommended "
    "offerings are NOT assessed."
)


def _solver():
    s = cp_model.CpSolver()
    s.parameters.num_search_workers = 1   # reproducible CP-SAT output (PRD N11)
    s.parameters.random_seed = 42         # fixed seed; preserves determinism
    # No wall-clock budget: the cover model is tiny and decided instantly, so the
    # result never depends on machine speed.
    return s


def minimal_conflict_cover(edges):
    """Deterministic minimum vertex cover of the pairwise-hard conflict graph.

    ``edges`` is an iterable of 2-element course pairs. Returns the sorted list of
    courses to which one alternate-time (or async) section must be added so that
    every conflict edge has a covered endpoint — the fewest such courses, with a
    lexicographic tie-break (prefer earlier-sorted courses) so the specific cover
    is bit-for-bit reproducible. Empty edges -> ``[]``.
    """
    nodes = sorted({n for e in edges for n in e})
    if not nodes:
        return []
    m = cp_model.CpModel()
    v = {n: m.NewBoolVar(f"cover_{i}") for i, n in enumerate(nodes)}
    for a, b in edges:
        m.Add(v[a] + v[b] >= 1)

    # Phase 1: minimize the cover SIZE.
    m.Minimize(sum(v.values()))
    s1 = _solver()
    if s1.Solve(m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # Every edge has two endpoints, so a cover always exists; defensive only.
        return nodes
    size = int(round(s1.ObjectiveValue()))
    # A valid minimum-size cover (the lex tie-break below refines WHICH one) — also
    # the deterministic fallback if phase 2 cannot run.
    phase1_cover = sorted(n for n in nodes if s1.Value(v[n]) == 1)

    # The base-2 positional weight below overflows int64 once the graph reaches ~63
    # nodes; conflict graphs among required courses are tiny (a course with one
    # async/TBA section breaks any all-overlap), so this is unreachable from real
    # inputs — fall back to the phase-1 cover rather than feed CP-SAT an invalid
    # (overflowing) coefficient.
    if len(nodes) >= 62:
        return phase1_cover

    # Phase 2: fix the size, then prefer covers that INCLUDE lexicographically-
    # earlier courses. A base-2 positional weight (2**i for the i-th sorted node)
    # makes each node's coefficient strictly dominate ALL later nodes combined, so
    # the minimum is the UNIQUE cover whose membership bitmask is smallest — a true
    # lexicographic order. A plain linear ``i * v`` would TIE distinct covers
    # (e.g. {C0,C3} vs {C1,C2}, 0+3 == 1+2), leaving the pick to CP-SAT's internal
    # heuristics, which are not stable across OR-Tools versions.
    m.Add(sum(v.values()) == size)
    m.Minimize(sum((1 << i) * v[n] for i, n in enumerate(nodes)))
    s2 = _solver()
    # GUARD the phase-2 status (mirroring phase 1): never read a cover off an
    # unsolved / invalid model — fall back to the valid phase-1 cover instead.
    if s2.Solve(m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return phase1_cover
    return sorted(n for n in nodes if s2.Value(v[n]) == 1)


def _async_section(course, term):
    """A synthetic async/TBA section of ``course`` in ``term`` — no days/times, so
    it is always available and conflict-free (``pairwise_hard_conflict`` is False
    whenever either course has an async section). The minimal structural lever."""
    return {"course": course, "term": term, "class_nbr": f"SYNTH-{course}",
            "days": "", "times": "", "status": "Open"}


def _offered_options(choice, offered, horizon):
    """(offered_ids, unoffered_ids) for a choice bucket's normalized options."""
    opts = sorted({mapping._norm(o) for o in choice.get("options", []) if o})
    offered_ids = [o for o in opts
                   if buildability._in_horizon(offered.get(o, []), horizon)]
    unoffered = [o for o in opts if o not in set(offered_ids)]
    return offered_ids, unoffered


def _program_changes(program, sections, audit, *, ge_coverage, active_courses,
                     horizon_terms, by_design):
    """Compute the minimal offering changes for ONE program from its F1 audit.

    Returns the recommendation dict, or ``None`` when the program's required path
    is already structurally buildable (no offering-fixable gap)."""
    horizon = set(horizon_terms) if horizon_terms else None
    offered = buildability.offered_by_course(sections)

    missing = list(audit["missing"])  # already by_design- and (live) filtered
    dead = set(audit["dead_requirements"])
    # A de-catalogued required course is a curriculum fix, not an offering — never
    # recommend re-offering it; surface it instead.
    fixable_missing = [c for c in missing if c not in dead]

    hard_edges = [list(pair) for pair in audit["time_conflict"]["pairwise_hard"]]
    cover = minimal_conflict_cover(hard_edges)

    actions, notes, synthetic = [], [], []
    # The term a synthetic offering is placed in: the earliest audited term (any
    # in-horizon term makes a missing course available / breaks an all-overlap).
    all_terms = sorted({s["term"] for secs in offered.values() for s in secs
                        if s["term"] is not None}, key=str)
    term0 = (sorted(horizon, key=str)[0] if horizon
             else (all_terms[0] if all_terms else "SYNTH"))

    for c in sorted(fixable_missing):
        actions.append({
            "action": "add_section", "course": c,
            "reason": ("no section offered in the audited terms — offer at least "
                       "one (an async/TBA section structurally suffices)")})
        synthetic.append(_async_section(c, term0))

    for c in sorted(dead & set(missing)):  # sorted: set-iteration order must not leak
        notes.append(f"{c}: required but absent from the active catalog "
                     "(a curriculum decision, not an offering change) — excluded")

    cleared_choices = True
    for ch in (program or {}).get("major_choices", []):
        need = buildability._int(ch.get("need", ch.get("required_count", 1))) or 1
        offered_ids, unoffered = _offered_options(ch, offered, horizon)
        shortfall = need - len(offered_ids)
        if shortfall <= 0:
            continue
        if len(unoffered) < shortfall:
            cleared_choices = False
            notes.append(
                f"choice bucket needs {need} of {sorted(set(offered_ids) | set(unoffered))} "
                f"but only {len(unoffered)} more option(s) could be offered — its "
                "need exceeds the option set; cannot be cleared by adding offerings")
            add_ids = list(unoffered)
        else:
            add_ids = unoffered[:shortfall]
        actions.append({
            "action": "add_choice_option",
            "options": sorted(set(offered_ids) | set(unoffered)),
            "need": need, "offered": len(offered_ids), "shortfall": shortfall,
            "offer_candidates": sorted(add_ids),
            "reason": ("the disjunctive requirement is short — offer "
                       f"{shortfall} more distinct option(s)")})
        synthetic.extend(_async_section(o, term0) for o in add_ids)

    for c in cover:
        edges_hit = [e for e in hard_edges if c in e]
        partners = sorted({(e[0] if e[1] == c else e[1]) for e in edges_hit})
        actions.append({
            "action": "add_alt_time_section", "course": c, "resolves": partners,
            "reason": ("every offered section conflicts with a co-required course "
                       f"({', '.join(partners)}) — add one section at a "
                       "non-overlapping time")})
        synthetic.append(_async_section(c, term0))

    if not actions and not notes:
        return None  # already buildable

    # The minimum is the count of DISTINCT offerings actually proposed — dedup the
    # synthetic sections by (course, term). This is correct even when a bucket's
    # need exceeds its option set (only len(unoffered) offerings are buildable, not
    # the raw shortfall) and if any course were shared across gap categories.
    total_changes = len({(s["course"], s["term"]) for s in synthetic})

    # Re-audit against the synthetic (offerings-added) sections — the inverse
    # re-solve that VERIFIES the recommendation clears the structural gaps.
    after = buildability.audit_program(
        program, sections + synthetic, ge_coverage=ge_coverage,
        active_courses=active_courses, horizon_terms=horizon_terms,
        by_design=by_design)
    buildable_after = (
        cleared_choices
        and not after["missing"]
        and not after["time_conflict"]["pairwise_hard"]
        and all(g["slack"] >= 0 for g in after["choice_groups"]))

    return {
        "code": program.get("code", ""),
        "title": program.get("title", ""),
        "total_changes": total_changes,
        "actions": actions,
        "notes": notes,
        "score_before": audit["score"],
        "score_after": after["score"],
        "buildable_after": buildable_after,
    }


# Checks the minimal-perturbation model deliberately does not evaluate.
_NOT_ASSESSED = [
    {"check": "seat_instructor_room_feasibility", "status": "inert",
     "reason": ("whether a recommended section can actually be staffed, roomed, and "
                "filled — an offering proxy cannot see capacity, faculty, or rooms")},
    {"check": "prerequisite_horizon_plan", "status": "inert",
     "reason": ("whether the prerequisite chain fits the cohort term horizon — that "
                "is the infeasibility explainer (E11), a separate diagnostic")},
    {"check": "season_single_section_term_clash_risks", "status": "inert",
     "reason": ("recommended-season mismatches, single-section bottlenecks, and "
                "same-semester term clashes are advisory RISKS, not minimal-set "
                "blockers, so they are not counted here")},
    {"check": "student_completion", "status": "inert",
     "reason": ("a planning-structure recommendation only; no student-level outcome "
                "exists in any LACCD source")},
]


def perturbation_report(programs, sections, *, ge_coverage=None, active_courses=None,
                        horizon_terms=None, by_design=None):
    """Honest active/inert envelope for the minimal-perturbation recommender.

    Inert (with a reason) when there is no program, no section, or every audited
    program's required path is ALREADY structurally buildable (nothing to
    recommend) — never an empty 'all good'. Active payload lists, per program that
    needs changes, the minimal offering action set and the re-audited
    ``buildable_after`` verification.
    """
    if not programs:
        return {"status": "inert", "label": MIN_PERTURBATION_LABEL,
                "reason": "no program supplied to recommend offering changes for"}
    if not sections:
        return {"status": "inert", "label": MIN_PERTURBATION_LABEL,
                "reason": ("no offered sections to assess the required path against")}

    horizon = (horizon_terms if horizon_terms is not None
               else sorted({r.get("term") for r in sections if r.get("term") is not None},
                           key=lambda t: str(t)))

    recs = []
    for p in programs:
        audit = buildability.audit_program(
            p, sections, ge_coverage=ge_coverage, active_courses=active_courses,
            horizon_terms=horizon, by_design=by_design)
        rec = _program_changes(
            p, sections, audit, ge_coverage=ge_coverage, active_courses=active_courses,
            horizon_terms=horizon, by_design=by_design)
        if rec is not None:
            recs.append(rec)

    if not recs:
        return {"status": "inert", "label": MIN_PERTURBATION_LABEL,
                "reason": ("every audited program's required path is already "
                           "structurally buildable — no offering change is needed")}

    recs.sort(key=lambda r: (-r["total_changes"], r["code"]))
    return {
        "status": "active", "label": MIN_PERTURBATION_LABEL,
        "horizon_terms": horizon,
        "programs": recs,
        "not_assessed": _NOT_ASSESSED,
    }
