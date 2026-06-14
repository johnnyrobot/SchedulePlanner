"""Tests for perturbation.py — E14 minimal-perturbation recommender.

The inverse of E11: the fewest OFFERING changes (add a section / add an
alternate-time section) that flip a program's required path from
structurally not-buildable (F1) to buildable. Deterministic; outside engine.run.
"""
import json

import perturbation


# --------------------------------------------------------------- fixtures

def _sec(course, term="2268", days="", times="", class_nbr=None, status="Open",
         cap=30, tot=10, wait=0):
    """One raw section dict in the shape buildability.offered_by_course reads."""
    return {
        "course": course, "term": term, "class_nbr": class_nbr or f"{course}-{days}{times}",
        "days": days, "times": times, "status": status,
        "Cap Enrl": cap, "Tot Enrl": tot, "Wait Tot": wait,
    }


def _program(courses, *, code="MATH-AA", title="Math AA", choices=None):
    return {
        "code": code, "title": title,
        "courses": [c if isinstance(c, dict) else {"course_id": c} for c in courses],
        "major_choices": choices or [],
    }


# --------------------------------------------------------------- min cover

def test_cover_empty_edges_is_empty():
    assert perturbation.minimal_conflict_cover([]) == []


def test_cover_single_edge_picks_lexicographically_first_node():
    # min vertex cover of {A-B} is one node; the deterministic tie-break prefers
    # the lexicographically earlier course.
    assert perturbation.minimal_conflict_cover([["B", "A"]]) == ["A"]


def test_cover_star_is_the_center_alone():
    edges = [["X", "A"], ["X", "B"], ["X", "C"]]
    assert perturbation.minimal_conflict_cover(edges) == ["X"]


def test_cover_triangle_needs_two_nodes_deterministically():
    edges = [["A", "B"], ["B", "C"], ["A", "C"]]
    cover = perturbation.minimal_conflict_cover(edges)
    assert len(cover) == 2
    # a valid cover hits every edge
    assert all(a in cover or b in cover for a, b in edges)
    # deterministic, lexicographically-minimal among min covers
    assert cover == ["A", "B"]


def test_cover_tie_graph_is_uniquely_resolved():
    # K(2,2) between {C0,C3} and {C1,C2}: BOTH {C0,C3} and {C1,C2} are size-2 min
    # covers. A LINEAR index-sum tie-break ties them (0+3 == 1+2 == 3), leaving the
    # pick to CP-SAT internal heuristics (fragile across OR-Tools versions). The
    # base-2 dominating weight breaks the tie UNIQUELY toward earlier-indexed nodes.
    edges = [["C0", "C1"], ["C0", "C2"], ["C3", "C1"], ["C3", "C2"]]
    cover = perturbation.minimal_conflict_cover(edges)
    assert len(cover) == 2 and all(a in cover or b in cover for a, b in edges)
    assert cover == ["C1", "C2"]   # the base-2-minimal of the two tied covers


# --------------------------------------------------------------- inert

def test_report_inert_without_programs():
    out = perturbation.perturbation_report([], [_sec("MATH 101")])
    assert out["status"] == "inert"
    assert "program" in out["reason"].lower()


def test_report_inert_without_sections():
    out = perturbation.perturbation_report([_program(["MATH 101"])], [])
    assert out["status"] == "inert"
    assert "section" in out["reason"].lower()


def test_report_inert_when_every_program_is_already_buildable():
    # required course offered, no conflict -> nothing to recommend.
    prog = _program(["MATH 101"])
    out = perturbation.perturbation_report([prog], [_sec("MATH 101", days="MW",
                                                         times="10:00 AM - 11:00 AM")])
    assert out["status"] == "inert"
    assert "buildable" in out["reason"].lower()


# --------------------------------------------------------------- missing course

def test_missing_required_course_yields_one_add_section_action():
    prog = _program(["MATH 101", "ENGL 101"])
    sections = [_sec("MATH 101", days="MW", times="10:00 AM - 11:00 AM")]  # ENGL absent
    out = perturbation.perturbation_report([prog], sections)
    assert out["status"] == "active"
    p = out["programs"][0]
    assert p["total_changes"] == 1
    adds = [a for a in p["actions"] if a["action"] == "add_section"]
    assert [a["course"] for a in adds] == ["ENGL 101"]
    assert p["buildable_after"] is True
    assert p["score_after"] > p["score_before"]


# --------------------------------------------------------------- time conflict

def test_pairwise_hard_conflict_yields_one_alt_time_action():
    prog = _program(["MATH 101", "PHYS 101"])
    sections = [
        _sec("MATH 101", days="MW", times="10:00 AM - 11:00 AM"),
        _sec("PHYS 101", days="MW", times="10:00 AM - 11:00 AM"),
    ]
    out = perturbation.perturbation_report([prog], sections)
    p = out["programs"][0]
    alts = [a for a in p["actions"] if a["action"] == "add_alt_time_section"]
    assert len(alts) == 1
    assert alts[0]["course"] == "MATH 101"  # lexicographically-first cover node
    assert p["total_changes"] == 1
    assert p["buildable_after"] is True


# --------------------------------------------------------------- choice bucket

def test_choice_bucket_shortfall_yields_add_choice_option_action():
    prog = _program(["MATH 101"],
                    choices=[{"options": ["HIST 101", "HIST 102", "HIST 103"],
                              "need": 2}])
    sections = [
        _sec("MATH 101", days="MW", times="10:00 AM - 11:00 AM"),
        _sec("HIST 101", days="TTh", times="9:00 AM - 10:00 AM"),  # only 1 of 2 offered
    ]
    out = perturbation.perturbation_report([prog], sections)
    p = out["programs"][0]
    ch = [a for a in p["actions"] if a["action"] == "add_choice_option"]
    assert len(ch) == 1
    assert ch[0]["shortfall"] == 1
    assert p["total_changes"] == 1
    assert p["buildable_after"] is True


def test_choice_bucket_that_exceeds_its_option_set_cannot_be_cleared():
    # need 3 but only 2 options exist -> no offering of the listed options can
    # satisfy it; honest buildable_after False, never an overclaim.
    prog = _program(["MATH 101"],
                    choices=[{"options": ["HIST 101", "HIST 102"], "need": 3}])
    sections = [_sec("MATH 101", days="MW", times="10:00 AM - 11:00 AM")]
    out = perturbation.perturbation_report([prog], sections)
    p = out["programs"][0]
    assert p["buildable_after"] is False
    assert any("option set" in n.lower() or "exceeds" in n.lower()
               for n in p.get("notes", []))
    # total_changes counts the offerings ACTUALLY buildable (2 unoffered options),
    # never the raw shortfall of 3 — no overclaim in the count either.
    assert p["total_changes"] == 2


# --------------------------------------------------------------- dead requirement

def test_dead_requirement_is_excluded_from_actions_and_surfaced():
    # a de-catalogued required course is NOT fixable by adding an offering
    # (that's a curriculum change) -> no add_section for it.
    prog = _program(["MATH 101", "GONE 999"])
    sections = [_sec("MATH 101", days="MW", times="10:00 AM - 11:00 AM")]
    active = {"MATH 101"}  # GONE 999 not in active catalog
    out = perturbation.perturbation_report([prog], sections, active_courses=active)
    p = out["programs"][0]
    assert all(a.get("course") != "GONE 999" for a in p["actions"])
    assert any("GONE 999" in str(n) or "catalog" in str(n).lower()
               for n in p.get("notes", []))


# --------------------------------------------------------------- honesty + determinism

def test_label_is_honest_about_offerings_not_outcomes():
    label = perturbation.MIN_PERTURBATION_LABEL.lower()
    assert "offering" in label
    assert "not" in label and ("completion" in label or "outcome" in label)


def test_not_assessed_discloses_seat_and_prereq_horizon_gaps():
    prog = _program(["MATH 101", "ENGL 101"])
    sections = [_sec("MATH 101", days="MW", times="10:00 AM - 11:00 AM")]
    out = perturbation.perturbation_report([prog], sections)
    checks = " ".join(str(n) for n in out.get("not_assessed", [])).lower()
    assert "seat" in checks or "instructor" in checks
    assert "prereq" in checks or "horizon" in checks


def test_report_is_deterministic():
    prog = _program(["MATH 101", "ENGL 101", "PHYS 101"])
    sections = [
        _sec("MATH 101", days="MW", times="10:00 AM - 11:00 AM"),
        _sec("PHYS 101", days="MW", times="10:00 AM - 11:00 AM"),
    ]
    a = perturbation.perturbation_report([prog], sections)
    b = perturbation.perturbation_report([prog], sections)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
