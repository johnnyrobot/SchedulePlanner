"""Tests for the offline CCCCO Data Mart course-success adapter (E9).

The adapter reads a public, AGGREGATE success/retention export (no student rows)
into a join map, honest about its granularity (course vs TOP/discipline). All
fixtures are written to tmp_path — no real download is required (and the real
shape is only ASSUMED, per the #17 ceiling).
"""
import pytest

from sources.course_success import load_course_success
from sources.http import SourceDataError


def _csv(tmp_path, text):
    p = tmp_path / "success.csv"
    p.write_text(text)
    return str(p)


def test_loads_course_level_export(tmp_path):
    path = _csv(tmp_path,
                "Course,Enrollment,Success Rate,Retention Rate\n"
                "MATH 125,300,55%,82%\n"
                "ENGL 101,500,71%,90%\n")
    success_map, granularity = load_course_success(path)
    assert granularity == "Course"
    assert success_map["MATH 125"]["success_rate"] == pytest.approx(0.55)
    assert success_map["MATH 125"]["retention_rate"] == pytest.approx(0.82)
    assert success_map["MATH 125"]["enrollment"] == 300
    assert success_map["ENGL 101"]["success_rate"] == pytest.approx(0.71)


def test_loads_discipline_level_export_reports_granularity(tmp_path):
    # The Data Mart aggregates by TOP discipline; the adapter discloses that key.
    path = _csv(tmp_path, "TOP Code,Success Rate\n1701.00,0.58\n1501.00,0.66\n")
    success_map, granularity = load_course_success(path)
    assert granularity == "TOP Code"
    assert success_map["1701.00"]["success_rate"] == pytest.approx(0.58)


def test_rate_parsing_accepts_percent_fraction_and_bare_number(tmp_path):
    path = _csv(tmp_path, "Course,Success Rate\nA 1,85%\nB 2,0.6\nC 3,73\n")
    sm, _ = load_course_success(path)
    assert sm["A 1"]["success_rate"] == pytest.approx(0.85)
    assert sm["B 2"]["success_rate"] == pytest.approx(0.60)
    assert sm["C 3"]["success_rate"] == pytest.approx(0.73)   # 73 -> 0.73


def test_missing_optional_columns_yield_none(tmp_path):
    path = _csv(tmp_path, "Course,Success Rate\nMATH 125,0.55\n")
    sm, _ = load_course_success(path)
    assert sm["MATH 125"]["retention_rate"] is None
    assert sm["MATH 125"]["enrollment"] is None


def test_blank_success_cell_is_none_not_zero(tmp_path):
    path = _csv(tmp_path, "Course,Success Rate\nMATH 125,\n")
    sm, _ = load_course_success(path)
    assert sm["MATH 125"]["success_rate"] is None


def test_missing_key_or_success_column_raises_named(tmp_path):
    path = _csv(tmp_path, "Foo,Bar\n1,2\n")
    with pytest.raises(SourceDataError) as exc:
        load_course_success(path)
    assert "CCCCO Data Mart" in str(exc.value)


def test_non_numeric_rate_raises_named(tmp_path):
    path = _csv(tmp_path, "Course,Success Rate\nMATH 125,high\n")
    with pytest.raises(SourceDataError):
        load_course_success(path)


def test_all_blank_keys_raises(tmp_path):
    path = _csv(tmp_path, "Course,Success Rate\n,0.5\n")
    with pytest.raises(SourceDataError):
        load_course_success(path)


def test_key_is_norm_canonicalized(tmp_path):
    path = _csv(tmp_path, "Course,Success Rate\n math  125 ,0.5\n")
    sm, _ = load_course_success(path)
    assert "MATH 125" in sm    # upper + whitespace-collapsed
