"""Performance / scale tests for the engine (PRD N5 + N6).

PRD §7.2:
  * N5 — full analysis completes in under 30 s for typical LAMC data volumes
          (~1,000 sections x 8 terms).
  * N6 — per-program solve under 10 s.

Fixtures are synthesized at test time by tests/perf_fixture_builder.py (no giant
workbook is committed). Both cases are deterministic.

Selectable subsets:
  * ``pytest tests/test_performance.py -k count``  -> the fixture-scale guard
  * ``pytest tests/test_performance.py -k n6``     -> the per-program N6 case

NEGATIVE-CONTROL NOTE (verified by hand, see the m8-A report):
  Lowering N5_BUDGET_SECONDS to ~0.01 (below the measured ~0.27 s run) makes
  test_n5_full_analysis_under_budget FAIL; restoring it to 30.0 passes. Likewise
  dropping N6_BUDGET_SECONDS below the measured per-program wall clock fails the
  N6 case. The thresholds below are the real PRD bounds, not no-ops.
"""
from __future__ import annotations

import time

import pytest

import engine
from engine import COHORTS, build_model, load_data, solve_cohort
from perf_fixture_builder import build_n5_dataset, build_n6_single_program

# PRD bounds (the documented budgets these tests enforce).
N5_BUDGET_SECONDS = 30.0   # PRD N5: full analysis under 30 s at ~1,000 sections
N6_BUDGET_SECONDS = 10.0   # PRD N6: per-program solve under 10 s

# The fixture must be genuinely at scale, or the budget assertion is vacuous.
N5_MIN_SECTIONS = 900      # ~1,000 sections; guard against a shrunken fixture


@pytest.fixture(scope="module")
def n5_fixture(tmp_path_factory):
    """Build the ~1,000-section, many-program N5 workbook once per module."""
    out = tmp_path_factory.mktemp("perf_n5") / "n5.xlsx"
    return build_n5_dataset(out)


@pytest.fixture(scope="module")
def n6_fixture(tmp_path_factory):
    """Build the single deep-chain N6 workbook once per module."""
    out = tmp_path_factory.mktemp("perf_n6") / "n6.xlsx"
    return build_n6_single_program(out)


# --------------------------------------------------------------------------- #
# N5 — fixture-scale guard (selectable via `-k count`).                        #
# --------------------------------------------------------------------------- #
def test_n5_fixture_is_at_scale_count(n5_fixture):
    """Guard: the built N5 fixture really is ~1,000 sections x 8 terms across
    many programs, so the budget test below can't pass on a tiny set."""
    assert n5_fixture["section_count"] >= N5_MIN_SECTIONS, (
        f"N5 fixture only has {n5_fixture['section_count']} sections "
        f"(need >= {N5_MIN_SECTIONS}); budget test would be vacuous."
    )
    assert n5_fixture["term_count"] == 8, "PRD N5 is specified at 8 terms"
    assert n5_fixture["program_count"] >= 20, (
        "N5 cost scales with program count x 2 cohorts x up-to-2 solves; "
        "the fixture must carry MANY programs."
    )
    # The engine must actually see 8 terms in the data it loads.
    sec, _, _ = load_data(n5_fixture["path"])
    assert sec["Term"].nunique() == 8


# --------------------------------------------------------------------------- #
# N5 — the budget assertion (PRD N5).                                          #
# --------------------------------------------------------------------------- #
def test_n5_full_analysis_under_budget(n5_fixture):
    """PRD N5: engine.run on the ~1,000-section fixture finishes under 30 s.

    Also asserts the run is non-trivial: it must (a) report 8 terms and (b) fire
    the infeasible-then-minfix double-solve on at least one program, proving the
    heavy path ran rather than a degenerate empty solve."""
    section_count = n5_fixture["section_count"]
    assert section_count >= N5_MIN_SECTIONS, (
        f"refusing to assert the budget on a tiny fixture "
        f"({section_count} sections)"
    )

    start = time.perf_counter()
    results = engine.run(n5_fixture["path"])
    elapsed = time.perf_counter() - start

    print(f"\n[N5] engine.run on {section_count} sections x "
          f"{n5_fixture['term_count']} terms, {n5_fixture['program_count']} "
          f"programs: {elapsed:.3f}s (budget {N5_BUDGET_SECONDS}s)")

    assert results["terms_in_data"] == 8
    # the planted infeasible-then-minfix program exercised the double-solve
    minfix_fired = any(
        res and res.get("needs_fix")
        for prog in results["programs"].values()
        for res in prog["cohorts"].values()
    )
    assert minfix_fired, (
        "expected at least one program to need the allow_fixes double-solve "
        "(PROG00 is built Fall-only-infeasible) — the heavy path did not run"
    )
    assert elapsed < N5_BUDGET_SECONDS, (
        f"engine.run took {elapsed:.2f}s on {section_count} sections, "
        f"over the PRD N5 budget of {N5_BUDGET_SECONDS}s"
    )


# --------------------------------------------------------------------------- #
# N6 — per-program worst-case wall clock (selectable via `-k n6`).             #
# --------------------------------------------------------------------------- #
def test_n6_single_program_solve_under_budget(n6_fixture):
    """PRD N6: the worst single-program wall clock is within the 10 s budget.

    The DEEP program is built from deep, all-Fall-only prereq chains so the
    full_time no-fix solve is INFEASIBLE and the engine's allow_fixes
    double-solve fires — the deepest single-program path. We time each cohort
    exactly as engine.run does (no-fix pass, then allow_fixes pass on failure)
    and assert the worst of them is under budget."""
    sec, cat, prog = load_data(n6_fixture["path"])
    active, course_seasons, units, prereqs = build_model(sec, cat, prog)

    pcode = "DEEP"
    worst_cohort_seconds = 0.0
    double_solve_fired = False
    for ck, cohort in COHORTS.items():
        start = time.perf_counter()
        res = solve_cohort(pcode, prog, course_seasons, units, prereqs,
                           cohort, allow_fixes=False)
        if res is None:
            double_solve_fired = True
            res = solve_cohort(pcode, prog, course_seasons, units, prereqs,
                               cohort, allow_fixes=True)
        elapsed = time.perf_counter() - start
        worst_cohort_seconds = max(worst_cohort_seconds, elapsed)

    print(f"\n[N6] worst single-program ({pcode}) cohort wall clock: "
          f"{worst_cohort_seconds:.3f}s over {n6_fixture['course_count']} "
          f"courses (budget {N6_BUDGET_SECONDS}s); "
          f"double_solve_fired={double_solve_fired}")

    assert double_solve_fired, (
        "N6 fixture should force the full_time infeasible-then-minfix "
        "double-solve; if it did not, the deep-chain pattern regressed"
    )
    assert worst_cohort_seconds < N6_BUDGET_SECONDS, (
        f"worst single-program solve {worst_cohort_seconds:.2f}s exceeds the "
        f"PRD N6 budget of {N6_BUDGET_SECONDS}s"
    )


def test_n6_whole_program_run_under_budget(n6_fixture):
    """End-to-end guard: engine.run over the single deep-chain program (both
    cohorts, double-solve included) stays well under the N6 per-program budget.

    This complements the cohort-level timing above with the real run() path."""
    start = time.perf_counter()
    results = engine.run(n6_fixture["path"])
    elapsed = time.perf_counter() - start

    assert "DEEP" in results["programs"]
    ft = results["programs"]["DEEP"]["cohorts"]["full_time"]
    assert ft is not None and ft.get("needs_fix"), (
        "DEEP full_time should solve only via the minfix path"
    )
    assert elapsed < N6_BUDGET_SECONDS, (
        f"engine.run on the single deep-chain program took {elapsed:.2f}s, "
        f"over the PRD N6 budget of {N6_BUDGET_SECONDS}s"
    )
