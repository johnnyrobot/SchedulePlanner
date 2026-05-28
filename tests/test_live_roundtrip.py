import pytest

import engine
from sources import mapping

SECTIONS = [
    {"term": 2268, "course": "CS 101", "units": "3.00"},
    {"term": 2264, "course": "MATH 245", "units": "5.00"},
    {"term": 2268, "course": "MATH 245", "units": "5.00"},
]
PROGRAM = {
    "code": "COMPUTER-SCIENCE", "title": "Computer Science", "award": "AS-T",
    "ge_pattern": "", "courses": [
        {"course_id": "CS 101", "title": "Intro", "recommended_semester": 1, "units": 3.0},
        {"course_id": "MATH 245", "title": "Calc I", "recommended_semester": 2, "units": 5.0},
    ],
}


def test_mapped_workbook_runs_through_engine(tmp_path):
    out = tmp_path / "live.xlsx"
    mapping.write_workbook(SECTIONS, PROGRAM, str(out))

    results = engine.run(str(out))

    # data summary
    assert results["terms_in_data"] == 2
    # analysis shape is stable — engine always emits all four diagnostic keys
    assert set(results["analysis"]) == {
        "rotation_gaps", "single_section", "modality_mismatch", "under_supply"}
    # CS 101 offered in only 1 of 2 terms -> a rotation gap is surfaced
    assert any(g["course"] == "CS 101" for g in results["analysis"]["rotation_gaps"])
    # enrollment-driven detectors are inert (Cap/Tot/Wait = 0)
    assert results["analysis"]["modality_mismatch"] == []
    assert results["analysis"]["under_supply"] == []
    # program solved for the full-time cohort
    prog = results["programs"]["COMPUTER-SCIENCE"]
    assert prog["title"] == "Computer Science"
    full_time = prog["cohorts"]["full_time"]
    assert full_time is not None
    assert full_time["terms_used"] == 1
    assert full_time["plan"] == {1: ["CS 101", "MATH 245"]}
    # part-time cohort is also feasible (8u < 9u cap)
    assert prog["cohorts"]["part_time"] is not None


@pytest.mark.live
def test_live_lamc_end_to_end(tmp_path):
    """Hits the real LACCD APIs. Run with: pytest -m live"""
    import build_live_workbook
    out = tmp_path / "live_real.xlsx"
    sections, program = build_live_workbook.build("LAMC", [2268], "Biology")
    assert len(sections) > 0
    if program is None:
        pytest.fail("Program mapper returned None for 'Biology' at LAMC — API may have changed")
    mapping.write_workbook(sections, program, str(out))
    results = engine.run(str(out))
    assert results["terms_in_data"] >= 1
