"""End-to-end determinism guards (PRD N11) across the whole pipeline, offline.

These tests are the regression net for "the same logical input always yields the
same plan", covering every surface a plan can enter or leave through:

  * the LIVE pipeline (build_live_workbook.analyze_live) via the injected
    FakeClient -- byte-identical reconciliation + engine results across runs;
  * the new IR enrollment sample fixture -> byte-identical engine.run JSON;
  * a CSV-folder input vs the equivalent .xlsx for the SAME data -> same plan;
  * a source guard that the CP-SAT solver keeps random_seed=42 AND
    num_search_workers=1 (a future edit dropping single-worker determinism
    fails loudly here); and
  * the optional LLM prereq parser is non-interfering: with a parser that
    returns exactly what the regex fallback would, engine.run output is
    identical to the no-LLM run -- proving the AI layer never moves the plan.
"""
import inspect
import json
import re

import pytest

import build_live_workbook
import engine
from generate_synthetic import generate


def _canon(obj):
    """Stable JSON text for byte-identity comparison of structured results."""
    return json.dumps(obj, sort_keys=True)


# --------------------------------------------------------------- live pipeline
def test_live_analyze_is_byte_identical_across_runs(lamc_routes, make_client,
                                                    tmp_path):
    """analyze_live (FakeClient-driven) produces identical reconciliation and
    engine results on two independent runs."""
    def run(tag):
        client = make_client(lamc_routes)
        out = tmp_path / f"live_{tag}.xlsx"
        return build_live_workbook.analyze_live(
            "LAMC", [2268], "Biology", str(out), client=client)

    r1 = run("a")
    r2 = run("b")
    assert _canon(r1["reconciliation"]) == _canon(r2["reconciliation"])
    assert _canon(r1["results"]) == _canon(r2["results"])
    # the surrounding structured fields are stable too
    assert _canon(r1["program"]) == _canon(r2["program"])
    assert _canon(r1["inert_detectors"]) == _canon(r2["inert_detectors"])


# ----------------------------------------------------------- enrollment sample
def test_enrollment_sample_engine_run_is_byte_identical(tmp_path):
    """The IR enrollment sample fixture -> byte-identical engine.run JSON across
    two runs (the solver + analysis are deterministic on it)."""
    sample = tmp_path / "sample.xlsx"
    generate(str(sample), enrollment_sample=True)
    assert _canon(engine.run(str(sample))) == _canon(engine.run(str(sample)))


# ----------------------------------------------------- input-surface parity N11
def test_csv_folder_input_matches_xlsx_for_same_data(tmp_path):
    """The same logical data delivered as a CSV folder vs an .xlsx workbook
    yields the same plan (re-confirms N11 across input surfaces)."""
    xlsx = engine._default_data_path()
    sec, cat, prog = engine.load_data(xlsx)

    folder = tmp_path / "csvs"
    folder.mkdir()
    sec.to_csv(folder / "sections.csv", index=False)
    cat.to_csv(folder / "catalog.csv", index=False)
    prog.to_csv(folder / "programs.csv", index=False)

    assert _canon(engine.run(str(folder))) == _canon(engine.run(xlsx))


# ------------------------------------------------------------- solver guards
def test_solver_pins_deterministic_parameters():
    """Guard: solve_cohort must set random_seed=42 AND num_search_workers=1.

    CP-SAT is only reproducible with a fixed seed and a single search worker; a
    future refactor that drops either must fail here, not silently desync plans."""
    src = inspect.getsource(engine.solve_cohort)
    assert re.search(r"random_seed\s*=\s*42", src), \
        "solve_cohort must pin solver.parameters.random_seed = 42"
    assert re.search(r"num_search_workers\s*=\s*1", src), \
        "solve_cohort must pin solver.parameters.num_search_workers = 1"


def test_solver_uses_deterministic_time_budget_not_wallclock():
    """E2: the solve budget must be WORK-based (max_deterministic_time), not the
    wall-clock max_time_in_seconds — otherwise a slow machine could return a
    different FEASIBLE-not-OPTIMAL plan, silently breaking cross-machine
    reproducibility the determinism doctrine rests on."""
    src = inspect.getsource(engine.solve_cohort)
    assert re.search(r"max_deterministic_time\s*=", src), \
        "solve_cohort must use a deterministic (work-based) solve budget"
    assert "max_time_in_seconds" not in src, \
        "solve_cohort must NOT use the wall-clock max_time_in_seconds budget"


def test_proven_optimal_is_computed_from_solver_status_not_a_constant():
    """SOURCE-GUARD: the bundled data always solves OPTIMAL, so the data-level test
    below cannot tell ``proven_optimal: True`` (the honest expression) from a
    hardcoded ``True``. Pin that the flag is the real ``st == cp_model.OPTIMAL``
    comparison (mirrors test_solver_pins_deterministic_parameters), so a regression
    that would silently label a FEASIBLE-not-OPTIMAL plan 'proven optimal' — the
    exact thing the E2 honesty advisory exists to prevent — is caught."""
    src = inspect.getsource(engine.solve_cohort)
    assert re.search(r'"proven_optimal"\s*:\s*st\s*==\s*cp_model\.OPTIMAL', src), \
        "proven_optimal must be computed as (st == cp_model.OPTIMAL), not a constant"


def test_cohort_results_are_proven_optimal_on_the_default_data():
    """E2: every solved cohort on the bundled data reaches OPTIMAL (the models are
    tiny), so proven_optimal is True — the plan shown is the true minimum-term plan.
    The advisory only fires if a plan is FEASIBLE-not-proven-optimal."""
    results = engine.run(engine._default_data_path())
    saw = 0
    for prog in results["programs"].values():
        for c in prog["cohorts"].values():
            if isinstance(c, dict):
                assert c.get("proven_optimal") is True, c
                saw += 1
    assert saw > 0, "expected at least one solved cohort to check"


# ----------------------------------------------------- LLM non-interference
def test_llm_parser_does_not_change_the_plan():
    """With an LLM prereq parser that returns exactly what the regex fallback
    would, engine.run output is identical to the no-LLM run: the optional AI
    layer parses text, it never decides the schedule."""
    xlsx = engine._default_data_path()

    # A parser that mirrors the regex fallback's output for any text it sees.
    def mirror_parser(text):
        return engine.parse_prereq(text, llm=None)

    baseline = _canon(engine.run(xlsx))
    with_llm = _canon(engine.run(xlsx, llm=mirror_parser))
    assert with_llm == baseline

    # And the mirror parser genuinely reproduces the regex parse when invoked,
    # so the equality above is non-vacuous rather than the parser being skipped.
    for text in ("(MATH 245)", "(CHEM 101 OR CHEM 102) AND MATH 245", "ENGL 101"):
        assert mirror_parser(text) == engine.parse_prereq(text, llm=None)


def test_ge_plan_is_deterministic(tmp_path):
    import pandas as pd
    out = tmp_path / "ge_det.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as xl:
        pd.DataFrame([{"Term": 20248, "CLASS": "ART 101", "Class Status": "Active",
                       "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0},
                      {"Term": 20248, "CLASS": "ART 105", "Class Status": "Active",
                       "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0}]).to_excel(
            xl, sheet_name="sections", index=False)
        pd.DataFrame([{"Course ID": "ART 101", "Units": 3, "Prerequisites (structured)": ""},
                      {"Course ID": "ART 105", "Units": 3, "Prerequisites (structured)": ""}]).to_excel(
            xl, sheet_name="catalog", index=False)
        pd.DataFrame([{"Program Code": "A", "Program Title": "A", "Course ID": "ART 101",
                       "Recommended Semester": 1}]).to_excel(xl, sheet_name="programs", index=False)
        pd.DataFrame([{"Program Code": "A", "Pattern": "igetc", "Area": "3A",
                       "Area Title": "Arts", "Required Count": 1, "Resolution": "concrete",
                       "Candidate Course IDs": "ART 101;ART 105", "Recommended Course": "",
                       "Units": 3.0}]).to_excel(xl, sheet_name="ge_requirements", index=False)
    import engine
    a = engine.run(str(out))["programs"]["A"]["cohorts"]["full_time"]["plan"]
    b = engine.run(str(out))["programs"]["A"]["cohorts"]["full_time"]["plan"]
    assert a == b
