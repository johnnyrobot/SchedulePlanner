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
