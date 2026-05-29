"""Tests for the live-shaped IR enrollment sample (files/lamc_sample_enrollment.xlsx).

The bundled demo (files/lamc_data.xlsx) already fires every detector, so its
value is breadth. THIS fixture's distinct value is shape + causation:

  * its sections sheet mirrors the real IR PeopleSoft column layout with
    POPULATED Cap Enrl / Tot Enrl / Wait Tot and instructor PII ABSENT, so the
    ingestion path is exercised against the production layout before real IR
    data lands; and
  * its enrollment-driven detector hits are PLANTED and snapshot-pinned, and we
    prove they are *caused* by those non-zero counts (zero them in memory and
    the hits collapse) -- distinguishing real demand signal from the demo's
    incidental noise.
"""
import pathlib

import pandas as pd
import pytest

import engine
from generate_synthetic import SAMPLE_BOTTLENECKS, generate

REPO = pathlib.Path(__file__).resolve().parent.parent
SAMPLE = REPO / "files" / "lamc_sample_enrollment.xlsx"

# Instructor / personally-identifying column names that MUST NOT appear in an
# IR enrollment export shared for scheduling analysis.
_PII_NAMES = {
    "instructor", "instructor name", "instructor_name", "faculty", "faculty name",
    "emplid", "email", "first name", "last name", "name", "student id", "sid",
}


@pytest.fixture(scope="module")
def sample_frames():
    return engine.load_data(str(SAMPLE))


def test_sample_exists_and_is_tracked():
    assert SAMPLE.exists(), "run: python3 generate_synthetic.py --enrollment-sample"


def test_sample_has_ir_shape_populated_counts_and_no_pii(sample_frames):
    sec, _cat, _prog = sample_frames
    # IR enrollment columns present AND populated (the whole point of this fixture)
    for col in ("Cap Enrl", "Tot Enrl", "Wait Tot"):
        assert col in sec.columns, f"missing IR column {col!r}"
        assert sec[col].sum() > 0, f"{col} should be non-zero across the sample"
    # multi-term: Fall (2248) + Spring (2252)
    assert set(int(t) for t in sec["Term"].unique()) == {2248, 2252}
    # instructor PII columns are absent
    present_pii = [c for c in sec.columns if c.strip().lower() in _PII_NAMES]
    assert present_pii == [], f"PII columns must be absent, found {present_pii}"


def test_planted_bottlenecks_match_snapshot(sample_frames):
    """Pin WHICH course fires each detector and its approximate magnitude, so a
    future tweak to the generator cannot silently move the planted bottleneck."""
    sec, cat, prog = sample_frames
    active, _cs, _u, _p = engine.build_model(sec, cat, prog)
    result = engine.analyze(active, prog, sec["Term"].nunique())

    mm = result["modality_mismatch"]
    us = result["under_supply"]
    # exactly one planted hit per enrollment-driven detector (no incidental noise)
    assert len(mm) == 1, f"expected exactly one modality_mismatch, got {mm}"
    assert len(us) == 1, f"expected exactly one under_supply, got {us}"

    exp_mm = SAMPLE_BOTTLENECKS["modality_mismatch"]
    exp_us = SAMPLE_BOTTLENECKS["under_supply"]
    assert mm[0]["course"] == exp_mm["course"] == "ACCTG 2"
    assert us[0]["course"] == exp_us["course"] == "ENGL 101"
    # snapshot the magnitudes (low fill ~35%, chronic waitlist 60 across the run)
    assert mm[0]["fill_pct"] == exp_mm["fill_pct"] == 35
    assert mm[0]["fill_pct"] < 55  # below the modality_mismatch threshold
    assert us[0]["waitlisted"] == exp_us["waitlisted"] == 60
    assert us[0]["waitlisted"] > 15  # above the under_supply threshold

    # the non-enrollment detectors stay quiet so causation is unambiguous
    assert result["rotation_gaps"] == []
    assert result["single_section"] == []


def test_nonzero_counts_DRIVE_the_detectors(sample_frames):
    """Causation, not correlation: zero Cap/Tot/Wait in memory and the two
    enrollment-driven detector lists collapse -- proving the populated IR counts
    (not the schedule shape) are what fire them. This is what distinguishes the
    sample from the demo, whose hits also depend on rotation/section structure."""
    sec, cat, prog = sample_frames
    n_terms = sec["Term"].nunique()

    base = engine.analyze(engine.build_model(sec, cat, prog)[0], prog, n_terms)
    assert base["modality_mismatch"] and base["under_supply"]

    zeroed = sec.copy()
    zeroed[["Cap Enrl", "Tot Enrl", "Wait Tot"]] = 0
    after = engine.analyze(engine.build_model(zeroed, cat, prog)[0], prog, n_terms)

    assert after["modality_mismatch"] == [], "modality_mismatch must be count-driven"
    assert after["under_supply"] == [], "under_supply must be count-driven"


def test_every_program_cohort_is_feasible():
    """No cohort returns None even after the allow_fixes retry: the sample's
    offerings cover every program closure in both seasons, so the solver always
    produces a plan (a None here would mean an infeasible, unusable fixture)."""
    res = engine.run(str(SAMPLE))
    for pcode, prog in res["programs"].items():
        for ck, cohort in prog["cohorts"].items():
            assert cohort is not None, f"{pcode}/{ck} is infeasible in the sample"
            assert cohort["plan"], f"{pcode}/{ck} produced an empty plan"


def test_sample_generation_is_byte_identical(tmp_path):
    """Two generations of the sample are byte-for-byte identical (determinism)."""
    a = tmp_path / "a.xlsx"
    b = tmp_path / "b.xlsx"
    generate(str(a), enrollment_sample=True)
    generate(str(b), enrollment_sample=True)
    assert a.read_bytes() == b.read_bytes()
    # and the committed fixture matches a fresh generation (it is up to date)
    assert SAMPLE.read_bytes() == a.read_bytes(), (
        "files/lamc_sample_enrollment.xlsx is stale; regenerate with "
        "python3 generate_synthetic.py --enrollment-sample")


def test_demo_generation_is_byte_identical(tmp_path):
    """Two DEMO-mode generations are byte-for-byte identical.

    This exercises the random.Random(seed) isolation in the demo RNG path
    (the sample mode draws no randomness, so only this test guards that the
    seeded local RNG -- not the global one -- and the frozen timestamp keep the
    demo workbook reproducible)."""
    a = tmp_path / "demo_a.xlsx"
    b = tmp_path / "demo_b.xlsx"
    generate(str(a), enrollment_sample=False)
    generate(str(b), enrollment_sample=False)
    assert a.read_bytes() == b.read_bytes()
