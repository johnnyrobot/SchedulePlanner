import json
from types import SimpleNamespace

import demand_supply as D
from sources.mapping import _norm as NORM


def _sec(course, cls, cap, tot, wait=0, status="Open", term=2268,
         days="MW", times="9:00 AM - 10:15 AM", facil_id=""):
    return {"course": course, "term": term, "class_nbr": cls, "days": days,
            "times": times, "Cap Enrl": cap, "Tot Enrl": tot, "Wait Tot": wait,
            "status": status, "facil_id": facil_id}


def test_label_carries_proxy_caveat():
    assert "PROXY" in D.DEMAND_SUPPLY_LABEL


def test_inert_when_no_sections():
    r = D.demand_supply_report([])
    assert r["status"] == "inert" and "no offered sections" in r["reason"]


def test_inert_when_no_seat_counts():
    # Sections with no Cap (live API shape) -> nothing assessable -> inert.
    secs = [_sec("MATH 227", "1", cap=None, tot=None)]
    r = D.demand_supply_report(secs)
    assert r["status"] == "inert"
    assert "no Cap" in r["reason"] or "seat counts" in r["reason"]


def test_add_qualifier_high_fill_alone():
    # fill 1.0 (40/40), no waitlist -> qualifies on fill alone.
    r = D.demand_supply_report([_sec("MATH 227", "1", 40, 40)])
    assert r["status"] == "active"
    courses = [x["course"] for x in r["add_list"]]
    assert NORM("MATH 227") in courses
    row = r["add_list"][0]
    assert row["demand_ratio"] == 1.0 and row["fill"] == 1.0


def test_waitlist_needs_pairing():
    # Wait 20 (>15) but fill 0.75 and Open -> does NOT qualify (weak waitlist).
    weak = D.demand_supply_report([_sec("BIO 3", "1", 40, 30, wait=20, status="Open")])
    assert all(x["course"] != NORM("BIO 3") for x in weak["add_list"])
    # Same waitlist but Closed -> qualifies.
    closed = D.demand_supply_report([_sec("BIO 3", "1", 40, 30, wait=20, status="Closed")])
    assert any(x["course"] == NORM("BIO 3") for x in closed["add_list"])
    # Same waitlist but high fill (37/40 = 0.925 >= 0.90) -> qualifies.
    tight = D.demand_supply_report([_sec("BIO 3", "1", 40, 37, wait=20, status="Open")])
    assert any(x["course"] == NORM("BIO 3") for x in tight["add_list"])


def test_action_score_program_weight():
    secs = [_sec("MATH 227", "1", 40, 40)]
    plain = D.demand_supply_report(secs)
    assert plain["program_weighted"] is False
    assert plain["add_list"][0]["action_score"] == 1.0       # demand_ratio * 1.0
    assert plain["add_list"][0]["n_programs"] == 0

    demand = SimpleNamespace(required={NORM("MATH 227"): {"P1", "P2", "P3"}})
    weighted = D.demand_supply_report(secs, program_demand=demand)
    assert weighted["program_weighted"] is True
    row = weighted["add_list"][0]
    assert row["n_programs"] == 3 and row["required"] is True
    assert row["action_score"] == 1.3                        # round(1.0 * (1 + 0.1*3), 2)


def test_capacity_slack_observation():
    # Two under-filled sections (fill 11/80 = 0.1375 <= 0.40) -> slack, not add.
    secs = [_sec("ART 101", "1", 40, 6), _sec("ART 101", "2", 40, 5,
                 days="TR", times="1:00 PM - 2:15 PM")]
    r = D.demand_supply_report(secs)
    slack = r["capacity_slack"]
    assert any(s["course"] == NORM("ART 101") for s in slack)
    s0 = next(s for s in slack if s["course"] == NORM("ART 101"))
    assert "not a cut" in s0["note"]
    assert all(x["course"] != NORM("ART 101") for x in r["add_list"])


def test_single_under_filled_section_is_not_slack():
    # One under-filled section -> no consolidation possible -> not slack.
    r = D.demand_supply_report([_sec("ART 101", "1", 40, 6)])
    assert r["capacity_slack"] == []


def test_sort_is_byte_stable_by_score():
    secs = [
        _sec("LOW 1", "1", 40, 40),                                  # ratio 1.0
        _sec("HIGH 1", "2", 40, 40, wait=40, status="Closed"),       # ratio 2.0
    ]
    r = D.demand_supply_report(secs)
    assert [x["course"] for x in r["add_list"]] == [NORM("HIGH 1"), NORM("LOW 1")]


def test_not_assessed_counts_required_without_counts():
    secs = [_sec("MATH 227", "1", 40, 40)]
    demand = SimpleNamespace(required={
        NORM("MATH 227"): {"P1"}, NORM("PHYS 6"): {"P1"}})  # PHYS 6 not offered
    r = D.demand_supply_report(secs, program_demand=demand)
    assert r["not_assessed"] == 1
    assert json.dumps(r)                                         # JSON-serializable
