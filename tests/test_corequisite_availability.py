"""Tests for F9 — AB1705 corequisite co-availability detector.

Reports, as an honest co-OFFERING STRUCTURE proxy, whether the catalog
corequisite SUPPORT course of a transfer-level English (GE Area 1A) / Math
(Area 2) gateway is scheduled in the SAME first-year term as the gateway. It
never claims the support is required, sufficient, or causal (per AB1705 evidence,
direct placement was the dominant lever) and never measures a student outcome.
Patterns mirror the F8 gateway_momentum tests.
"""
import json

from corequisite_availability import corequisite_availability_report


def _sec(course, term, *, class_nbr="", days="MW", times="9:00 AM - 10:00 AM"):
    return {"course": course, "term": term, "class_nbr": class_nbr,
            "days": days, "times": times, "status": "Open",
            "Cap Enrl": 30, "Tot Enrl": 10, "Wait Tot": 0}


def _program(*, ge_requirements=None, courses=None):
    return {"code": "P", "title": "Test Program",
            "ge_requirements": ge_requirements or [], "courses": courses or []}


# ----------------------------------------------------------------- honesty label
def test_label_carries_structure_proxy_and_ab1705_caveats():
    rep = corequisite_availability_report([], program=_program())
    label = rep["label"]
    assert "STRUCTURE proxy" in label
    assert "DIRECT PLACEMENT" in label
    assert "NOT a measured" in label or "not a measured" in label


# ------------------------------------------------------------------ inert paths
def test_inert_when_no_sections():
    rep = corequisite_availability_report([], program=_program(
        ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]),
        coreq_map={"ENGL 101": ["ENGL 101L"]})
    assert rep["status"] == "inert"
    assert "section" in rep["reason"].lower()
    assert rep.get("remedy")


def test_inert_when_no_coreq_map():
    # Gateway + sections present, but no corequisite linkage was supplied (the
    # default path: coreqs are excluded from the prereq fetch). Remedy names it.
    rep = corequisite_availability_report(
        [_sec("ENGL 101", 2248)],
        program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]),
        coreq_map=None)
    assert rep["status"] == "inert"
    assert "corequisite" in rep["reason"].lower()
    assert "elumen-live" in rep["remedy"].lower()


def test_inert_when_no_gateway_identified():
    rep = corequisite_availability_report(
        [_sec("CS 101", 2248)],
        program=_program(ge_requirements=[{"area": "3A", "recommended_course": "ART 1"}],
                         courses=[{"course_id": "CS 101", "recommended_semester": 1}]),
        coreq_map={"CS 101": ["CS 101L"]})
    assert rep["status"] == "inert"
    assert "gateway" in rep["reason"].lower()


def test_inert_when_gateway_has_no_corequisite():
    # ENGL 101 gateway identified, but the coreq map carries no entry for it.
    rep = corequisite_availability_report(
        [_sec("ENGL 101", 2248)],
        program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]),
        coreq_map={"CS 101": ["CS 101L"]})
    assert rep["status"] == "inert"
    assert "corequisite" in rep["reason"].lower()


# ----------------------------------------------------------------- active paths
def test_active_when_coreq_co_offered_same_term():
    rep = corequisite_availability_report(
        [_sec("ENGL 101", 2248, class_nbr="1"), _sec("ENGL 101L", 2248, class_nbr="2")],
        program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]),
        coreq_map={"ENGL 101": ["ENGL 101L"]})
    assert rep["status"] == "active"
    eng = rep["english"]
    assert eng["identified"] is True
    assert eng["has_corequisite"] is True
    assert eng["corequisites"] == ["ENGL 101L"]
    assert eng["co_offered_year1"] is True
    assert eng["co_offered_terms"] == ["2248"]
    assert eng["obstructions"] == []


def test_coreq_not_offered_obstruction():
    rep = corequisite_availability_report(
        [_sec("ENGL 101", 2248)],
        program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]),
        coreq_map={"ENGL 101": ["ENGL 101L"]})
    eng = rep["english"]
    assert eng["co_offered_year1"] is False
    assert any("not offered" in o for o in eng["obstructions"])
    detail = eng["corequisite_detail"][0]
    assert detail["course"] == "ENGL 101L" and detail["offered"] is False


def test_coreq_offered_but_never_same_term():
    # Gateway in 2248, coreq in 2252 — both in the earliest-two window, never together.
    rep = corequisite_availability_report(
        [_sec("ENGL 101", 2248, class_nbr="1"), _sec("ENGL 101L", 2252, class_nbr="2")],
        program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]),
        coreq_map={"ENGL 101": ["ENGL 101L"]})
    eng = rep["english"]
    assert eng["co_offered_year1"] is False
    assert any("never in the same term" in o for o in eng["obstructions"])


def test_coreq_offered_only_outside_first_year_window():
    # Gateway in 2248/2252 (year1), coreq only in 2268 (3rd term).
    secs = [_sec("ENGL 101", 2248, class_nbr="1"), _sec("ENGL 101", 2252, class_nbr="2"),
            _sec("ENGL 101L", 2268, class_nbr="3")]
    rep = corequisite_availability_report(
        secs, program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]),
        coreq_map={"ENGL 101": ["ENGL 101L"]})
    eng = rep["english"]
    assert eng["co_offered_year1"] is False
    assert any("outside the first-year window" in o for o in eng["obstructions"])


def test_math_via_major_subject_fallback_active():
    rep = corequisite_availability_report(
        [_sec("MATH 150", 2248, class_nbr="1"), _sec("MATH 150L", 2248, class_nbr="2")],
        program=_program(courses=[{"course_id": "MATH 150", "recommended_semester": 1}]),
        coreq_map={"MATH 150": ["MATH 150L"]})
    assert rep["status"] == "active"
    math = rep["math"]
    assert math["identified"] is True and math["via"] == "major_subject"
    assert math["transfer_level"] == "unverified"
    assert math["co_offered_year1"] is True


def test_both_gateways_supported_year1():
    secs = [_sec("ENGL 101", 2248, class_nbr="1"), _sec("ENGL 101L", 2248, class_nbr="2"),
            _sec("MATH 150", 2248, class_nbr="3"), _sec("MATH 150L", 2248, class_nbr="4")]
    rep = corequisite_availability_report(secs, program=_program(ge_requirements=[
        {"area": "1A", "recommended_course": "ENGL 101"},
        {"area": "2", "recommended_course": "MATH 150"}]),
        coreq_map={"ENGL 101": ["ENGL 101L"], "MATH 150": ["MATH 150L"]})
    assert rep["english"]["co_offered_year1"] is True
    assert rep["math"]["co_offered_year1"] is True
    assert rep["both_gateways_coreq_co_offered_year1"] is True


def test_one_gateway_without_coreq_still_active_via_other():
    # English gateway identified (1A) but no coreq; Math gateway (major) has a
    # co-offered coreq -> active overall, english has_corequisite False.
    secs = [_sec("ENGL 101", 2248), _sec("MATH 150", 2248, class_nbr="1"),
            _sec("MATH 150L", 2248, class_nbr="2")]
    rep = corequisite_availability_report(secs, program=_program(
        ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}],
        courses=[{"course_id": "MATH 150", "recommended_semester": 1}]),
        coreq_map={"MATH 150": ["MATH 150L"]})
    assert rep["status"] == "active"
    assert rep["english"]["has_corequisite"] is False
    assert rep["math"]["co_offered_year1"] is True


# --------------------------------------------------------------- join / honesty
def test_join_bridges_zero_padded_schedule_and_stripped_elumen_coreqs():
    # Real LACCD live schedule emits zero-padded catalog numbers ("MATH 0238"),
    # while eLumen's normalize_course_code STRIPS zeros ("MATH 238"). The join must
    # bridge them, else a real co-offered corequisite is silently missed (false
    # inert / false "not offered"). Gateway + coreq are co-offered in 2248.
    secs = [_sec("MATH 0238", 2248, class_nbr="1"), _sec("MATH 0238L", 2248, class_nbr="2")]
    rep = corequisite_availability_report(
        secs, program=_program(ge_requirements=[{"area": "2", "recommended_course": "MATH 0238"}]),
        coreq_map={"MATH 238": ["MATH 238L"]})
    assert rep["status"] == "active"
    math = rep["math"]
    assert math["has_corequisite"] is True
    assert math["co_offered_year1"] is True
    assert math["obstructions"] == []


def test_gateway_absent_in_window_does_not_blame_the_coreq():
    # Gateway offered only in a LATER term; its coreq runs in the year-1 window.
    # The obstruction must blame the GATEWAY's absence, NOT the support course.
    secs = [_sec("ENGL 101", 2268, class_nbr="1"),     # gateway only in the 3rd term
            _sec("ENGL 101L", 2248, class_nbr="2"), _sec("ENGL 101L", 2252, class_nbr="3"),
            _sec("CS 101", 2248, class_nbr="4")]        # establishes 2248/2252 as year1
    rep = corequisite_availability_report(
        secs, program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]),
        coreq_map={"ENGL 101": ["ENGL 101L"]})
    eng = rep["english"]
    assert eng["co_offered_year1"] is False
    assert any("gateway" in o and "first-year window" in o for o in eng["obstructions"])
    assert not any("never in the same term" in o for o in eng["obstructions"])


def test_co_offering_robust_to_mixed_term_types():
    # A coreq in the SAME term still co-offers even when one section's term is an
    # int and the other a str (latent type-mismatch in the set intersection).
    secs = [{"course": "ENGL 101", "term": 2248, "class_nbr": "1", "days": "MW",
             "times": "9:00 AM - 10:00 AM", "status": "Open"},
            {"course": "ENGL 101L", "term": "2248", "class_nbr": "2", "days": "MW",
             "times": "9:00 AM - 10:00 AM", "status": "Open"}]
    rep = corequisite_availability_report(
        secs, program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]),
        coreq_map={"ENGL 101": ["ENGL 101L"]})
    assert rep["english"]["co_offered_year1"] is True
    assert rep["english"]["co_offered_terms"] == ["2248"]


def test_coreq_join_is_norm_insensitive_to_spacing():
    # A coreq map keyed/valued with irregular spacing still joins via mapping._norm.
    rep = corequisite_availability_report(
        [_sec("ENGL 101", 2248, class_nbr="1"), _sec("ENGL 101L", 2248, class_nbr="2")],
        program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]),
        coreq_map={"ENGL  101": ["ENGL 101L"]})
    assert rep["status"] == "active"
    assert rep["english"]["co_offered_year1"] is True


def test_not_assessed_lists_placement_linkage_and_completion():
    rep = corequisite_availability_report(
        [_sec("ENGL 101", 2248, class_nbr="1"), _sec("ENGL 101L", 2248, class_nbr="2")],
        program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]),
        coreq_map={"ENGL 101": ["ENGL 101L"]})
    checks = {n["check"] for n in rep["not_assessed"]}
    assert "placement_prerequisite_blocking" in checks
    assert "corequisite_enrollment_linkage" in checks
    assert "student_completion_or_corequisite_effectiveness" in checks
    for n in rep["not_assessed"]:
        assert n["status"] == "inert" and n["reason"]


def test_tolerates_none_program():
    rep = corequisite_availability_report(
        [_sec("ENGL 101", 2248)], program=None, coreq_map={"ENGL 101": ["ENGL 101L"]})
    assert rep["status"] == "inert"  # no gateway identifiable, no crash


def test_single_term_window_collapse_is_disclosed():
    rep = corequisite_availability_report(
        [_sec("ENGL 101", 2248, class_nbr="1"), _sec("ENGL 101L", 2248, class_nbr="2")],
        program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]),
        coreq_map={"ENGL 101": ["ENGL 101L"]})
    assert rep["first_year_terms"] == ["2248"]
    assert rep.get("window_note")


# -------------------------------------------------------------- determinism/json
def test_report_is_byte_stable():
    secs = [_sec("ENGL 101", 2248, class_nbr="1"), _sec("ENGL 101L", 2248, class_nbr="2")]
    prog = _program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}])
    cmap = {"ENGL 101": ["ENGL 101L"]}
    assert corequisite_availability_report(secs, program=prog, coreq_map=cmap) == \
        corequisite_availability_report(secs, program=prog, coreq_map=cmap)


def test_report_is_json_serializable():
    rep = corequisite_availability_report(
        [_sec("ENGL 101", 2248, class_nbr="1"), _sec("ENGL 101L", 2248, class_nbr="2")],
        program=_program(ge_requirements=[{"area": "1A", "recommended_course": "ENGL 101"}]),
        coreq_map={"ENGL 101": ["ENGL 101L"]})
    json.dumps(rep)
