"""Regression tests for the code-review remediation (Critical + themes 1-3).

Each test pins a behavior the PRE-fix code got wrong, so the fix can't silently
regress:

  * Critical  — sources.schedule.fetch_sections never ``raise None``.
  * Theme 1   — secondary / lab meeting blocks (``meetings[1:]`` and split rows
                sharing a class number) are no longer dropped by the detectors.
  * Theme 2   — courses offered in DIFFERENT terms are not flagged as a hard /
                mutually-exclusive time conflict.
  * Theme 3   — aliased subject spellings (ENGL vs ENGLISH) are matched at the
                cross-program / demand join points, not undercounted.
"""
from types import SimpleNamespace

import buildability
import cross_program_bottleneck as cpb
import demand_supply
import grid_pressure
from sources import schedule
from sources import timeblocks as tb


# --------------------------------------------------------------- Critical -----
def test_fetch_sections_empty_terms_never_raises_none(monkeypatch):
    # An empty terms list falls back to DEFAULT_TERMS; force it empty to prove the
    # guard: the loop never runs, so a bare ``raise last_error`` would be raise None.
    monkeypatch.setattr(schedule, "DEFAULT_TERMS", [])
    assert schedule.fetch_sections("LAMC", terms=[]) == []


# --------------------------------------------------------- Theme 2: termed -----
def test_pairwise_hard_conflict_termed_same_term_overlaps():
    mw10 = tb.parse_meeting("MW", "10:00 AM - 11:00 AM")
    a = [(2268, mw10)]
    b = [(2268, mw10)]
    assert tb.pairwise_hard_conflict_termed(a, b) is True


def test_pairwise_hard_conflict_termed_cross_term_is_free():
    mw10 = tb.parse_meeting("MW", "10:00 AM - 11:00 AM")
    # Same weekly pattern but offered in different terms -> take them separately.
    a = [(2268, mw10)]
    b = [(2272, mw10)]
    assert tb.pairwise_hard_conflict_termed(a, b) is False


def test_pairwise_hard_conflict_termed_async_is_free():
    mw10 = tb.parse_meeting("MW", "10:00 AM - 11:00 AM")
    a = [(2268, mw10)]
    b = [(2268, [])]  # async/TBA escape valve
    assert tb.pairwise_hard_conflict_termed(a, b) is False


def test_pairwise_hard_conflict_termed_matches_untermed_single_term():
    mw10 = tb.parse_meeting("MW", "10:00 AM - 11:00 AM")
    tr = tb.parse_meeting("TR", "10:00 AM - 11:00 AM")
    a_plain, b_plain = [mw10], [tr]
    a_termed = [(2268, mw10)]
    b_termed = [(2268, tr)]
    assert (tb.pairwise_hard_conflict_termed(a_termed, b_termed)
            == tb.pairwise_hard_conflict(a_plain, b_plain) is False)


# --------------------------------------- Theme 2: buildability.time_conflict ---
def test_time_conflict_cross_term_not_hard():
    program = {"courses": [{"course_id": "A 1"}, {"course_id": "B 1"}]}
    required = {"A 1", "B 1"}
    sections = [
        {"course": "A 1", "term": 2268, "class_nbr": "1", "days": "MW",
         "times": "10:00 AM - 11:00 AM"},
        {"course": "B 1", "term": 2272, "class_nbr": "2", "days": "MW",
         "times": "10:00 AM - 11:00 AM"},
    ]
    offered = buildability.offered_by_course(sections)
    res = buildability.time_conflict(program, required, offered)
    assert res["pairwise_hard"] == []
    assert res["feasible"] is True


def test_time_conflict_same_term_still_hard():
    program = {"courses": [{"course_id": "A 1"}, {"course_id": "B 1"}]}
    required = {"A 1", "B 1"}
    sections = [
        {"course": "A 1", "term": 2268, "class_nbr": "1", "days": "MW",
         "times": "10:00 AM - 11:00 AM"},
        {"course": "B 1", "term": 2268, "class_nbr": "2", "days": "MW",
         "times": "10:00 AM - 11:00 AM"},
    ]
    offered = buildability.offered_by_course(sections)
    res = buildability.time_conflict(program, required, offered)
    assert ["A 1", "B 1"] in res["pairwise_hard"]


# --------------------------------- Theme 1: split-row meeting union ------------
def test_offered_by_course_unions_split_lecture_lab_rows():
    # Same physical section split across two rows sharing a class number and with
    # NO per-row ``meetings`` list (an import shape) — the lab block must survive.
    sections = [
        {"course": "BIO 3", "term": 2268, "class_nbr": "1", "days": "MW",
         "times": "8:00 AM - 9:00 AM"},
        {"course": "BIO 3", "term": 2268, "class_nbr": "1", "days": "F",
         "times": "1:00 PM - 4:00 PM"},
    ]
    offered = buildability.offered_by_course(sections)
    assert len(offered["BIO 3"]) == 1  # one section, not two
    meeting = offered["BIO 3"][0]["meeting"]
    days = {b[0] for b in meeting}
    assert {"M", "W", "F"} <= days  # the Friday lab block was unioned in, not dropped


def test_offered_by_course_single_row_unchanged():
    sections = [
        {"course": "MATH 1", "term": 2268, "class_nbr": "1", "days": "MW",
         "times": "9:00 AM - 10:00 AM"},
    ]
    offered = buildability.offered_by_course(sections)
    assert offered["MATH 1"][0]["meeting"] == tb.parse_meeting(
        "MW", "9:00 AM - 10:00 AM")


# --------------------------------- Theme 2: grid_pressure mutual_exclusions ----
def test_mutual_exclusions_cross_term_not_flagged():
    # Two morning-locked courses at the same weekly time but in different terms.
    sections = [
        {"course": "A 1", "term": 2268, "class_nbr": "1", "days": "MW",
         "times": "10:00 AM - 11:00 AM"},
        {"course": "B 1", "term": 2272, "class_nbr": "2", "days": "MW",
         "times": "10:00 AM - 11:00 AM"},
    ]
    locked = {"A 1", "B 1"}
    pairs, _trunc = grid_pressure.mutual_exclusions(sections, locked)
    assert pairs == []


def test_mutual_exclusions_same_term_flagged():
    sections = [
        {"course": "A 1", "term": 2268, "class_nbr": "1", "days": "MW",
         "times": "10:00 AM - 11:00 AM"},
        {"course": "B 1", "term": 2268, "class_nbr": "2", "days": "MW",
         "times": "10:00 AM - 11:00 AM"},
    ]
    locked = {"A 1", "B 1"}
    pairs, _trunc = grid_pressure.mutual_exclusions(sections, locked)
    assert [p["courses"] for p in pairs] == [["A 1", "B 1"]]


# --------------------------- Theme 3: cross_program_bottleneck alias merge -----
def test_offered_match_merges_both_spellings():
    eng_secs = [{"id": "engl"}]
    english_secs = [{"id": "english"}]
    offered = {"ENGL 101": eng_secs, "ENGLISH 101": english_secs}
    alias_idx = cpb._alias_index(offered)
    _label, secs = cpb._offered_match("ENGLISH 101", offered, alias_idx)
    # Both spellings of the same course are merged, deduped by identity (2 not 3).
    assert len(secs) == 2
    ids = {id(s) for s in secs}
    assert ids == {id(english_secs[0]), id(eng_secs[0])}


def test_offered_match_single_spelling_unchanged():
    secs = [{"id": "a"}, {"id": "b"}]
    offered = {"MATH 101": secs}
    alias_idx = cpb._alias_index(offered)
    label, got = cpb._offered_match("MATH 101", offered, alias_idx)
    assert label == "MATH 101"
    assert got == secs


# ---------------------------------------- Theme 3: demand_supply alias join ----
def _counted_section(course, term="2268", cls="1"):
    return {"course": course, "term": term, "class_nbr": cls,
            "days": "MW", "times": "10:00 AM - 11:00 AM",
            "Cap Enrl": 30, "Tot Enrl": 30, "Wait Tot": 20, "status": "Closed"}


def test_demand_supply_matches_aliased_required():
    # Offered as ENGLISH, required map keyed by ENGL -> must still be weighted and
    # NOT counted as not_assessed.
    sections = [_counted_section("ENGLISH 101")]
    demand = SimpleNamespace(required={"ENGL 101": ("PROG_A", "PROG_B")})
    res = demand_supply.demand_supply_report(
        sections, program_demand=demand, facility=None)
    assert res["status"] == "active"
    assert res["not_assessed"] == 0
    row = next(r for r in res["add_list"] if r["course"] == "ENGLISH 101")
    assert row["n_programs"] == 2
    assert row["required"] is True
