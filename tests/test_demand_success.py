"""Tests for E9 — the demand-vs-success escalation detector.

Crosses the MEASURED aggregate course-success data (from the CCCCO Data Mart
adapter) with the supply-constraint signals (F2 bottlenecks / F5 demand) to
escalate a course that is BOTH hard-to-get-into AND historically lower-success.
Inert by default (no success export); never claims a student-level or completion
outcome.
"""
import json

from demand_success import demand_success_report


def _sec(course, term=2268):
    return {"course": course, "term": term, "class_nbr": "1", "days": "MW",
            "times": "9:00 AM - 10:00 AM", "status": "Open"}


def test_inert_when_no_success_map():
    rep = demand_success_report([_sec("MATH 125")], None)
    assert rep["status"] == "inert"
    assert "success" in rep["reason"].lower() and rep.get("remedy")


def test_label_carries_measured_not_completion_caveat():
    rep = demand_success_report([_sec("MATH 125")], None)
    assert "MEASURED" in rep["label"]
    assert "NOT a program-completion" in rep["label"] or "not a program-completion" in rep["label"].lower()


def test_active_lists_offered_courses_with_outcome_only():
    smap = {"MATH 125": {"success_rate": 0.55, "retention_rate": 0.82, "enrollment": 300},
            "PHYS 999": {"success_rate": 0.9, "retention_rate": None, "enrollment": None}}
    rep = demand_success_report([_sec("MATH 125"), _sec("ENGL 101")], smap)
    assert rep["status"] == "active"
    courses = {r["course"] for r in rep["with_outcome"]}
    assert courses == {"MATH 125"}              # ENGL 101 offered but no data; PHYS 999 has data but not offered
    assert rep["with_outcome"][0]["success_rate"] == 0.55


def test_offered_without_outcome_is_surfaced_not_dropped():
    smap = {"MATH 125": {"success_rate": 0.55, "retention_rate": None, "enrollment": None}}
    rep = demand_success_report([_sec("MATH 125"), _sec("ENGL 101")], smap)
    assert rep["matched"] == 1
    assert rep["offered_without_outcome"] == 1   # ENGL 101 has no success row


def test_escalates_supply_constrained_and_low_success_lowest_first():
    smap = {"MATH 125": {"success_rate": 0.55, "retention_rate": None, "enrollment": None},
            "CHEM 101": {"success_rate": 0.40, "retention_rate": None, "enrollment": None},
            "ENGL 101": {"success_rate": 0.85, "retention_rate": None, "enrollment": None}}
    rep = demand_success_report(
        [_sec("MATH 125"), _sec("CHEM 101"), _sec("ENGL 101")], smap,
        supply_constrained=["MATH 125", "CHEM 101"])   # ENGL 101 not supply-constrained
    esc = [r["course"] for r in rep["escalated"]]
    assert esc == ["CHEM 101", "MATH 125"]      # both constrained, lowest success first
    assert "ENGL 101" not in esc                # high-success / not constrained -> not escalated


def test_escalated_empty_when_no_supply_signal():
    smap = {"MATH 125": {"success_rate": 0.55, "retention_rate": None, "enrollment": None}}
    rep = demand_success_report([_sec("MATH 125")], smap, supply_constrained=None)
    assert rep["escalated"] == []
    assert rep["with_outcome"]                  # still lists the outcome, just no escalation


def test_granularity_is_reported():
    smap = {"1701.00": {"success_rate": 0.58, "retention_rate": None, "enrollment": None}}
    rep = demand_success_report([_sec("1701.00")], smap, granularity="TOP Code")
    assert rep["granularity"] == "TOP Code"


def test_not_assessed_discloses_completion_and_causation():
    smap = {"MATH 125": {"success_rate": 0.55, "retention_rate": None, "enrollment": None}}
    rep = demand_success_report([_sec("MATH 125")], smap)
    checks = {n["check"] for n in rep["not_assessed"]}
    assert "student_completion" in checks
    assert "causation" in checks
    for n in rep["not_assessed"]:
        assert n["status"] == "inert" and n["reason"]


def test_join_norm_insensitive():
    smap = {"MATH 125": {"success_rate": 0.55, "retention_rate": None, "enrollment": None}}
    rep = demand_success_report([_sec(" math 125 ")], smap)
    assert {r["course"] for r in rep["with_outcome"]} == {"MATH 125"}


def test_report_is_deterministic_and_json():
    smap = {"MATH 125": {"success_rate": 0.55, "retention_rate": None, "enrollment": None},
            "CHEM 101": {"success_rate": 0.40, "retention_rate": None, "enrollment": None}}
    secs = [_sec("MATH 125"), _sec("CHEM 101")]
    a = demand_success_report(secs, smap, supply_constrained=["MATH 125", "CHEM 101"])
    b = demand_success_report(secs, smap, supply_constrained=["MATH 125", "CHEM 101"])
    assert a == b
    json.dumps(a)
