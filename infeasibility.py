"""infeasibility.py — E11: deterministic infeasibility explainer (CP-SAT MUS).

When the planner (``engine.solve_cohort``) finds NO feasible plan for a program
cohort — even after the season-fix retry (``allow_fixes=True``) — it returns
``None``. This module explains WHY by isolating the MINIMAL set of required major
courses whose mandatory scheduling jointly makes the plan infeasible: relax the
requirement to take any ONE of them and a plan exists again ("a minimal
unsatisfiable set", MUS).

How it stays faithful to the planner WITHOUT touching ``engine.run``:
  * Feasibility depends ONLY on the constraints, never the objective. So this
    module mirrors ``solve_cohort``'s HARD constraints — per-term unit cap,
    prerequisite ordering, time-block conflicts, the term horizon, and the GE
    reserve/concrete requirements held as fixed BACKGROUND — but drops the
    objective and the makespan var, and RELAXES season availability (the
    ``allow_fixes=True`` regime: the planner can shift a course to a different
    term, so a season mismatch is never the irreducible cause). Each required
    major course's "must be scheduled" lower bound is gated by an enforcement
    literal; the literals are assumed true and the unsat core is decoded back to
    courses. The result is feasibility-EQUIVALENT to the planner's
    ``allow_fixes=True`` solve by construction.
  * It re-derives the planner's EXACT inputs (``engine.load_data`` /
    ``build_model`` / ``_load_ge`` / ``_hard_conflict_pairs`` / ``closure`` /
    ``_cadence``) from the written workbook, so no derivation can drift.
  * A VALIDATION GATE: the full-assumption model MUST be infeasible (matching the
    planner's ``None``); if the mirror cannot reproduce it, the report says so
    honestly rather than inventing a minimal set.

Determinism: ``num_search_workers=1``, ``random_seed=42``, NO wall-clock budget
(feasibility-only over a tiny model is decided instantly), assumptions and the
returned core are sorted by literal index, and the deletion loop iterates in a
fixed order — so the MUS is bit-for-bit reproducible. Runs OUTSIDE ``engine.run``
(advisory only); JSON-serializable.

Honesty: a planning-STRUCTURE diagnostic, NOT a student outcome or prediction.
"""
from __future__ import annotations

from ortools.sat.python import cp_model

import engine

INFEASIBILITY_LABEL = (
    "Infeasibility Explainer: a deterministic STRUCTURAL re-solve (outside "
    "engine.run) that isolates the minimal set of required major courses whose "
    "mandatory scheduling jointly makes a cohort's plan infeasible — relax any one "
    "and a plan exists. A planning-structure diagnostic, NOT a student outcome or a "
    "prediction. It mirrors the planner's hard constraints (prerequisite ordering, "
    "per-term unit cap, time-block conflicts, the term horizon) with GE "
    "requirements held as fixed background; season mismatches are treated as "
    "fixable (the planner can shift a course's term), so they are NOT the cause here."
)

# Cohorts explained, in a fixed (deterministic) order.
_COHORT_ORDER = ("full_time", "part_time")


def _build_model(courses, units, prereqs, hard_conflicts, H, maxu, ge_rows):
    """Mirror solve_cohort's HARD constraints (season-relaxed, no objective/makespan).

    Returns ``(model, req_lits)`` where ``req_lits[course]`` is the enforcement
    literal whose truth makes ``course`` REQUIRED (scheduled exactly once). The
    upper bound (at-most-once) is ungated, so relaxing a literal makes its course
    OPTIONAL — still available as a prerequisite, just not mandatory.
    """
    m = cp_model.CpModel()
    take = {}
    req_lits = {}

    # Major (required) courses: at-most-once always; the >=1 lower bound is GATED.
    for c in courses:
        for t in range(1, H + 1):
            take[(c, t)] = m.NewBoolVar(f"x_{c}_{t}")
        m.AddAtMostOne(take[(c, t)] for t in range(1, H + 1))
        lit = m.NewBoolVar(f"req_{c}")
        req_lits[c] = lit
        m.Add(sum(take[(c, t)] for t in range(1, H + 1)) >= 1).OnlyEnforceIf(lit)

    # Concrete GE candidate courses: at most once (HARD background).
    ge_candidates = sorted({c for r in ge_rows if r["resolution"] == "concrete"
                            for c in r["candidates"]} - set(courses))
    for c in ge_candidates:
        for t in range(1, H + 1):
            take[(c, t)] = m.NewBoolVar(f"g_{c}_{t}")
        m.AddAtMostOne(take[(c, t)] for t in range(1, H + 1))

    # GE reserve pseudo-items: one per required count, scheduled exactly once (HARD).
    reserve_items = []  # (item_id, units)
    for r in sorted(ge_rows, key=lambda x: x["area"]):
        if r["resolution"] != "reserve":
            continue
        for i in range(r["required_count"]):
            iid = f"GE:{r['pattern']}:{r['area']}#{i}"
            reserve_items.append((iid, float(r["units"])))
            for t in range(1, H + 1):
                take[(iid, t)] = m.NewBoolVar(f"r_{iid}_{t}")
            m.AddExactlyOne(take[(iid, t)] for t in range(1, H + 1))

    # Season availability is RELAXED (allow_fixes regime) — deliberately omitted.

    # Prereqs (major courses only; v1 does not expand closure over GE candidates).
    for c in courses:
        for grp in prereqs.get(c, []):
            grp = [p for p in grp if p in courses]
            if not grp:
                continue
            for t in range(1, H + 1):
                m.Add(sum(take[(p, tp)] for p in grp for tp in range(1, t)) >= 1)\
                    .OnlyEnforceIf(take[(c, t)])

    # Time-block hard conflicts: two all-overlapping courses cannot share a term.
    item_ids = set(courses) | set(ge_candidates)
    for pair in (hard_conflicts or ()):
        a, b = tuple(pair)
        if a in item_ids and b in item_ids:
            for t in range(1, H + 1):
                m.Add(take[(a, t)] + take[(b, t)] <= 1)

    # Choose-from-set selection: exactly required_count of an area's candidates.
    for r in ge_rows:
        if r["resolution"] != "concrete":
            continue
        taken = {c: sum(take[(c, t)] for t in range(1, H + 1)) for c in r["candidates"]}
        m.Add(sum(taken.values()) == r["required_count"])

    # Per-term unit cap across major + GE candidates + reserve slots.
    reserve_units = {iid: u for iid, u in reserve_items}
    unit_items = list(courses) + ge_candidates
    for t in range(1, H + 1):
        terms_units = [int(units.get(c, 3)) * take[(c, t)] for c in unit_items]
        terms_units += [int(round(reserve_units[iid])) * take[(iid, t)]
                        for iid, _u in reserve_items]
        m.Add(sum(terms_units) <= maxu)

    return m, req_lits


def _solver():
    s = cp_model.CpSolver()
    s.parameters.num_search_workers = 1   # reproducible CP-SAT output (PRD N11)
    s.parameters.random_seed = 42         # fixed seed; preserves determinism
    # No wall-clock budget: a feasibility-only model over a handful of courses is
    # decided instantly, so the result never depends on machine speed.
    return s


def minimal_conflict_set(*, courses, units, prereqs, hard_conflicts, H, maxu, ge_rows):
    """Isolate the minimal set of required courses whose scheduling is infeasible.

    Returns one of:
      * ``{"feasible": True}`` — the mirror model is satisfiable (nothing to explain;
        if the planner reported this cohort unbuildable, the explainer could not
        reproduce it — the caller surfaces that honestly).
      * ``{"feasible": False, "conflict_set": [..sorted course ids..],
         "background_only": bool}`` — ``conflict_set`` is a true MUS over required
        major courses; ``background_only`` is True when the model is infeasible even
        with NO required major course (the conflict is in the GE/unit background).
      * ``{"unknown": True}`` — the solver returned UNKNOWN (should not happen with
        no time limit); never reported as a conflict.
    """
    # Canonical, deduped order so the MUS is reproducible even for a future direct
    # caller (the production caller already passes ``sorted(closure(...))``).
    courses = sorted(set(courses))

    def build():
        return _build_model(courses, units, prereqs, hard_conflicts, H, maxu, ge_rows)

    m, req = build()
    m.AddAssumptions([req[c] for c in courses])
    solver = _solver()
    st = solver.Solve(m)
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"feasible": True}
    if st != cp_model.INFEASIBLE:
        return {"unknown": True}

    # Decode the sufficient (not-yet-minimal) core off the solver that proved it.
    idx_to_course = {req[c].Index(): c for c in courses}
    core = [idx_to_course[i]
            for i in sorted(solver.SufficientAssumptionsForInfeasibility())
            if i in idx_to_course]

    # Deterministic deletion-based minimization to a TRUE minimal unsatisfiable set.
    essential = []
    cand = sorted(core, key=lambda c: req[c].Index())
    i = 0
    while i < len(cand):
        trial = essential + cand[i + 1:]
        m2, req2 = build()
        m2.AddAssumptions([req2[c] for c in trial])
        if _solver().Solve(m2) == cp_model.INFEASIBLE:
            cand = essential + cand[i + 1:]   # cand[i] not needed; drop it (i stays)
        else:
            essential.append(cand[i])         # cand[i] essential; keep it
            i += 1
    return {"feasible": False, "conflict_set": sorted(essential),
            "background_only": not essential}


def infeasibility_report(out_path, results, *, max_explained=3):
    """Explain why each unbuildable program cohort has no feasible plan (E11).

    Inert when every cohort has a plan. Active when at least one cohort is ``None``
    (truly unbuildable): re-derives the planner's exact model inputs from the
    written workbook and isolates the minimal conflicting set per unbuildable
    cohort (capped at ``max_explained``, with the drop surfaced). Pure +
    deterministic; runs OUTSIDE engine.run.
    """
    label = INFEASIBILITY_LABEL
    progs = (results or {}).get("programs") or {}
    unbuildable = [(pcode, ck)
                   for pcode in sorted(progs)
                   for ck in _COHORT_ORDER
                   if (progs[pcode].get("cohorts") or {}).get(ck, "x") is None]
    if not unbuildable:
        return {"status": "inert", "label": label,
                "reason": "every program cohort has a feasible plan to explain",
                "remedy": ("this fires only when the planner finds NO feasible plan "
                           "for a program cohort")}

    sec, cat, prog = engine.load_data(out_path)
    # Reload reproduces engine.run's EXACT inputs — but only if engine.run used the
    # SAME structured (no-LLM) prereq parse. build_live_workbook calls
    # engine.run(out_path) with no llm, and build_model defaults llm=None here, so
    # prereqs match. A future engine.run(path, llm=...) caller would desync prereqs
    # and (harmlessly) surface reproduced:False rather than a wrong minimal set.
    _active, course_seasons, units, prereqs = engine.build_model(sec, cat, prog)
    ge_rows = engine._load_ge(out_path)
    hard_conflicts = engine._hard_conflict_pairs(sec)
    seasons_present = set().union(*course_seasons.values()) if course_seasons else set()
    cadence = engine._cadence(seasons_present)

    explanations = []
    for pcode, ck in unbuildable[:max_explained]:
        cohort = engine.COHORTS[ck]
        H = int(round((cohort["horizon"] / 2) * len(cadence)))
        maxu = cohort["max_units"]
        courses = sorted(engine.closure(
            list(prog[prog["Program Code"] == pcode]["Course ID"]), prereqs))
        ge_pc = [r for r in (ge_rows or []) if r["program_code"] == pcode]
        res = minimal_conflict_set(courses=courses, units=units, prereqs=prereqs,
                                   hard_conflicts=hard_conflicts, H=H, maxu=maxu,
                                   ge_rows=ge_pc)
        title = progs[pcode].get("title") or pcode
        if res.get("feasible") or res.get("unknown"):
            # The mirror could not reproduce the planner's infeasibility — say so,
            # never invent a minimal set.
            explanations.append({
                "program": title, "cohort": cohort["label"], "horizon_terms": H,
                "reproduced": False,
                "note": ("the planner found no feasible plan, but the structural "
                         "explainer could not reproduce it, so a minimal conflicting "
                         "set is unavailable"),
            })
            continue
        explanations.append({
            "program": title, "cohort": cohort["label"], "horizon_terms": H,
            "reproduced": True,
            "minimal_conflict_set": res["conflict_set"],
            "background_only": res["background_only"],
            "summary": (
                f"these {len(res['conflict_set'])} required course(s) cannot all be "
                f"scheduled within the {H}-term {cohort['label'].lower()} plan; "
                "relaxing any one restores feasibility"
                if not res["background_only"] else
                f"the {cohort['label'].lower()} plan is infeasible from the GE / "
                f"per-term-unit background alone (no single set of major courses is "
                f"the cause)"),
        })

    block = {
        "status": "active", "label": label,
        "explained": explanations,
        "not_assessed": [
            {"check": "season_mismatch_as_cause", "status": "inert",
             "reason": ("season mismatches are treated as fixable (the planner shifts "
                        "a course's term), so they are excluded as an irreducible cause; "
                        "the buildability audit surfaces never-offered courses")},
            {"check": "ge_minimal_set", "status": "inert",
             "reason": ("GE requirements are held as fixed background, not gated, so a "
                        "minimal set over GE areas is not isolated (only flagged as "
                        "background_only when GE/units alone are infeasible)")},
            {"check": "student_completion", "status": "inert",
             "reason": ("a planning-structure diagnostic only; no student-level "
                        "outcome exists in any LACCD source")},
        ],
    }
    if len(unbuildable) > max_explained:
        block["truncated"] = {"unbuildable_cohorts":
                              len(unbuildable) - max_explained}
    return block
