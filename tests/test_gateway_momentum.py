"""Tests for F8 — First-Year Gateway-Momentum detector (offering proxy).

The detector reports, as an honest OFFERING proxy, whether a program's
transfer-level English (GE Area 1A) and Math (Area 2) gateway courses can be
SCHEDULED in the first year of the analyzed schedule. It never claims a student
completed them (no student-level data exists). Patterns mirror the demand_supply
/ grid_pressure detector tests: label caveat, inert cases, active case,
identification strategies, determinism, JSON-serializability.
"""
import json

from gateway_momentum import gateway_momentum_report


def _sec(course, term, *, class_nbr="", days="MW", times="9:00 AM - 10:00 AM"):
    return {"course": course, "term": term, "class_nbr": class_nbr,
            "days": days, "times": times, "status": "Open",
            "Cap Enrl": 30, "Tot Enrl": 10, "Wait Tot": 0}


def _program(*, ge_requirements=None, courses=None):
    return {"code": "P", "title": "Test Program",
            "ge_requirements": ge_requirements or [], "courses": courses or []}


# --------------------------------------------------------------- honesty label
def test_label_carries_proxy_caveat():
    rep = gateway_momentum_report([], program=_program())
    assert "PROXY" in rep["label"]
    assert "NOT a measured completion rate" in rep["label"]


# ------------------------------------------------------------------ inert paths
def test_inert_when_no_sections():
    rep = gateway_momentum_report([], program=_program(
        ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]))
    assert rep["status"] == "inert"
    assert "section" in rep["reason"].lower()
    assert rep.get("remedy")


def test_inert_when_no_gateway_identifiable():
    # No 1A/2 GE area and no ENGL/MATH required major course -> cannot identify.
    rep = gateway_momentum_report(
        [_sec("CS 101", 2248)],
        program=_program(ge_requirements=[{"area": "3A", "recommended_course": "ART 1"}],
                         courses=[{"course_id": "CS 101", "recommended_semester": 1}]))
    assert rep["status"] == "inert"
    assert "gateway" in rep["reason"].lower()
    assert rep.get("remedy")


# --------------------------------------------------------------- identification
def test_identifies_english_via_ge_area_1A():
    # Two sections in the same first-year term -> schedulable, no single-section flag.
    rep = gateway_momentum_report(
        [_sec("ENGL 101", 2248, class_nbr="1"), _sec("ENGL 101", 2248, class_nbr="2")],
        program=_program(ge_requirements=[
            {"area": "1A", "recommended_course": "ENGL 101", "recommended_semester": 1}]))
    assert rep["status"] == "active"
    eng = rep["english"]
    assert eng["identified"] is True
    assert eng["course"] == "ENGL 101"
    assert eng["via"] == "ge_area_1A"
    assert eng["schedulable_year1"] is True
    assert eng["obstructions"] == []


def test_identifies_math_via_major_subject_fallback():
    # No Math GE area, but a required MATH major course -> identified by subject.
    rep = gateway_momentum_report(
        [_sec("MATH 150", 2248)],
        program=_program(
            ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}],
            courses=[{"course_id": "MATH 150", "recommended_semester": 2}]))
    math = rep["math"]
    assert math["identified"] is True
    assert math["course"] == "MATH 150"
    assert math["via"] == "major_subject"


def test_english_subject_alias_engl_folds_to_english():
    # A required 'ENGL 101' major course (no GE area) is identified as the English
    # gateway via canonical_subject(ENGL) == ENGLISH.
    rep = gateway_momentum_report(
        [_sec("ENGL 101", 2248), _sec("MATH 150", 2248)],
        program=_program(courses=[
            {"course_id": "ENGL 101", "recommended_semester": 1},
            {"course_id": "MATH 150", "recommended_semester": 1}]))
    assert rep["english"]["course"] == "ENGL 101"
    assert rep["english"]["via"] == "major_subject"
    assert rep["math"]["course"] == "MATH 150"


# ----------------------------------------------------------------- obstructions
def test_not_offered_obstruction():
    # Gateway named but no section of it anywhere in the schedule.
    rep = gateway_momentum_report(
        [_sec("CS 101", 2248)],
        program=_program(ge_requirements=[
            {"area": "1A", "recommended_course": "ENGL 101"}]))
    eng = rep["english"]
    assert eng["identified"] is True
    assert eng["schedulable_year1"] is False
    assert any("not offered" in o for o in eng["obstructions"])


def test_offered_only_after_year1_obstruction():
    # ENGL 101 offered only in the 3rd term -> outside the first-year (earliest 2) window.
    secs = [_sec("ENGL 101", 2268), _sec("CS 101", 2248), _sec("CS 101", 2252)]
    rep = gateway_momentum_report(secs, program=_program(
        ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]))
    eng = rep["english"]
    assert eng["schedulable_year1"] is False
    assert any("after the first-year window" in o for o in eng["obstructions"])


def test_single_section_in_window_obstruction():
    # One ENGL 101 section in each of the two first-year terms -> single-section risk.
    secs = [_sec("ENGL 101", 2248, class_nbr="1"),
            _sec("ENGL 101", 2252, class_nbr="2")]
    rep = gateway_momentum_report(secs, program=_program(
        ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]))
    eng = rep["english"]
    assert eng["schedulable_year1"] is True
    assert any("single section" in o for o in eng["obstructions"])


# ------------------------------------------------------------------- aggregates
def test_both_gateways_year1_true_when_both_schedulable():
    secs = [_sec("ENGL 101", 2248, class_nbr="1"), _sec("ENGL 101", 2248, class_nbr="2"),
            _sec("MATH 150", 2248, class_nbr="3"), _sec("MATH 150", 2252, class_nbr="4"),
            _sec("MATH 150", 2252, class_nbr="5")]
    rep = gateway_momentum_report(secs, program=_program(ge_requirements=[
        {"area": "1A", "recommended_course": "ENGL 101"},
        {"area": "2", "recommended_course": "MATH 150"}]))
    assert rep["english"]["schedulable_year1"] is True
    assert rep["math"]["schedulable_year1"] is True
    assert rep["both_gateways_year1"] is True


def test_first_year_window_is_earliest_two_terms():
    secs = [_sec("ENGL 101", 2268), _sec("CS 101", 2248), _sec("CS 101", 2252)]
    rep = gateway_momentum_report(secs, program=_program(
        ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]))
    assert rep["first_year_terms"] == ["2248", "2252"]


def test_not_assessed_lists_placement_and_student_outcome():
    rep = gateway_momentum_report(
        [_sec("ENGL 101", 2248)],
        program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]))
    checks = {n["check"] for n in rep["not_assessed"]}
    assert "placement_prerequisite_blocking" in checks
    assert "student_completion" in checks
    for n in rep["not_assessed"]:
        assert n["status"] == "inert" and n["reason"]


# ----------------------------------------------------- transfer-level honesty
def test_major_subject_prefers_higher_numbered_over_below_transfer():
    # MATH 125 (Intermediate Algebra, below-transfer, sem 1) + MATH 150 (transfer
    # Calculus, sem 2): the fallback must NOT pick the earlier REMEDIAL course.
    # Prefer the higher course number (a better transfer-level correlate).
    rep = gateway_momentum_report(
        [_sec("MATH 125", 2248, class_nbr="1"), _sec("MATH 150", 2248, class_nbr="2")],
        program=_program(courses=[
            {"course_id": "MATH 125", "recommended_semester": 1},
            {"course_id": "MATH 150", "recommended_semester": 2}]))
    assert rep["math"]["course"] == "MATH 150"
    assert rep["math"]["transfer_level"] == "unverified"   # subject heuristic, not verified


def test_ge_area_gateway_is_transfer_level_verified():
    rep = gateway_momentum_report(
        [_sec("ENGL 101", 2248)],
        program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]))
    assert rep["english"]["transfer_level"] == "area-defined"  # 1A IS transfer-level by GE def


def test_not_assessed_includes_seat_and_time_conflict():
    rep = gateway_momentum_report(
        [_sec("ENGL 101", 2248)],
        program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]))
    checks = {n["check"] for n in rep["not_assessed"]}
    assert "seat_availability_and_time_conflict" in checks


def test_tolerates_none_ge_requirements_and_courses():
    # program_mapper always emits lists, but a None must not crash the detector.
    rep = gateway_momentum_report(
        [_sec("CS 101", 2248)],
        program={"code": "P", "ge_requirements": None, "courses": None})
    assert rep["status"] == "inert"   # nothing identifiable, no TypeError


def test_single_term_window_collapse_is_disclosed():
    rep = gateway_momentum_report(
        [_sec("ENGL 101", 2248)],
        program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]))
    assert rep["first_year_terms"] == ["2248"]
    assert rep.get("window_note")     # the single-term collapse is surfaced


# -------------------------------------------------------------- determinism/json
def test_report_is_byte_stable():
    secs = [_sec("ENGL 101", 2248), _sec("MATH 150", 2252)]
    prog = _program(ge_requirements=[
        {"area": "1A", "recommended_course": "ENGL 101"},
        {"area": "2", "recommended_course": "MATH 150"}])
    assert gateway_momentum_report(secs, program=prog) == \
        gateway_momentum_report(secs, program=prog)


def test_report_is_json_serializable():
    rep = gateway_momentum_report(
        [_sec("ENGL 101", 2248)],
        program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]))
    json.dumps(rep)  # must not raise
