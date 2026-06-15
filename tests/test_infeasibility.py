"""Tests for E11 — the deterministic CP-SAT infeasibility explainer (MUS).

The core ``minimal_conflict_set`` mirrors engine.solve_cohort's HARD feasibility
constraints (season-relaxed = allow_fixes regime, no objective) over major
courses, each gated by an enforcement literal, and isolates the minimal
unsatisfiable set of required courses via SufficientAssumptionsForInfeasibility +
deterministic iterative deletion. These tests pin clean, hand-verifiable
scenarios (unit overflow, time conflict, feasible, GE-background) where the MUS
is known by construction.
"""
import json

import infeasibility


# --------------------------------------------------------------- core MUS logic
def test_unit_overflow_two_courses_is_minimal_conflict():
    # A,B each 12 units, cap 18, single term (H=1): both forced into term 1 ->
    # 24 > 18 infeasible; dropping either one (12 <= 18) restores feasibility.
    out = infeasibility.minimal_conflict_set(
        courses=["A", "B"], units={"A": 12, "B": 12}, prereqs={},
        hard_conflicts=set(), H=1, maxu=18, ge_rows=[])
    assert out["feasible"] is False
    assert set(out["conflict_set"]) == {"A", "B"}
    assert out["background_only"] is False


def test_time_conflict_pair_is_minimal_conflict():
    # A,B hard-conflict and H=1 forces both into term 1 -> cannot co-schedule.
    out = infeasibility.minimal_conflict_set(
        courses=["A", "B"], units={"A": 3, "B": 3}, prereqs={},
        hard_conflicts={frozenset({"A", "B"})}, H=1, maxu=18, ge_rows=[])
    assert out["feasible"] is False
    assert set(out["conflict_set"]) == {"A", "B"}


def test_feasible_plan_reports_feasible():
    # A,B each 9 units, cap 18, H=1 -> 18 <= 18 fits; nothing to explain.
    out = infeasibility.minimal_conflict_set(
        courses=["A", "B"], units={"A": 9, "B": 9}, prereqs={},
        hard_conflicts=set(), H=1, maxu=18, ge_rows=[])
    assert out["feasible"] is True


def test_minimal_set_is_truly_minimal_not_the_whole_core():
    # Three 12-unit courses, cap 18, H=1: ANY two overflow (24>18). The MUS must
    # be a 2-course subset, never all three (a sufficient core could over-include).
    out = infeasibility.minimal_conflict_set(
        courses=["A", "B", "C"], units={"A": 12, "B": 12, "C": 12}, prereqs={},
        hard_conflicts=set(), H=1, maxu=18, ge_rows=[])
    assert out["feasible"] is False
    assert len(out["conflict_set"]) == 2          # minimal, not the full 3-course core
    assert set(out["conflict_set"]).issubset({"A", "B", "C"})


def test_background_only_when_ge_reserve_alone_overflows():
    # A single major course (relaxable) + a GE reserve area needing 2 items of 18
    # units in H=1 (cap 18): infeasible even with NO required major course ->
    # the conflict is in the GE/background, not a major-course set.
    ge = [{"program_code": "P", "area": "2", "resolution": "reserve",
           "required_count": 2, "units": 18, "area_title": "Math",
           "pattern": "igetc", "recommended": None, "candidates": []}]
    out = infeasibility.minimal_conflict_set(
        courses=["A"], units={"A": 3}, prereqs={}, hard_conflicts=set(),
        H=1, maxu=18, ge_rows=ge)
    assert out["feasible"] is False
    assert out["conflict_set"] == []
    assert out["background_only"] is True


def test_minimal_conflict_set_is_deterministic():
    args = dict(courses=["A", "B", "C"], units={"A": 12, "B": 12, "C": 12},
                prereqs={}, hard_conflicts=set(), H=1, maxu=18, ge_rows=[])
    assert infeasibility.minimal_conflict_set(**args) == \
        infeasibility.minimal_conflict_set(**args)


# ------------------------------------------------------------- report envelope
def test_report_inert_when_no_unbuildable_cohort():
    results = {"programs": {"P": {"title": "P", "cohorts": {
        "full_time": {"terms_used": 4, "plan": {1: ["A"]}},
        "part_time": {"terms_used": 6, "plan": {1: ["A"]}}}}}}
    out = infeasibility.infeasibility_report("(unused)", results)
    assert out["status"] == "inert"
    assert "feasible" in out["reason"].lower() and out.get("remedy")


def test_label_carries_structural_diagnostic_caveat():
    out = infeasibility.infeasibility_report("(unused)", {"programs": {}})
    assert "STRUCTURAL" in out["label"] or "structural" in out["label"]
    assert "NOT a student outcome" in out["label"] or "not a student outcome" in out["label"].lower()


def test_report_is_json_serializable_inert():
    out = infeasibility.infeasibility_report("(unused)", {"programs": {}})
    json.dumps(out)
