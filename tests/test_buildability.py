"""Tests for buildability.py — the Program-Map Buildability Audit (F1).

Deterministic, pure-function audit of whether a program's required path is
schedulable against a set of raw section records. No network, no solver.
"""
import buildability as B
from sources import mapping

NORM = mapping._norm


def _program():
    """A small Biology-like program: 4 required courses + one major choice.

    BIOLOGY 3 and CHEM 101 are both recommended for semester 1 and (in the
    fixture below) each runs a single MW 9:00 section -> a hard time conflict.
    PHYSICS 6 is required but never offered -> missing. The ANTHRO choice has
    only one of its two options offered -> zero slack.
    """
    return {
        "code": "BIOL-AS", "title": "Biology AS-T", "award": "AS-T",
        "courses": [
            {"course_id": "BIOLOGY 3", "recommended_semester": 1},
            {"course_id": "CHEM 101", "recommended_semester": 1},
            {"course_id": "MATH 261", "recommended_semester": 2},
            {"course_id": "PHYSICS 6", "recommended_semester": 3},
        ],
        "major_choices": [
            {"options": ["ANTHRO 101", "ANTHRO 102"], "recommended_semester": 2},
        ],
    }


def _sections():
    return [
        {"course": "BIOLOGY 3", "term": 2268, "class_nbr": "1001", "days": "MW",
         "times": "9:00 AM - 10:15 AM", "Cap Enrl": 30, "Tot Enrl": 30, "status": "Closed"},
        {"course": "CHEM 101", "term": 2268, "class_nbr": "1002", "days": "MW",
         "times": "9:00 AM - 10:15 AM", "Cap Enrl": 24, "Tot Enrl": 10, "status": "Open"},
        {"course": "MATH 261", "term": 2268, "class_nbr": "1003", "days": "TR",
         "times": "11:00 AM - 12:15 PM", "Cap Enrl": 35, "Tot Enrl": 20, "status": "Open"},
        {"course": "ANTHRO 101", "term": 2268, "class_nbr": "1004", "days": "F",
         "times": "9:00 AM - 11:50 AM", "Cap Enrl": 40, "Tot Enrl": 5, "status": "Open"},
    ]


# --- per-sub-check helpers -------------------------------------------------

def test_required_set_normalizes_major_courses():
    req = B.required_set(_program())
    assert req == {NORM("BIOLOGY 3"), NORM("CHEM 101"), NORM("MATH 261"), NORM("PHYSICS 6")}


def test_offered_by_course_dedups_meeting_pattern_rows():
    secs = _sections() + [
        # a second meeting-pattern row of the SAME section (same term+class_nbr) -> deduped
        {"course": "BIOLOGY 3", "term": 2268, "class_nbr": "1001", "days": "F",
         "times": "9:00 AM - 11:50 AM"},
    ]
    offered = B.offered_by_course(secs)
    assert len(offered[NORM("BIOLOGY 3")]) == 1  # the duplicate (term,class_nbr) row dropped


def test_availability_flags_missing_required():
    offered = B.offered_by_course(_sections())
    avail, missing = B.availability(B.required_set(_program()), offered)
    assert NORM("PHYSICS 6") in missing
    assert NORM("BIOLOGY 3") in avail and NORM("MATH 261") in avail


def test_choice_slack_counts_offered_options():
    offered = B.offered_by_course(_sections())
    slack = B.choice_slack(_program(), offered)
    assert len(slack) == 1
    assert slack[0]["need"] == 1 and slack[0]["offered"] == 1 and slack[0]["slack"] == 0


def test_time_conflict_detects_hard_pair_and_term_clash():
    offered = B.offered_by_course(_sections())
    tc = B.time_conflict(_program(), B.required_set(_program()), offered)
    assert tc["feasible"] is False
    assert [NORM("BIOLOGY 3"), NORM("CHEM 101")] in tc["pairwise_hard"]
    # both are recommended for semester 1 -> also a term clash
    assert any(c["recommended_semester"] == 1 for c in tc["term_clashes"])


def test_time_conflict_feasible_when_no_overlap():
    secs = [
        {"course": "A 1", "term": 2268, "class_nbr": "1", "days": "MW",
         "times": "9:00 AM - 10:15 AM"},
        {"course": "B 1", "term": 2268, "class_nbr": "2", "days": "TR",
         "times": "9:00 AM - 10:15 AM"},
    ]
    prog = {"courses": [{"course_id": "A 1", "recommended_semester": 1},
                        {"course_id": "B 1", "recommended_semester": 1}]}
    offered = B.offered_by_course(secs)
    tc = B.time_conflict(prog, B.required_set(prog), offered)
    assert tc["feasible"] is True and tc["pairwise_hard"] == [] and tc["term_clashes"] == []


def test_single_section_required_flags_lone_sections():
    offered = B.offered_by_course(_sections())
    lone = B.single_section_required(B.required_set(_program()), offered)
    assert NORM("BIOLOGY 3") in lone and NORM("MATH 261") in lone


def test_season_mismatch_when_recommended_season_not_offered():
    # term 2268 -> Fall; recommended_semester 2 -> Spring under the default cadence.
    offered = B.offered_by_course(_sections())
    mm = B.season_mismatches(_program(), offered)
    courses = {m["course"] for m in mm}
    assert NORM("MATH 261") in courses          # rec sem 2 (Spring) but only offered Fall
    assert NORM("BIOLOGY 3") not in courses      # rec sem 1 (Fall), offered Fall -> ok


def test_seat_pressure_flags_full_or_closed():
    offered = B.offered_by_course(_sections())
    sp = {s["course"]: s for s in B.seat_pressure(B.required_set(_program()), offered)}
    assert NORM("BIOLOGY 3") in sp and sp[NORM("BIOLOGY 3")]["closed"] is True
    assert NORM("CHEM 101") not in sp            # 10/24 fill, open -> no pressure


def test_dead_requirements_skipped_without_active_set():
    dead, note = B.dead_requirements(B.required_set(_program()), None)
    assert dead == [] and note  # honest note, never a false positive


def test_dead_requirements_flags_inactive_course():
    active = {NORM("BIOLOGY 3"), NORM("CHEM 101"), NORM("MATH 261")}  # PHYSICS 6 inactive
    dead, note = B.dead_requirements(B.required_set(_program()), active)
    assert dead == [NORM("PHYSICS 6")] and note is None


# --- assembly + honesty ----------------------------------------------------

def test_audit_program_scorecard_shape_and_score():
    audit = B.audit_program(_program(), _sections())
    assert audit["code"] == "BIOL-AS"
    assert audit["required_total"] == 4 and audit["available"] == 3
    assert audit["missing"] == [NORM("PHYSICS 6")]
    assert audit["time_conflict"]["feasible"] is False
    assert 0 <= audit["score"] <= 100
    # missing 1/4 + a hard time conflict -> not a perfect score
    assert audit["score"] < 100
    assert isinstance(audit["summary"], str) and audit["summary"]


def test_audit_program_by_design_exclusion_not_counted_missing():
    audit = B.audit_program(_program(), _sections(), by_design={NORM("PHYSICS 6")})
    assert NORM("PHYSICS 6") not in audit["missing"]
    assert NORM("PHYSICS 6") in audit["by_design_excluded"]


def test_buildability_report_active_with_program():
    rep = B.buildability_report([_program()], _sections())
    assert rep["status"] == "active"
    assert rep["label"] and "PROXY" in rep["label"]
    assert len(rep["programs"]) == 1
    assert rep["horizon_terms"] == [2268]


def test_buildability_report_inert_when_no_sections():
    rep = B.buildability_report([_program()], [])
    assert rep["status"] == "inert" and rep["reason"]


def test_buildability_report_inert_when_no_program():
    rep = B.buildability_report([], _sections())
    assert rep["status"] == "inert" and rep["reason"]


def test_perfect_program_scores_100():
    prog = {"code": "OK", "title": "All good",
            "courses": [{"course_id": "A 1", "recommended_semester": 1},
                        {"course_id": "B 1", "recommended_semester": 1}]}
    secs = [
        {"course": "A 1", "term": 2268, "class_nbr": "1", "days": "MW",
         "times": "9:00 AM - 10:15 AM", "Cap Enrl": 30, "Tot Enrl": 5, "status": "Open"},
        {"course": "A 1", "term": 2268, "class_nbr": "9", "days": "TR",
         "times": "9:00 AM - 10:15 AM", "Cap Enrl": 30, "Tot Enrl": 5, "status": "Open"},
        {"course": "B 1", "term": 2268, "class_nbr": "2", "days": "TR",
         "times": "11:00 AM - 12:15 PM", "Cap Enrl": 30, "Tot Enrl": 5, "status": "Open"},
        {"course": "B 1", "term": 2268, "class_nbr": "8", "days": "MW",
         "times": "11:00 AM - 12:15 PM", "Cap Enrl": 30, "Tot Enrl": 5, "status": "Open"},
    ]
    audit = B.audit_program(prog, secs)
    assert audit["score"] == 100 and audit["time_conflict"]["feasible"] is True
