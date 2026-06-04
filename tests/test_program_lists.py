"""Tests for the multi-program requirements reader: sources.program_lists.

OFFLINE: pure file reads, no network. The committed sample
(``files/lamc_program_lists_sample.xlsx``) uses real public LACCD plan/course
codes (a Program Course Lists export carries no PII — it is plan structure, not
people). ``_build_sample`` is the single source of truth for the fixture's
contents; ``test_committed_fixture_matches_builder`` guards it from going stale
by comparing LOGICAL contents (not raw bytes — see the lock/fixture lesson).
"""
import pathlib

import pandas as pd
import pytest

from sources import program_lists
from sources.http import SourceDataError
from sources.mapping import _norm

REPO = pathlib.Path(__file__).resolve().parent.parent
SAMPLE = str(REPO / "files" / "lamc_program_lists_sample.xlsx")

# (Plan Code, Program, "Type of  List", Class Selection Criteria, Course, Notes)
# MATH 227 required by 4 plans; CHEM 101 by 2; MATH 261 by 2. PHYSICS 006/037 are
# a "Select 1" list (choice, not required). BIOLOGY 003 keeps its leading zero.
# MATH 265 sits under "Upper Division Coursework (Required)" (substring "required").
SAMPLE_ROWS = [
    ("M0501", "Biology AS-T", "Required", "Required", "BIOLOGY 003", ""),
    ("M0501", "Biology AS-T", "Required", "Required", "CHEM 101", ""),
    ("M0501", "Biology AS-T", "Required", "Required", "MATH 227", ""),
    ("M0501", "Biology AS-T", "List A", "Select 1", "PHYSICS 006", ""),
    ("M0501", "Biology AS-T", "List A", "Select 1", "PHYSICS 037", ""),
    ("M0502", "Chemistry AS-T", "Required", "Required", "CHEM 101", ""),
    ("M0502", "Chemistry AS-T", "Required", "Required", "MATH 227", ""),
    ("M0502", "Chemistry AS-T", "Required", "Required", "MATH 261", ""),
    ("M0502", "Chemistry AS-T", "Elective", "Select 1", "CHEM 102", ""),
    ("M0503", "Psychology AA-T", "Required", "Required", "PSYCH 001", ""),
    ("M0503", "Psychology AA-T", "Required", "Required", "MATH 227", ""),
    ("M0503", "Psychology AA-T", "List B", "Select 1", "SOC 001", ""),
    ("M0503", "Psychology AA-T", "List B", "Select 1", "ANTHRO 101", ""),
    ("M0504", "Mathematics AS-T", "Required", "Required", "MATH 227", ""),
    ("M0504", "Mathematics AS-T", "Required", "Required", "MATH 261", ""),
    ("M0504", "Mathematics AS-T", "Required", "Required", "MATH 262", ""),
    ("M0504", "Mathematics AS-T", "Upper Division Coursework (Required)",
     "Required", "MATH 265", ""),
]

# Real-export headers — note the DOUBLE space in "Type of  List".
COLUMNS = ["Plan Code", "Program", "Type of  List", "Class Selection Criteria",
           "Course", "Special Notes or Limitations"]


def _build_sample(path):
    """Write the committed sample workbook. The one place its contents live."""
    pd.DataFrame(SAMPLE_ROWS, columns=COLUMNS).to_excel(path, index=False)


# --------------------------------------------------------------- happy path
def test_load_returns_program_demand():
    d = program_lists.load_program_lists(SAMPLE)
    assert isinstance(d, program_lists.ProgramDemand)
    assert isinstance(d.required, dict)
    assert isinstance(d.listed, dict)
    assert isinstance(d.titles, dict)


def test_titles_map_plan_code_to_program_name():
    d = program_lists.load_program_lists(SAMPLE)
    assert d.titles["M0501"] == "Biology AS-T"
    assert d.titles["M0504"] == "Mathematics AS-T"
    assert d.n_plans == 4


def test_required_course_counts_across_programs():
    d = program_lists.load_program_lists(SAMPLE)
    assert d.required["MATH 227"] == {"M0501", "M0502", "M0503", "M0504"}
    assert len(d.required["MATH 227"]) == 4
    assert d.required["CHEM 101"] == {"M0501", "M0502"}
    assert len(d.required["MATH 261"]) == 2


def test_select_n_list_course_is_listed_not_required():
    d = program_lists.load_program_lists(SAMPLE)
    # PHYSICS 006 is in M0501's "List A / Select 1" bucket -> a choice, not a
    # hard requirement.
    assert "PHYSICS 006" not in d.required
    assert d.listed["PHYSICS 006"] == {"M0501"}
    # an elective likewise
    assert "CHEM 102" not in d.required
    assert "CHEM 102" in d.listed


def test_required_is_superset_subgraph_of_listed():
    d = program_lists.load_program_lists(SAMPLE)
    # every (course, plan) in required must also appear in listed (any bucket)
    for course, plans in d.required.items():
        assert plans <= d.listed.get(course, set())


def test_required_substring_in_type_of_list_counts():
    d = program_lists.load_program_lists(SAMPLE)
    # "Upper Division Coursework (Required)" -> the substring "required" classifies
    # it as a hard requirement.
    assert d.required["MATH 265"] == {"M0504"}


# --------------------------------------------------------------- normalization
def test_course_keys_normalized_via_mapping_norm():
    d = program_lists.load_program_lists(SAMPLE)
    # _norm uppercases + collapses whitespace but does NOT strip leading zeros.
    assert "BIOLOGY 003" in d.required
    assert _norm("BIOLOGY 003") == "BIOLOGY 003"
    assert "BIOLOGY 3" not in d.required          # honest: no leading-zero collapse
    # everything stored is already normalized
    for course in list(d.listed) + list(d.required):
        assert course == _norm(course)


# --------------------------------------------------------------- the double-space header
def test_double_space_type_of_list_header_is_used():
    # the module must look for the export's literal double-space header.
    assert program_lists.TYPE_COL == "Type of  List"


def test_double_space_header_classifies_required(tmp_path):
    # a frame whose ONLY required signal is the double-space "Type of  List"
    # column (criteria blank) must still classify Required rows.
    p = tmp_path / "ds.csv"
    pd.DataFrame(
        [{"Plan Code": "P1", "Program": "Prog One",
          "Type of  List": "Required", "Course": "ART 101"},
         {"Plan Code": "P1", "Program": "Prog One",
          "Type of  List": "List A", "Course": "ART 102"}]
    ).to_csv(p, index=False)
    d = program_lists.load_program_lists(str(p))
    assert d.required["ART 101"] == {"P1"}
    assert "ART 102" not in d.required
    assert d.listed["ART 102"] == {"P1"}


# --------------------------------------------------------------- error surface
def test_missing_plan_code_column_raises_naming_file(tmp_path):
    p = tmp_path / "noplan.csv"
    pd.DataFrame([{"Program": "X", "Course": "ART 101"}]).to_csv(p, index=False)
    with pytest.raises(SourceDataError) as exc:
        program_lists.load_program_lists(str(p))
    assert "noplan.csv" in str(exc.value)
    assert "Plan Code" in str(exc.value)


def test_missing_course_column_raises_naming_file(tmp_path):
    p = tmp_path / "nocourse.csv"
    pd.DataFrame([{"Plan Code": "P1", "Program": "X"}]).to_csv(p, index=False)
    with pytest.raises(SourceDataError) as exc:
        program_lists.load_program_lists(str(p))
    assert "nocourse.csv" in str(exc.value)
    assert "Course" in str(exc.value)


def test_blank_plan_or_course_rows_skipped(tmp_path):
    p = tmp_path / "blanks.csv"
    pd.DataFrame(
        [{"Plan Code": "P1", "Program": "X", "Type of  List": "Required",
          "Course": "ART 101"},
         {"Plan Code": "", "Program": "X", "Type of  List": "Required",
          "Course": "ART 999"},                       # blank plan -> skipped
         {"Plan Code": "P1", "Program": "X", "Type of  List": "Required",
          "Course": ""}]                              # blank course -> skipped
    ).to_csv(p, index=False)
    d = program_lists.load_program_lists(str(p))
    assert set(d.listed) == {"ART 101"}


# --------------------------------------------------------------- fixture guard
def test_committed_fixture_matches_builder(tmp_path):
    """The committed sample must equal what _build_sample produces (logical
    contents, not raw bytes)."""
    fresh = tmp_path / "fresh.xlsx"
    _build_sample(fresh)
    a = program_lists.load_program_lists(SAMPLE)
    b = program_lists.load_program_lists(str(fresh))
    assert a.required == b.required
    assert a.listed == b.listed
    assert a.titles == b.titles


def test_committed_fixture_has_no_pii():
    cols = [c for s in pd.ExcelFile(SAMPLE).sheet_names
            for c in pd.ExcelFile(SAMPLE).parse(s, nrows=0).columns]
    for pii in ("Name", "Emails", "Email", "Instructor", "Student", "SID"):
        assert pii not in cols, f"committed fixture must not carry PII column {pii!r}"
