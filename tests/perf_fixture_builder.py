"""Deterministic perf-fixture synthesis for the m8-A scale tests (PRD N5/N6).

These builders write *throwaway* workbooks into a caller-supplied tmp path at
test time. Nothing large is committed; the fixtures are regenerated on every
run from a fixed structure (no RNG), so they are byte-stable.

Design note — what actually drives engine.run's wall clock:
    engine.run solves, for EVERY program, BOTH cohorts (full_time + part_time),
    and for any cohort that is infeasible under current offerings it solves a
    SECOND time with allow_fixes=True. So the cost scales with

        programs x 2 cohorts x (1 or 2 solve passes)

    not with the raw section count. A realistic N5 fixture therefore needs MANY
    programs and at least one program that is infeasible-then-minfix (so the
    double-solve path is exercised under load), in addition to ~1,000 sections.

Term scheme matches the real data: a term code ending in '8' is Fall, ending in
'2' is Spring (see engine.season_of_code). We use the same 8 terms the bundled
demo uses (4 Fall + 4 Spring) so engine sees terms_in_data == 8.
"""
from __future__ import annotations

import pandas as pd

# Eight terms: 4 Fall (codes end in 8) + 4 Spring (codes end in 2), matching the
# bundled demo's term span so terms_in_data == 8 (PRD N5 "x 8 terms").
TERMS = [2228, 2232, 2238, 2242, 2248, 2252, 2258, 2262]


def _is_fall(term: int) -> bool:
    return str(term).endswith("8")


def _write_workbook(out_path, sec_rows, cat_rows, prog_rows):
    sec = pd.DataFrame(sec_rows)
    cat = pd.DataFrame(cat_rows)
    prog = pd.DataFrame(prog_rows)
    with pd.ExcelWriter(out_path) as xl:
        sec.to_excel(xl, sheet_name="sections", index=False)
        cat.to_excel(xl, sheet_name="catalog", index=False)
        prog.to_excel(xl, sheet_name="programs", index=False)
    return len(sec)


def build_n5_dataset(out_path, *, n_programs: int = 22, courses_per_program: int = 6) -> dict:
    """Synthesize an N5-scale dataset (~1,000 sections x 8 terms, MANY programs).

    Program 0 (``PROG00``) is the deliberate INFEASIBLE-then-minfix program: its
    first three courses form a Fall-only prerequisite chain. In the full_time
    4-term horizon (Fall, Spring, Fall, Spring) a 3-deep Fall-only chain cannot
    be ordered without placing a course in a Spring it isn't offered in, so the
    first (no-fix) solve returns None and the engine fires the allow_fixes
    double-solve. That guarantees the heaviest solve path runs under load.

    Defaults land at ~1,044 sections (PRD N5 "~1,000 sections"). Returns a small
    manifest dict (path, section_count, program_count, term_count) so the test
    can assert the fixture is genuinely at scale and can't pass vacuously.
    """
    sec_rows, cat_rows, prog_rows = [], [], []
    seen_courses = set()

    for pidx in range(n_programs):
        pcode = f"PROG{pidx:02d}"
        infeasible = (pidx == 0)
        for cidx in range(courses_per_program):
            cid = f"P{pidx:02d}C{cidx:02d}"
            # A short prereq chain on the first three courses of each program
            # (so the solver does real ordering work, not just bin-packing).
            prereq = f"(P{pidx:02d}C{cidx - 1:02d})" if 0 < cidx < 3 else ""
            if cid not in seen_courses:
                cat_rows.append({
                    "Course ID": cid, "Units": 3,
                    "Prerequisites (structured)": prereq,
                })
                seen_courses.add(cid)
            prog_rows.append({
                "Program Code": pcode, "Program Title": f"Program {pidx}",
                "Course ID": cid, "Recommended Semester": (cidx % 4) + 1,
            })
            for term in TERMS:
                # The infeasible program's chain head is Fall-only -> minfix.
                if infeasible and cidx < 3 and not _is_fall(term):
                    continue
                sec_rows.append({
                    "Term": term, "CLASS": cid, "Class Status": "Active",
                    "Cap Enrl": 30, "Tot Enrl": 10, "Wait Tot": 0,
                })

    n_sections = _write_workbook(out_path, sec_rows, cat_rows, prog_rows)
    return {
        "path": str(out_path),
        "section_count": n_sections,
        "program_count": n_programs,
        "term_count": len(TERMS),
        "infeasible_program": "PROG00",
    }


def build_n6_single_program(out_path, *, n_chains: int = 6, depth: int = 3) -> dict:
    """Synthesize a SINGLE-program fixture with deep, all-Fall-only prereq chains.

    This is the per-program worst-case (PRD N6: per-program solve < 10s). The
    program ``DEEP`` is built from ``n_chains`` independent prerequisite chains,
    each ``depth`` courses long, EVERY course offered Fall-only. In the 4-term
    full_time horizon every chain of depth >= 2 forces at least one Spring fix,
    so the first (no-fix) full_time solve returns None and the engine runs the
    allow_fixes double-solve — the deepest, slowest single-program path.

    The defaults (6 chains x depth 3 = 18 courses) keep the program
    feasible-WITH-fixes: the no-fix solve fails and the allow_fixes solve
    succeeds, which is exactly the infeasible-then-minfix pattern N6 documents.
    (Stacking too many Fall-only chains would overflow the per-term unit cap and
    make the program infeasible even with fixes, which is NOT what N6 measures.)
    Returns a manifest dict.
    """
    sec_rows, cat_rows, prog_rows = [], [], []
    for ch in range(n_chains):
        for i in range(1, depth + 1):
            cid = f"C{ch:02d}_{i}"
            prereq = f"(C{ch:02d}_{i - 1})" if i > 1 else ""
            cat_rows.append({
                "Course ID": cid, "Units": 3,
                "Prerequisites (structured)": prereq,
            })
            prog_rows.append({
                "Program Code": "DEEP", "Program Title": "Deep Chain",
                "Course ID": cid, "Recommended Semester": ((i - 1) % 4) + 1,
            })
            # Fall-only: only the two Fall terms in the span carry sections.
            for term in (2248, 2258):
                sec_rows.append({
                    "Term": term, "CLASS": cid, "Class Status": "Active",
                    "Cap Enrl": 30, "Tot Enrl": 10, "Wait Tot": 0,
                })

    n_sections = _write_workbook(out_path, sec_rows, cat_rows, prog_rows)
    return {
        "path": str(out_path),
        "section_count": n_sections,
        "program_count": 1,
        "course_count": n_chains * depth,
        "n_chains": n_chains,
        "depth": depth,
    }
