"""Regression tests for the engine.py code-review remediation.

  * official_map_issues checks recommended semesters against the DATA-DERIVED
    cadence (Summer/Winter aware), not the static Fall/Spring default — while
    staying byte-identical on Fall/Spring-only data.
  * _load_ge converts a malformed ge_requirements row into a contextual
    InputDataError instead of a raw KeyError/ValueError.
"""
import pandas as pd
import pytest

import engine


def _prog():
    # One program, one course recommended for the 3rd planning semester.
    return pd.DataFrame([
        {"Program Code": "P", "Program Title": "P", "Course ID": "X 1",
         "Recommended Semester": 3},
    ])


def test_official_map_issues_default_cadence_unchanged():
    # Under the legacy 2-season cadence, semester 3 -> Fall; a Spring-only course
    # is therefore (legacy behavior) flagged as mapped to a season it isn't offered.
    issues = engine.official_map_issues("P", _prog(), {"X 1": {"Spring"}}, {})
    assert issues and "X 1" in issues[0]


def test_official_map_issues_uses_data_cadence_for_summer_winter():
    # With a 4-season cadence, semester 3 -> Spring, so the SAME Spring-only course
    # is correctly NOT flagged (the bug: it was checked against the static default).
    four = ["Fall", "Winter", "Spring", "Summer"]
    issues = engine.official_map_issues("P", _prog(), {"X 1": {"Spring"}}, {}, four)
    assert issues == []


def test_run_official_map_issues_golden_safe_on_demo():
    # The committed demo is Fall/Spring-only, so the derived cadence is the default
    # and the AS-T-CSCI official-map violations are still detected (unchanged).
    prog = engine.run(engine._default_data_path())["programs"]["AS-T-CSCI"]
    assert prog["official_map_issues"]


def test_load_ge_malformed_row_raises_contextual_error(tmp_path):
    (tmp_path / "ge_requirements.csv").write_text(
        "Program Code,Area,Required Count\nP,4,notanumber\n")
    with pytest.raises(engine.InputDataError, match="ge_requirements row 0"):
        engine._load_ge(str(tmp_path))


def test_load_ge_valid_row_still_parses(tmp_path):
    (tmp_path / "ge_requirements.csv").write_text(
        "Program Code,Area,Area Title,Required Count,Resolution,Units\n"
        "P,4,Humanities,1,reserve,3\n")
    rows = engine._load_ge(str(tmp_path))
    assert len(rows) == 1
    assert rows[0]["program_code"] == "P" and rows[0]["required_count"] == 1
