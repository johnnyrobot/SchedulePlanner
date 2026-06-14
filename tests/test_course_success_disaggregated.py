"""Tests for the DISAGGREGATED CCCCO Data Mart reader (E13).

Reads a long-format disaggregated success export (one row per course/discipline x
demographic subgroup) into a nested map, ENFORCING the published Cal-PASS small-cell
suppression (counts BELOW 10 are suppressed — never shown as a rate). The <10
suppression is load-bearing for BOTH honesty (no false precision) and PRIVACY (no
small-cell re-identification), so an enrollment/count column is REQUIRED.
"""
import pytest

from sources.course_success import load_course_success_disaggregated
from sources.http import SourceDataError


def _csv(tmp_path, text):
    p = tmp_path / "disagg.csv"
    p.write_text(text)
    return str(p)


def test_loads_long_format_by_course_and_subgroup(tmp_path):
    path = _csv(tmp_path,
                "Course,Subgroup,Enrollment,Success Rate\n"
                "MATH 125,All,1000,0.62\n"
                "MATH 125,Group A,400,0.70\n"
                "MATH 125,Group B,300,0.50\n")
    dmap, granularity, supp_min = load_course_success_disaggregated(path)
    assert granularity == "Course" and supp_min == 10
    assert dmap["MATH 125"]["All"]["success_rate"] == pytest.approx(0.62)
    assert dmap["MATH 125"]["Group A"]["success_rate"] == pytest.approx(0.70)
    assert dmap["MATH 125"]["Group B"]["suppressed"] is False


def test_small_cell_below_10_is_suppressed_rate_hidden(tmp_path):
    # A subgroup with count < 10 MUST be suppressed: no rate, no count leaked.
    path = _csv(tmp_path,
                "Course,Subgroup,Enrollment,Success Rate\n"
                "MATH 125,All,1000,0.62\n"
                "MATH 125,Group C,7,0.43\n")
    dmap, _, _ = load_course_success_disaggregated(path)
    cell = dmap["MATH 125"]["Group C"]
    assert cell["suppressed"] is True
    assert cell["success_rate"] is None     # rate hidden
    assert cell["count"] is None            # small count not leaked either


def test_export_suppression_markers_honored(tmp_path):
    # The Data Mart itself suppresses cells with markers like "*" / "<11" / "N/A".
    path = _csv(tmp_path,
                "Course,Subgroup,Enrollment,Success Rate\n"
                "MATH 125,Group A,400,0.70\n"
                "MATH 125,Group D,200,*\n"
                "MATH 125,Group E,200,<11\n")
    dmap, _, _ = load_course_success_disaggregated(path)
    assert dmap["MATH 125"]["Group D"]["suppressed"] is True
    assert dmap["MATH 125"]["Group E"]["suppressed"] is True
    assert dmap["MATH 125"]["Group D"]["success_rate"] is None


def test_requires_count_column_for_suppression(tmp_path):
    # Without a count column we cannot enforce <10 -> refuse (privacy guardrail).
    path = _csv(tmp_path, "Course,Subgroup,Success Rate\nMATH 125,All,0.62\n")
    with pytest.raises(SourceDataError) as exc:
        load_course_success_disaggregated(path)
    assert "count" in str(exc.value).lower() or "enrollment" in str(exc.value).lower()


def test_requires_subgroup_column(tmp_path):
    path = _csv(tmp_path, "Course,Enrollment,Success Rate\nMATH 125,1000,0.62\n")
    with pytest.raises(SourceDataError) as exc:
        load_course_success_disaggregated(path)
    assert "subgroup" in str(exc.value).lower()


def test_configurable_suppression_min(tmp_path):
    path = _csv(tmp_path,
                "Course,Subgroup,Enrollment,Success Rate\n"
                "MATH 125,Group C,15,0.43\n")
    # default 10 -> 15 is shown; raise the bar to 20 -> 15 suppressed
    dmap, _, supp = load_course_success_disaggregated(path, suppression_min=20)
    assert supp == 20
    assert dmap["MATH 125"]["Group C"]["suppressed"] is True


def test_blank_key_or_subgroup_rows_skipped(tmp_path):
    path = _csv(tmp_path,
                "Course,Subgroup,Enrollment,Success Rate\n"
                ",All,1000,0.62\n"
                "MATH 125,,1000,0.62\n"
                "MATH 125,All,1000,0.62\n")
    dmap, _, _ = load_course_success_disaggregated(path)
    assert list(dmap) == ["MATH 125"]
    assert list(dmap["MATH 125"]) == ["All"]
