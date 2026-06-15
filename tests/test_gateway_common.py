"""Tests for gateway_common — the shared transfer-level gateway-identification
primitives used by BOTH F8 (gateway_momentum) and F9 (corequisite_availability).

These pin the primitive contract directly (F8's suite exercises them through the
report; F9 imports them straight). Behavior must match what gateway_momentum
shipped — this module is a pure extract, no semantic change.
"""
from gateway_common import (FIRST_YEAR_TERMS, _course_number, _first_year_terms,
                            _GATEWAYS, _identify_gateway, _subject)


def _program(*, ge_requirements=None, courses=None):
    return {"code": "P", "ge_requirements": ge_requirements or [],
            "courses": courses or []}


def test_gateways_tuple_covers_english_and_math():
    names = [g[0] for g in _GATEWAYS]
    assert names == ["english", "math"]


def test_subject_token_of_normalized_course_id():
    assert _subject("ENGL 101") == "ENGL"
    assert _subject("math 227") == "MATH"
    assert _subject("") == ""


def test_course_number_first_integer():
    assert _course_number("MATH 245H") == 245
    assert _course_number("ENGL 101") == 101
    assert _course_number("NONUM") == 0


def test_first_year_terms_is_earliest_two_distinct():
    secs = [{"term": 2268}, {"term": 2248}, {"term": 2252}, {"term": 2248}]
    assert _first_year_terms(secs, None) == [2248, 2252]
    assert FIRST_YEAR_TERMS == 2


def test_first_year_terms_honors_caller_horizon():
    assert _first_year_terms([{"term": 2248}], [9999]) == [9999]


def test_identify_gateway_via_ge_area_is_area_defined():
    eng = _identify_gateway(
        _program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]),
        frozenset({"1A", "1"}), frozenset({"ENGL", "ENGLISH"}))
    assert eng["identified"] is True
    assert eng["course"] == "ENGL 101"
    assert eng["via"] == "ge_area_1A"
    assert eng["transfer_level"] == "area-defined"


def test_identify_gateway_major_fallback_prefers_higher_number_unverified():
    # MATH 125 (below-transfer, earlier sem) vs MATH 150 (transfer): pick 150.
    math = _identify_gateway(
        _program(courses=[{"course_id": "MATH 125", "recommended_semester": 1},
                          {"course_id": "MATH 150", "recommended_semester": 2}]),
        frozenset({"2", "2A"}), frozenset({"MATH", "MATHEMATICS"}))
    assert math["course"] == "MATH 150"
    assert math["via"] == "major_subject"
    assert math["transfer_level"] == "unverified"


def test_identify_gateway_none_when_absent():
    out = _identify_gateway(_program(), frozenset({"1A"}), frozenset({"ENGL"}))
    assert out["identified"] is False
