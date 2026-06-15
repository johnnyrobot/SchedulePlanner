"""Tests for contact_hours.py — E15/F10 Title 5 §55002.5 contact-hour conformance.

The HONEST REVERSE of the ambiguous units->duration map: from OBSERVED scheduled
meeting time (+ weeks-of-instruction) compute implied per-unit term contact hours
and flag only IMPLAUSIBLE outliers against a wide Title 5 band. Heavily gated:
sections without units / woi / a meeting time are NOT assessed (surfaced, never
silently dropped). Deterministic; outside engine.run.
"""
import json

import contact_hours


def _sec(course, *, units="3", days="MW", times="10:00 AM - 11:30 AM",
         woi="18", contact="LEC", term="2268", class_nbr=None, meetings=None):
    r = {"course": course, "term": term, "units": units, "days": days,
         "times": times, "woi": woi, "contact": contact,
         "class_nbr": class_nbr or f"{course}-1"}
    if meetings is not None:
        r["meetings"] = meetings
    return r


# --------------------------------------------------------------- inert

def test_inert_without_sections():
    out = contact_hours.contact_hours_report([])
    assert out["status"] == "inert"
    assert "section" in out["reason"].lower()


def test_inert_when_no_section_is_assessable():
    # live-like rows: a meeting time but no weeks-of-instruction and no units ->
    # nothing can be normalized -> inert with a remedy naming the missing fields.
    rows = [_sec("MATH 101", units="", woi="")]
    out = contact_hours.contact_hours_report(rows)
    assert out["status"] == "inert"
    assert "week" in out["remedy"].lower() or "woi" in out["remedy"].lower()
    assert "unit" in out["remedy"].lower()


# --------------------------------------------------------------- active / consistent

def test_consistent_lecture_is_assessed_not_flagged():
    # 3-unit LEC, MW 10:00-11:30 = 90 min x 2 days = 180 min/wk; woi 18 ->
    # 180/60*18 = 54 term contact hrs / 3 units = 18 hrs/unit -> squarely in the
    # lecture band -> assessed, NOT flagged.
    out = contact_hours.contact_hours_report([_sec("MATH 101")])
    assert out["status"] == "active"
    assert out["assessed"] == 1
    assert out["flagged"] == []
    assert out["consistent"] == 1


def test_implausibly_low_lecture_is_flagged():
    # 5-unit LEC meeting only M 9:00-9:30 (30 min/wk); woi 18 -> 9 term hrs / 5
    # units = 1.8 hrs/unit -> far below the lecture floor -> flagged LOW.
    row = _sec("HIST 1", units="5", days="M", times="9:00 AM - 9:30 AM")
    out = contact_hours.contact_hours_report([row])
    assert out["status"] == "active"
    assert len(out["flagged"]) == 1
    f = out["flagged"][0]
    assert f["course"] == "HIST 1"
    assert f["direction"] == "low"
    assert f["per_unit_term_hours"] < 9


def test_implausibly_high_lecture_is_flagged():
    # 1-unit LEC meeting MTWThF 8:00 AM - 12:00 PM (240 min x 5 = 1200 min/wk);
    # woi 18 -> 360 term hrs / 1 unit -> far above the band -> flagged HIGH.
    row = _sec("PE 1", units="1", days="MTWThF", times="8:00 AM - 12:00 PM")
    out = contact_hours.contact_hours_report([row])
    assert len(out["flagged"]) == 1
    assert out["flagged"][0]["direction"] == "high"


# --------------------------------------------------------------- not assessed

def test_tba_section_is_excluded_and_surfaced():
    rows = [_sec("MATH 101"), _sec("ONLINE 1", days="", times="")]  # async/TBA
    out = contact_hours.contact_hours_report(rows)
    na = out["not_assessed"]
    assert na["no_meeting_time"] >= 1
    assert out["assessed"] == 1  # only the timed one


def test_missing_units_or_woi_surfaced_not_dropped():
    rows = [_sec("MATH 101"),
            _sec("NOWOI 1", woi=""),       # missing weeks-of-instruction
            _sec("NOUNIT 1", units="")]    # missing units
    out = contact_hours.contact_hours_report(rows)
    na = out["not_assessed"]
    assert na["missing_weeks"] >= 1
    assert na["missing_units"] >= 1


def test_category_reads_ir_component_key_case_insensitively():
    # The IR enrollment join attaches the contact category under "Component"
    # (capital C, sources/enrollment.py) — the ONLY path that supplies one. The
    # lab band [27,81] must be selected, not the wide union [9,81], or the LEC/LAB
    # distinction the feature is built around is dead code. A 3-unit LAB meeting
    # MW 9:00-10:30 (180 min/wk) × woi 18 = 54 term hrs / 3 = 18 hrs/unit -> BELOW
    # the lab floor (27) -> flagged low (it would pass under the union band).
    row = {"course": "CHEM 101", "term": "2268", "class_nbr": "1",
           "days": "MW", "times": "9:00 AM - 10:30 AM",
           "units": "3", "woi": "18", "Component": "LAB"}
    out = contact_hours.contact_hours_report([row])
    r0 = out["assessed_rows"][0]
    assert r0["contact_category"] == "lab"
    assert r0["expected_band"] == [27.0, 81.0]
    assert len(out["flagged"]) == 1 and out["flagged"][0]["direction"] == "low"


def test_unknown_contact_category_uses_wide_union_band_and_discloses():
    # no LEC/LAB token -> a wide union band; a mid-range value is consistent and
    # the category-unknown count is surfaced.
    row = _sec("UNK 1", contact="")
    out = contact_hours.contact_hours_report([row])
    assert out["status"] == "active"
    assert out["not_assessed"]["category_unknown"] >= 1


# --------------------------------------------------------------- multi-block (E1 fwd-compat)

def test_multiblock_meetings_sum_all_blocks():
    # forward-compatible with E1/#57: when a `meetings` list is present, ALL blocks
    # count. A LEC + lab block: MW 10:00-11:00 (120) + F 13:00-16:00 (180) = 300
    # min/wk; woi 18 -> 90 term hrs / 3 units = 30 hrs/unit.
    blocks = [{"days": "MW", "times": "10:00 AM - 11:00 AM"},
              {"days": "F", "times": "1:00 PM - 4:00 PM"}]
    row = _sec("BIO 3", units="3", meetings=blocks, days="MW",
               times="10:00 AM - 11:00 AM")
    out = contact_hours.contact_hours_report([row])
    f = out["assessed_rows"][0]
    assert f["weekly_minutes"] == 300
    assert out["used_all_blocks"] is True


def test_single_block_undercount_risk_disclosed_when_no_meetings_list():
    # without a `meetings` list only the first block is visible -> the undercount
    # risk for multi-block sections is disclosed (E1 dependency made honest).
    out = contact_hours.contact_hours_report([_sec("MATH 101")])
    checks = json.dumps(out["not_assessed"]).lower()
    assert "block" in checks or "undercount" in checks


# --------------------------------------------------------------- honesty + determinism

def test_label_is_honest_conformance_proxy():
    label = contact_hours.CONTACT_HOURS_LABEL.lower()
    assert "proxy" in label or "conformance" in label
    assert "not" in label and ("compliance" in label or "record" in label)


def test_report_is_deterministic():
    rows = [_sec("MATH 101"), _sec("HIST 1", units="5", days="M",
                                    times="9:00 AM - 9:30 AM")]
    a = contact_hours.contact_hours_report(rows)
    b = contact_hours.contact_hours_report(rows)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
