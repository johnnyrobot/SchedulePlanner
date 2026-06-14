"""Tests for E13 — the equity-disaggregated course-success GAP detector.

Computes a MEASURED aggregate gap = a subgroup's course-success rate minus a
reference rate (the overall/All row, else the highest-performing subgroup), over
small-cell-SUPPRESSED disaggregated data. Never a completion gap, never
student-level, never causal. Inert by default (no disaggregated export).
"""
import json

from equity_success_gap import equity_success_gap_report


def _sec(course, term=2268):
    return {"course": course, "term": term, "class_nbr": "1", "days": "MW",
            "times": "9:00 AM - 10:00 AM", "status": "Open"}


def test_inert_when_no_disagg_map():
    rep = equity_success_gap_report([_sec("MATH 125")], None)
    assert rep["status"] == "inert"
    assert rep.get("remedy")


def test_label_carries_measured_gap_caveats():
    rep = equity_success_gap_report([_sec("MATH 125")], None)
    assert "MEASURED" in rep["label"]
    low = rep["label"].lower()
    assert "not a completion gap" in low and "not student-level" in low


def test_reference_is_the_all_row_and_below_reference_flagged():
    dmap = {"MATH 125": {
        "All": {"success_rate": 0.62, "count": 1000, "suppressed": False},
        "Group A": {"success_rate": 0.70, "count": 400, "suppressed": False},
        "Group B": {"success_rate": 0.50, "count": 300, "suppressed": False}}}
    rep = equity_success_gap_report([_sec("MATH 125")], dmap)
    assert rep["status"] == "active"
    course = rep["courses"][0]
    assert course["reference_subgroup"] == "All"
    assert course["reference_rate"] == 0.62
    below = {g["subgroup"]: g["gap"] for g in course["below_reference"]}
    assert "Group B" in below
    assert abs(below["Group B"] - (-0.12)) < 1e-9       # 0.50 - 0.62
    assert "Group A" not in below                        # above reference, not a concern


def test_reference_falls_back_to_highest_subgroup_when_no_all_row():
    dmap = {"MATH 125": {
        "Group A": {"success_rate": 0.70, "count": 400, "suppressed": False},
        "Group B": {"success_rate": 0.50, "count": 300, "suppressed": False}}}
    rep = equity_success_gap_report([_sec("MATH 125")], dmap)
    course = rep["courses"][0]
    assert course["reference_subgroup"] == "Group A"      # highest, used as reference
    assert course["reference_basis"] == "highest_subgroup"
    below = {g["subgroup"] for g in course["below_reference"]}
    assert below == {"Group B"}


def test_suppressed_subgroups_counted_not_shown():
    dmap = {"MATH 125": {
        "All": {"success_rate": 0.62, "count": 1000, "suppressed": False},
        "Group C": {"success_rate": None, "count": None, "suppressed": True}}}
    rep = equity_success_gap_report([_sec("MATH 125")], dmap)
    course = rep["courses"][0]
    assert course["suppressed_subgroups"] == 1
    shown = {g["subgroup"] for g in course["below_reference"]}
    assert "Group C" not in shown                         # suppressed cell never shown


def test_only_offered_courses_with_data():
    dmap = {"MATH 125": {"All": {"success_rate": 0.62, "count": 1000, "suppressed": False},
                         "Group B": {"success_rate": 0.50, "count": 300, "suppressed": False}},
            "PHYS 999": {"All": {"success_rate": 0.9, "count": 50, "suppressed": False}}}
    rep = equity_success_gap_report([_sec("MATH 125"), _sec("ENGL 101")], dmap)
    assert {c["course"] for c in rep["courses"]} == {"MATH 125"}   # ENGL no data; PHYS not offered


def test_courses_sorted_by_largest_gap_first():
    dmap = {
        "MATH 125": {"All": {"success_rate": 0.60, "count": 1000, "suppressed": False},
                     "G": {"success_rate": 0.55, "count": 300, "suppressed": False}},   # -0.05
        "CHEM 101": {"All": {"success_rate": 0.60, "count": 1000, "suppressed": False},
                     "G": {"success_rate": 0.30, "count": 300, "suppressed": False}}}   # -0.30
    rep = equity_success_gap_report([_sec("MATH 125"), _sec("CHEM 101")], dmap)
    assert [c["course"] for c in rep["courses"]] == ["CHEM 101", "MATH 125"]


def test_not_assessed_discloses_completion_causation_suppression():
    dmap = {"MATH 125": {"All": {"success_rate": 0.62, "count": 1000, "suppressed": False},
                         "Group B": {"success_rate": 0.50, "count": 300, "suppressed": False}}}
    rep = equity_success_gap_report([_sec("MATH 125")], dmap)
    checks = {n["check"] for n in rep["not_assessed"]}
    assert {"student_completion", "causation", "suppressed_subgroups"} <= checks
    for n in rep["not_assessed"]:
        assert n["status"] == "inert" and n["reason"]


def test_granularity_reported():
    dmap = {"1701.00": {"All": {"success_rate": 0.6, "count": 1000, "suppressed": False},
                        "G": {"success_rate": 0.4, "count": 300, "suppressed": False}}}
    rep = equity_success_gap_report([_sec("1701.00")], dmap, granularity="TOP Code")
    assert rep["granularity"] == "TOP Code"


def test_course_with_no_below_reference_gap_is_omitted():
    # Every subgroup >= reference (only the All row + an above-ref group) -> no gap to show.
    dmap = {"MATH 125": {"All": {"success_rate": 0.60, "count": 1000, "suppressed": False},
                         "Group A": {"success_rate": 0.75, "count": 400, "suppressed": False}}}
    rep = equity_success_gap_report([_sec("MATH 125")], dmap)
    # active (data present) but no course has a below-reference gap
    assert all(c["below_reference"] for c in rep["courses"]) or rep["courses"] == []


def test_deterministic_and_json():
    dmap = {"MATH 125": {"All": {"success_rate": 0.62, "count": 1000, "suppressed": False},
                         "Group B": {"success_rate": 0.50, "count": 300, "suppressed": False}}}
    secs = [_sec("MATH 125")]
    a = equity_success_gap_report(secs, dmap)
    b = equity_success_gap_report(secs, dmap)
    assert a == b
    json.dumps(a)
