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


def _ge_coverage():
    """ge.resolve-style coverage: one schedulable area, one true gap, and three
    areas that must be EXCLUDED from the denominator (shared / no-articulation /
    reserve-only remainder)."""
    return {"areas": [
        {"area": "1A", "required": 1, "offered_eligible": 2, "eligible_count": 4, "flags": []},
        {"area": "4",  "required": 1, "offered_eligible": 0, "eligible_count": 3, "flags": ["no_offering"]},
        {"area": "6",  "required": 1, "offered_eligible": 0, "eligible_count": 0, "flags": ["no_assist_data"]},
        {"area": "5B", "required": 0, "resolution": "shared", "flags": []},
        {"area": "3",  "required": 1, "offered_eligible": 0, "eligible_count": None, "flags": []},
    ]}


def test_ge_denominator_counts_schedulable_and_gaps_excludes_rest():
    dn = B.ge_denominator(_ge_coverage())
    assert dn == {"areas_in_denominator": 2, "areas_schedulable": 1, "gaps": ["4"]}


def test_ge_denominator_none_when_absent_or_all_unknown():
    assert B.ge_denominator(None) is None
    assert B.ge_denominator({"areas": []}) is None
    # every area is unknown-articulation -> nothing countable -> None (fail open)
    assert B.ge_denominator({"areas": [
        {"area": "6", "required": 1, "offered_eligible": 0, "eligible_count": 0,
         "flags": ["no_assist_data"]}]}) is None


def test_score_rescales_denominator_with_ge():
    tc = {"feasible": True}
    major = B._score(8, ["X"], [], tc, [], [])                       # (8-1)/8 -> 88
    blended = B._score(8, ["X"], [], tc, [], [], ge_required=7, ge_missing=1)  # (15-2)/15 -> 87
    assert major == 88 and blended == 87


def test_score_ge_kwargs_default_to_today():
    tc = {"feasible": True}
    assert (B._score(4, ["X"], [], tc, [], [])
            == B._score(4, ["X"], [], tc, [], [], ge_required=0, ge_missing=0))


def _ge_active_coverage():
    return {"reviewed": False, "areas": [
        {"area": "1A", "required": 1, "offered_eligible": 2, "eligible_count": 4, "flags": []},
        {"area": "4",  "required": 1, "offered_eligible": 0, "eligible_count": 3, "flags": ["no_offering"]},
    ]}


def test_audit_program_folds_ge_into_score_with_delta():
    audit = B.audit_program(_program(), _sections(), ge_coverage=_ge_active_coverage())
    ge = audit["ge"]
    assert ge["status"] == "active" and ge["draft"] is True
    assert ge["areas_in_denominator"] == 2 and ge["areas_schedulable"] == 1 and ge["gaps"] == ["4"]
    # Signed delta: GE-inclusive == major-only + delta (direction not asserted).
    assert audit["score"] == audit["score_major_only"] + audit["score_delta"]
    assert audit["score_delta"] != 0          # a GE gap moved the number
    assert "GE:" in audit["summary"]


def test_audit_program_no_ge_is_byte_identical_today():
    a = B.audit_program(_program(), _sections())
    assert a["ge"] is None
    assert a["score"] == a["score_major_only"] and a["score_delta"] == 0


def test_audit_program_ge_inert_when_all_unknown_articulation():
    cov = {"reviewed": True, "areas": [
        {"area": "6", "required": 1, "offered_eligible": 0, "eligible_count": 0,
         "flags": ["no_assist_data"]}]}
    audit = B.audit_program(_program(), _sections(), ge_coverage=cov)
    assert audit["ge"]["status"] == "inert" and audit["ge"]["reason"]
    assert audit["score"] == audit["score_major_only"]   # GE did NOT move the number


def test_buildability_report_carries_ge_label():
    rep = B.buildability_report([_program()], _sections(), ge_coverage=_ge_active_coverage())
    assert "GE-inclusive" in rep["ge_label"]


def test_score_ge_can_raise_when_major_has_gaps():
    """Folding a fully-schedulable GE set RAISES the score when the major path has
    gaps — the signed delta can be POSITIVE (guards against assuming GE only lowers
    the number)."""
    tc = {"feasible": True}
    major = B._score(4, ["X", "Y"], [], tc, [], [])                 # (4-2)/4 = 50
    blended = B._score(4, ["X", "Y"], [], tc, [], [], ge_required=4, ge_missing=0)  # (8-2)/8 = 75
    assert major == 50 and blended == 75
    assert blended > major          # GE folding RAISED the score (positive delta)
