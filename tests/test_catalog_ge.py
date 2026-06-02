"""Tests for the local-GE catalog extractor (sources/catalog_ge.py).

Runs entirely on a committed OpenDataLoader-shaped JSON fixture — no PDF, no JVM,
no network — so the catalog -> GE extraction is proven deterministically in CI.
The final test feeds the extractor's output straight into the existing generic
resolver (sources/ge.resolve) to prove the Spec-1 pipeline is reused unchanged.
"""
import json
import pathlib

from sources import catalog_ge, ge

FIX = pathlib.Path(__file__).parent / "fixtures"


def _sample():
    return json.loads((FIX / "catalog_odl_sample.json").read_text())


def test_extract_finds_section_and_areas():
    pattern, area_courses, diag = catalog_ge.extract_local_ge(_sample())
    assert diag["section_found"] is True
    assert [a["code"] for a in pattern["areas"]] == ["A", "B", "G1"]
    assert pattern["reviewed_by"] == ""          # empty -> auto draft-gated
    assert diag["area_count"] == 3


def test_extract_area_courses_from_list_and_table():
    _pattern, area_courses, _diag = catalog_ge.extract_local_ge(_sample())
    assert area_courses["A"]["title"] == "Natural Sciences"
    assert area_courses["A"]["courses"] == ["BIOL 3", "CHEM 101", "PHYS SC 1"]
    # Multi-word subject pulled from a table: "POL SCI 1".
    assert area_courses["B"]["courses"] == ["HIST 11", "POL SCI 1", "PSYCH 1"]


def test_extract_named_area_without_area_label_gets_synth_code():
    _pattern, area_courses, _diag = catalog_ge.extract_local_ge(_sample())
    assert "G1" in area_courses
    assert area_courses["G1"]["title"].startswith("Mathematics")
    assert area_courses["G1"]["courses"] == ["MATH 245", "MATH 261"]


def test_extract_respects_section_end_boundary():
    # "ZZZZ 999" lives under the post-GE "Graduation Requirements" heading and
    # must NOT be attributed to any GE area.
    _pattern, area_courses, _diag = catalog_ge.extract_local_ge(_sample())
    all_courses = [c for a in area_courses.values() for c in a["courses"]]
    assert "ZZZZ 999" not in all_courses


def test_extract_no_ge_section_is_honest():
    doc = {"kids": [
        {"type": "heading", "heading level": 1, "content": "Course Descriptions"},
        {"type": "paragraph", "content": "BIOL 3 is an introductory course."},
    ]}
    pattern, area_courses, diag = catalog_ge.extract_local_ge(doc)
    assert diag["section_found"] is False
    assert pattern["areas"] == [] and area_courses == {}


def test_extract_empty_input_does_not_raise():
    pattern, area_courses, diag = catalog_ge.extract_local_ge({})
    assert pattern["areas"] == [] and area_courses == {}
    assert diag["section_found"] is False


def test_extracted_ge_feeds_resolver_unchanged():
    # The whole point of Spec 2: the catalog-derived (pattern, area_courses) is the
    # SAME shape ge.resolve already consumes from ASSIST.
    pattern, area_courses, _diag = catalog_ge.extract_local_ge(_sample())
    offered = {"BIOL 3", "CHEM 101", "HIST 11", "POL SCI 1", "MATH 245"}
    program = {"courses": [{"course_id": "BIOL 6"}]}
    rows, coverage = ge.resolve(pattern, area_courses, offered, program)
    assert len(coverage["areas"]) == 3
    assert {r["area"] for r in rows} <= {"A", "B", "G1"}
    # Area A has >=1 offered candidate -> it resolves (concrete or reserve), not unknown.
    assert coverage["unknown_areas"] == []
