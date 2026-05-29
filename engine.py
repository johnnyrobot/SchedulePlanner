"""
engine.py — headless scheduling engine for the desktop app.

Takes one uploaded file (an .xlsx workbook with sheets: sections, catalog,
programs) OR a folder of the three CSVs, and returns JSON-serializable results:
bottleneck analysis + per-program, per-cohort completion plans + fixes.

The schedule is produced by the OR-Tools solver (deterministic). The optional
LLM layer (Gemma 4 via Ollama, see llm_assist.py) only parses messy prerequisite
text and writes explanations — it never decides the schedule.
"""
from __future__ import annotations
import os
import pandas as pd
from ortools.sat.python import cp_model


class InputDataError(ValueError):
    """Raised when load_data receives an unreadable file or schema-invalid data."""


REQUIRED_COLUMNS = {
    "sections": ["Term", "CLASS", "Class Status", "Cap Enrl", "Tot Enrl", "Wait Tot"],
    "catalog":  ["Course ID", "Units", "Prerequisites (structured)"],
    "programs": ["Program Code", "Program Title", "Course ID", "Recommended Semester"],
}

COHORTS = {
    "full_time": {"max_units": 18, "horizon": 4, "label": "Full-time"},
    "part_time": {"max_units": 9,  "horizon": 8, "label": "Part-time"},
}
term_season = lambda t: "Fall" if t % 2 == 1 else "Spring"
season_of_code = lambda t: "Fall" if str(t).endswith("8") else "Spring"


# ----------------------------------------------------------------- data load
def _validate_schema(sec: pd.DataFrame, cat: pd.DataFrame, prog: pd.DataFrame) -> None:
    """Raise InputDataError if any required column is absent from a frame."""
    for sheet, frame, cols in (
        ("sections", sec,  REQUIRED_COLUMNS["sections"]),
        ("catalog",  cat,  REQUIRED_COLUMNS["catalog"]),
        ("programs", prog, REQUIRED_COLUMNS["programs"]),
    ):
        missing = [c for c in cols if c not in frame.columns]
        if missing:
            raise InputDataError(
                f"{sheet} sheet missing required column(s): {missing}"
            )


def load_data(path: str):
    """Accept an .xlsx workbook (3 sheets) or a directory of 3 CSVs."""
    if os.path.isdir(path):
        sec = pd.read_excel(os.path.join(path, "sections.xlsx")) \
            if os.path.exists(os.path.join(path, "sections.xlsx")) \
            else pd.read_csv(os.path.join(path, "sections.csv"))
        cat = pd.read_csv(os.path.join(path, "catalog.csv"))
        prog = pd.read_csv(os.path.join(path, "programs.csv"))
    else:
        try:
            xl = pd.ExcelFile(path)
        except Exception as exc:
            raise InputDataError(
                f"Cannot open '{path}' as an .xlsx workbook. "
                "Input must be an .xlsx workbook (with sheets: sections, catalog, programs) "
                "or a directory containing sections.csv (or sections.xlsx), catalog.csv, "
                "and programs.csv."
            ) from exc
        for sheet in ("sections", "catalog", "programs"):
            if sheet not in xl.sheet_names:
                raise InputDataError(
                    f"Workbook '{path}' is missing required sheet '{sheet}'. "
                    f"Expected sheets: sections, catalog, programs."
                )
        sec  = xl.parse("sections")
        cat  = xl.parse("catalog")
        prog = xl.parse("programs")
    return sec, cat, prog


def parse_prereq(s, llm=None):
    """'(A OR B) AND (C)' -> [['A','B'],['C']].  If text looks unstructured and
    an llm callable is provided, delegate to it."""
    if pd.isna(s) or not str(s).strip():
        return []
    txt = str(s)
    structured = ("(" in txt) or (" AND " in txt) or (" OR " in txt) or \
                 (txt.replace(" ", "").replace("-", "").isalnum() and len(txt.split()) <= 2)
    if not structured and llm is not None:
        return llm(txt)
    return [[c.strip() for c in grp.strip().strip("()").split(" OR ")]
            for grp in txt.split(" AND ")]


def build_model(sec, cat, prog, llm=None):
    active = sec[sec["Class Status"] == "Active"].copy()
    active["season"] = active["Term"].apply(season_of_code)
    course_seasons = active.groupby("CLASS")["season"].agg(lambda s: set(s)).to_dict()
    units = dict(zip(cat["Course ID"], cat["Units"]))
    prereqs = {r["Course ID"]: parse_prereq(r["Prerequisites (structured)"], llm)
               for _, r in cat.iterrows()}
    return active, course_seasons, units, prereqs


def closure(required, prereqs):
    need, stack = set(required), list(required)
    while stack:
        c = stack.pop()
        for grp in prereqs.get(c, []):
            for p in grp:
                if p not in need:
                    need.add(p); stack.append(p)
    return need


# ----------------------------------------------------------------- analysis
def analyze(active, prog, n_terms):
    required = set(prog["Course ID"])
    out = {"rotation_gaps": [], "single_section": [], "modality_mismatch": [],
           "under_supply": []}
    for cid in sorted(required):
        offered = active[active["CLASS"] == cid]["Term"].nunique()
        if offered < n_terms:
            out["rotation_gaps"].append({"course": cid, "offered": int(offered),
                                         "of": int(n_terms)})
        per_term = active[active["CLASS"] == cid].groupby("Term").size()
        if len(per_term) and per_term.min() == 1:
            out["single_section"].append({"course": cid})
        d = active[active["CLASS"] == cid]
        if len(d) and d["Cap Enrl"].sum() > 0:
            fill = d["Tot Enrl"].sum() / d["Cap Enrl"].sum()
            if fill < 0.55:
                out["modality_mismatch"].append({"course": cid,
                                                 "fill_pct": round(fill * 100)})
        wl = active[active["CLASS"] == cid]["Wait Tot"].sum()
        if wl > 15:
            out["under_supply"].append({"course": cid, "waitlisted": int(wl)})
    return out


# ----------------------------------------------------------------- solver
def solve_cohort(pcode, prog, course_seasons, units, prereqs, cohort, allow_fixes):
    H, maxu = cohort["horizon"], cohort["max_units"]
    courses = sorted(closure(list(prog[prog["Program Code"] == pcode]["Course ID"]),
                             prereqs))
    m = cp_model.CpModel()
    take = {(c, t): m.NewBoolVar(f"x_{c}_{t}")
            for c in courses for t in range(1, H + 1)}
    for c in courses:
        m.AddExactlyOne(take[(c, t)] for t in range(1, H + 1))
    fixes_pen = []
    for c in courses:
        avail = course_seasons.get(c, set())
        for t in range(1, H + 1):
            if term_season(t) not in avail:
                if allow_fixes:
                    fixes_pen.append(take[(c, t)])
                else:
                    m.Add(take[(c, t)] == 0)
    for c in courses:
        for grp in prereqs.get(c, []):
            grp = [p for p in grp if p in courses]
            if not grp:
                continue
            for t in range(1, H + 1):
                m.Add(sum(take[(p, tp)] for p in grp for tp in range(1, t)) >= 1)\
                    .OnlyEnforceIf(take[(c, t)])
    for t in range(1, H + 1):
        m.Add(sum(int(units.get(c, 3)) * take[(c, t)] for c in courses) <= maxu)
    last = m.NewIntVar(1, H, "last")
    for c in courses:
        m.Add(last >= sum(t * take[(c, t)] for t in range(1, H + 1)))
    m.Minimize(1000 * sum(fixes_pen) + last if allow_fixes else last)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    solver.parameters.random_seed = 42        # arbitrary fixed value; preserves determinism
    solver.parameters.num_search_workers = 1  # PRD N11: single worker required for reproducible CP-SAT output
    st = solver.Solve(m)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    plan, fixes = {}, []
    for c in courses:
        for t in range(1, H + 1):
            if solver.Value(take[(c, t)]):
                plan.setdefault(t, []).append(c)
                if term_season(t) not in course_seasons.get(c, set()):
                    fixes.append({"course": c, "season": term_season(t)})
    return {"terms_used": int(solver.Value(last)),
            "plan": {int(t): sorted(v) for t, v in sorted(plan.items())},
            "fixes": fixes}


def official_map_issues(pcode, prog, course_seasons, prereqs):
    g = prog[prog["Program Code"] == pcode]
    official = {r["Course ID"]: r["Recommended Semester"]
                for _, r in g.iterrows() if not pd.isna(r["Recommended Semester"])}
    issues = []
    for c, sem in official.items():
        sem = int(sem)
        if term_season(sem) not in course_seasons.get(c, set()):
            issues.append(f"{c} mapped to sem {sem} ({term_season(sem)}) but only "
                          f"offered {sorted(course_seasons.get(c, set())) or 'never'}")
    return issues


# ----------------------------------------------------------------- top level
def run(path: str, llm=None) -> dict:
    sec, cat, prog = load_data(path)
    _validate_schema(sec, cat, prog)
    active, course_seasons, units, prereqs = build_model(sec, cat, prog, llm)
    n_terms = sec["Term"].nunique()

    results = {"terms_in_data": int(n_terms),
               "analysis": analyze(active, prog, n_terms),
               "programs": {}}
    for pcode in prog["Program Code"].unique():
        title = prog[prog["Program Code"] == pcode]["Program Title"].iloc[0]
        entry = {"title": title,
                 "official_map_issues": official_map_issues(pcode, prog,
                                                            course_seasons, prereqs),
                 "cohorts": {}}
        for ck, cohort in COHORTS.items():
            res = solve_cohort(pcode, prog, course_seasons, units, prereqs,
                               cohort, allow_fixes=False)
            if res is None:
                res = solve_cohort(pcode, prog, course_seasons, units, prereqs,
                                   cohort, allow_fixes=True)
                if res:
                    res["needs_fix"] = True
            entry["cohorts"][ck] = res
        results["programs"][pcode] = entry
    return results


def _default_data_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "files", "lamc_data.xlsx")


if __name__ == "__main__":
    import json, sys
    path = sys.argv[1] if len(sys.argv) > 1 else _default_data_path()
    print(json.dumps(run(path), indent=2))
