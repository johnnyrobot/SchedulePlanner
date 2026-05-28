"""
OR-Tools CP-SAT scheduling solver for program completion, by cohort type.

For each program and each cohort profile the solver:
  1. checks whether the published Program Mapper sequence is valid
  2. finds the FASTEST feasible plan (minimize terms to complete) under current
     offerings, respecting prerequisites, course rotation, and the cohort's
     per-term unit cap
  3. if no plan fits the horizon, switches on 'fixes' and reports the minimum
     set of new course offerings that unblock completion

Cohort profiles capture the reality that most community-college students are
part-time and cannot finish a "2-year" program in 2 years regardless of
scheduling. Comparing cohorts shows which problems are scheduling problems and
which are structural load problems.

Reads the same three files as the rest of the pipeline. Offering availability is
DERIVED from the sections data, so this runs unchanged on real exports.
"""
import pandas as pd
from ortools.sat.python import cp_model

SECTIONS = "/home/claude/sections.xlsx"
CATALOG  = "/home/claude/catalog.csv"
PROGRAMS = "/home/claude/programs.csv"

# Cohort profiles. horizon = max terms allowed; max_units = per-term cap.
# Fall start assumed: odd term = Fall, even term = Spring.
COHORTS = {
    "full_time": dict(max_units=18, horizon=4, label="Full-time  (12-18 u/term)"),
    "part_time": dict(max_units=9,  horizon=8, label="Part-time  (6-9 u/term)"),
}

term_season = lambda t: "Fall" if t % 2 == 1 else "Spring"

# ---------------------------------------------------------------- load data
sec  = pd.read_excel(SECTIONS)
cat  = pd.read_csv(CATALOG)
prog = pd.read_csv(PROGRAMS)

active = sec[sec["Class Status"] == "Active"].copy()
season_of = lambda t: "Fall" if str(t).endswith("8") else "Spring"
active["season"] = active["Term"].apply(season_of)
COURSE_SEASONS = active.groupby("CLASS")["season"].agg(lambda s: set(s)).to_dict()
UNITS = dict(zip(cat["Course ID"], cat["Units"]))


def parse_prereq(s):
    if pd.isna(s) or not str(s).strip():
        return []
    return [[c.strip() for c in grp.strip().strip("()").split(" OR ")]
            for grp in str(s).split(" AND ")]

PREREQS = {r["Course ID"]: parse_prereq(r["Prerequisites (structured)"])
           for _, r in cat.iterrows()}


def closure(required):
    need, stack = set(required), list(required)
    while stack:
        c = stack.pop()
        for grp in PREREQS.get(c, []):
            for p in grp:
                if p not in need:
                    need.add(p); stack.append(p)
    return need


def check_official_map(pcode):
    g = prog[prog["Program Code"] == pcode]
    official = {r["Course ID"]: r["Recommended Semester"]
                for _, r in g.iterrows() if not pd.isna(r["Recommended Semester"])}
    v = []
    for c, sem in official.items():
        sem = int(sem)
        if term_season(sem) not in COURSE_SEASONS.get(c, set()):
            v.append(f"{c}: mapped to sem {sem} ({term_season(sem)}), "
                     f"only offered {COURSE_SEASONS.get(c, set()) or 'never'}")
        for grp in PREREQS.get(c, []):
            grp = [p for p in grp if p in official]
            if grp and not any(official.get(p, 99) < sem for p in grp):
                v.append(f"{c}: prereq {' or '.join(grp)} not before sem {sem}")
    return v


def solve_cohort(pcode, cohort_key, allow_fixes):
    """Return (status, plan, fixes, terms_used) for one program+cohort."""
    cohort = COHORTS[cohort_key]
    H, maxu = cohort["horizon"], cohort["max_units"]
    courses = sorted(closure(list(prog[prog["Program Code"] == pcode]["Course ID"])))

    m = cp_model.CpModel()
    take = {(c, t): m.NewBoolVar(f"x_{c}_{t}")
            for c in courses for t in range(1, H + 1)}
    for c in courses:
        m.AddExactlyOne(take[(c, t)] for t in range(1, H + 1))

    fix_terms = []
    for c in courses:
        avail = COURSE_SEASONS.get(c, set())
        for t in range(1, H + 1):
            if term_season(t) not in avail:
                if allow_fixes:
                    fix_terms.append(take[(c, t)])
                else:
                    m.Add(take[(c, t)] == 0)

    for c in courses:
        for grp in PREREQS.get(c, []):
            grp = [p for p in grp if p in courses]
            if not grp:
                continue
            for t in range(1, H + 1):
                earlier = [take[(p, tp)] for p in grp for tp in range(1, t)]
                m.Add(sum(earlier) >= 1).OnlyEnforceIf(take[(c, t)])

    for t in range(1, H + 1):
        m.Add(sum(int(UNITS.get(c, 3)) * take[(c, t)] for c in courses) <= maxu)

    last = m.NewIntVar(1, H, "last")
    for c in courses:
        m.Add(last >= sum(t * take[(c, t)] for t in range(1, H + 1)))
    if allow_fixes:
        m.Minimize(1000 * sum(fix_terms) + last)   # fixes dominate, then speed
    else:
        m.Minimize(last)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return "infeasible", None, None, None

    plan = {t: [] for t in range(1, H + 1)}
    fixes = []
    for c in courses:
        for t in range(1, H + 1):
            if solver.Value(take[(c, t)]):
                plan[t].append(c)
                if term_season(t) not in COURSE_SEASONS.get(c, set()):
                    fixes.append((c, term_season(t)))
    terms_used = solver.Value(last)
    return "ok", plan, fixes, terms_used


def report(pcode):
    title = prog[prog["Program Code"] == pcode]["Program Title"].iloc[0]
    print(f"\n{'='*66}\n{pcode} — {title}\n{'='*66}")

    viol = check_official_map(pcode)
    if viol:
        print(f"  Published map: BROKEN ({len(viol)})")
        for x in viol:
            print(f"     ! {x}")
    else:
        print("  Published map: valid")

    for ck in COHORTS:
        label = COHORTS[ck]["label"]
        st, plan, fixes, used = solve_cohort(pcode, ck, allow_fixes=False)
        if st == "ok":
            yrs = (used + 1) // 2
            note = " (best case under current offerings)"
            print(f"\n  {label}: completes in {used} terms (~{yrs} yr){note}")
            for t in sorted(plan):
                if plan[t]:
                    u = sum(int(UNITS.get(c, 3)) for c in plan[t])
                    print(f"     T{t} {term_season(t):6s} [{u:2d}u] {', '.join(sorted(plan[t]))}")
        else:
            # retry with fixes
            st2, plan2, fixes2, used2 = solve_cohort(pcode, ck, allow_fixes=True)
            print(f"\n  {label}: NOT possible under current offerings within "
                  f"{COHORTS[ck]['horizon']} terms")
            if st2 == "ok" and fixes2:
                uniq = sorted(set(fixes2))
                print(f"     Minimum fix to make it work ({len(uniq)}):")
                for c, s in uniq:
                    print(f"        + add {c} section in {s} "
                          f"(now only {COURSE_SEASONS.get(c, set())})")


if __name__ == "__main__":
    print("COMPLETION FEASIBILITY BY COHORT  (synthetic demo data)")
    for p in prog["Program Code"].unique():
        report(p)
    print(f"\n{'='*66}")
    print("Read: full-time vs part-time time-to-complete, and which programs")
    print("need a schedule change vs. just more terms.")
    print('='*66)
