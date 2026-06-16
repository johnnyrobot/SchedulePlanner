r"""Tests for the room-conflict + room-capacity detector.

Two net-new diagnostics computed in build_live_workbook OUTSIDE engine.run, from the
raw section room/days/times (which the workbook schema drops), mirroring the existing
time-block collision detector:

  - _room_collisions: two DIFFERENT sections in the same room + term at overlapping
    meeting times (combined cross-lists + undeduped meeting rows excluded).
  - _room_capacity_findings: a section enrolled beyond its assigned room's seats,
    joined on Facil ID against the facility table (sources/facility.py).

Records use CANONICAL day tokens (e.g. "TTh") the live API already returns; the
schedule-export importer is what maps the exports' "R" -> "Th" (tested separately).

OFFLINE: pure functions, no network. The committed facility fixture is synthetic
and PII-free (rooms, not people).
"""
import pathlib

import pytest

from build_live_workbook import (_lab_pool_stats, _room_capacity_findings,
                                 _room_collisions, _room_detector_entry,
                                 _section_meetings_by_course)
from sources.facility import is_lab, load_facility
from sources.http import SourceDataError

REPO = pathlib.Path(__file__).resolve().parent.parent
FAC = str(REPO / "files" / "lamc_facility_sample.xlsx")


def _sec(course, class_nbr, *, facil="", room="", days="MW",
         times="9:00 AM - 10:25 AM", term=2248, comb="", tot=None):
    r = {"term": term, "course": course, "class_nbr": class_nbr, "days": days,
         "times": times, "room": room, "facil_id": facil, "Comb Sects ID": comb}
    if tot is not None:
        r["Tot Enrl"] = tot
    return r


# --- facility loader -------------------------------------------------------

def test_facility_loader_normalizes_dedups_drops_online():
    m = load_facility(FAC)
    assert set(m) == {"MINST1006", "MINST1010", "MAMP212", "MCMS006", "MCMPC1"}
    assert m["MINST1006"]["capacity"] == 30          # first eff-dated row wins, not 99
    assert m["MAMP212"]["capacity"] == 34            # padded " MAMP212" normalized
    assert "MONLINE" not in m                         # online sentinel dropped
    assert sorted(k for k, v in m.items() if is_lab(v)) == ["MAMP212", "MCMPC1", "MCMS006"]


def test_facility_loader_missing_column_raises(tmp_path):
    import pandas as pd
    bad = tmp_path / "bad.xlsx"
    pd.DataFrame([{"Facil ID": "MX1", "Building": "X"}]).to_excel(
        bad, sheet_name="sheet1", index=False)
    with pytest.raises(SourceDataError) as exc:
        load_facility(str(bad))
    assert "bad.xlsx" in str(exc.value) and "Capacity" in str(exc.value)


# --- double-booking --------------------------------------------------------

def test_double_book_same_room_overlapping_times_flagged():
    secs = [_sec("ACCTG 001", "30001", facil="MINST1006"),
            _sec("ENGL 101", "30002", facil="MINST1006")]
    out = _room_collisions(secs)
    assert len(out) == 1
    f = out[0]
    assert f["kind"] == "double_book" and f["room"] == "MINST1006"
    assert f["courses"] == ["ACCTG 001", "ENGL 101"]
    assert f["class_nbrs"] == ["30001", "30002"]


def test_no_collision_when_times_do_not_overlap():
    secs = [_sec("ACCTG 001", "30001", facil="MINST1006", times="9:00 AM - 10:25 AM"),
            _sec("ENGL 101", "30002", facil="MINST1006", times="11:00 AM - 12:25 PM")]
    assert _room_collisions(secs) == []


def test_combined_cross_list_sharing_comb_id_not_flagged():
    # Same room + time but the SAME physical meeting under two course numbers.
    secs = [_sec("BIOLOGY 3", "40004", facil="MAMP212", comb="C100"),
            _sec("ANATOMY 1", "40005", facil="MAMP212", comb="C100")]
    assert _room_collisions(secs) == []


def test_online_and_async_sections_never_collide():
    secs = [_sec("ACCTG 001", "30001", facil="MONLINE", room="ONLINE", days="TBA", times=""),
            _sec("ENGL 101", "30002", facil="MONLINE", room="ONLINE", days="TBA", times="")]
    assert _room_collisions(secs) == []


def test_room_label_fallback_when_no_facil_id():
    # Live fetch path: no Facil ID, join on the room label instead.
    secs = [_sec("ACCTG 001", "30001", room="Mission-INST 1006"),
            _sec("ENGL 101", "30002", room="Mission-INST 1006")]
    assert len(_room_collisions(secs)) == 1


def test_thursday_overlap_on_canonical_tokens():
    # "TTh" and "Th" share Thursday -> overlap at the same room/time.
    secs = [_sec("ACCTG 001", "30001", facil="MINST1006", days="TTh"),
            _sec("ENGL 101", "30002", facil="MINST1006", days="Th")]
    assert len(_room_collisions(secs)) == 1


def test_same_section_two_meeting_rows_not_self_flagged():
    secs = [_sec("ACCTG 001", "30001", facil="MINST1006"),
            _sec("ACCTG 001", "30001", facil="MINST1006")]  # undeduped meeting pattern
    assert _room_collisions(secs) == []


# --- capacity --------------------------------------------------------------

def test_over_capacity_flagged_against_facility_table():
    facility = load_facility(FAC)
    secs = [_sec("ACCTG 001", "30001", facil="MINST1006", tot=45),   # cap 30 -> over by 15
            _sec("ENGL 101", "30002", facil="MAMP212", tot=30)]      # cap 34 -> ok
    out = _room_capacity_findings(secs, facility)
    assert len(out) == 1
    f = out[0]
    assert f["kind"] == "over_capacity" and f["room"] == "MINST1006"
    assert f["capacity"] == 30 and f["enrolled"] == 45


def test_capacity_noop_without_facility():
    secs = [_sec("ACCTG 001", "30001", facil="MINST1006", tot=45)]
    assert _room_capacity_findings(secs, None) == []
    assert _room_capacity_findings(secs, {}) == []


def test_lab_pool_stats():
    facility = load_facility(FAC)
    secs = [_sec("CHEM 101", "30001", facil="MAMP212"),     # a lab
            _sec("ACCTG 001", "30002", facil="MINST1006")]  # not a lab
    stats = _lab_pool_stats(secs, facility)
    assert stats == {"total_labs": 3, "labs_in_use": 1}
    assert _lab_pool_stats(secs, None) is None


# --- detector entry (active / inert) ---------------------------------------

def test_detector_entry_active_with_facility():
    secs = [_sec("ACCTG 001", "30001", facil="MINST1006", tot=45)]
    facility = load_facility(FAC)
    coll = _room_collisions(secs)
    entry = _room_detector_entry(secs, coll, facility_used=True,
                                 capacity=_room_capacity_findings(secs, facility),
                                 lab_stats=_lab_pool_stats(secs, facility))
    assert entry["detector"] == "room_conflict" and entry["status"] == "active"
    assert entry["capacity"]["status"] == "active"


def test_detector_entry_inert_when_all_online():
    secs = [_sec("ACCTG 001", "30001", facil="MONLINE", room="ONLINE", days="TBA", times="")]
    entry = _room_detector_entry(secs, [], facility_used=False)
    assert entry["status"] == "inert"


def test_detector_entry_capacity_inert_without_facility():
    secs = [_sec("ACCTG 001", "30001", facil="MINST1006")]
    entry = _room_detector_entry(secs, _room_collisions(secs), facility_used=False)
    assert entry["status"] == "active"            # rooms present
    assert entry["capacity"]["status"] == "inert"  # but no facility table


# --- M1: secondary meeting blocks are evaluated, not dropped ------------------

def test_room_conflict_detected_via_secondary_meeting_block():
    """Two sections whose FIRST blocks don't clash but whose SECOND blocks share a
    room at overlapping times ARE a real double-booking — previously missed because
    meetings[1:] were dropped at ingest (M1)."""
    a = {"term": 2248, "course": "BIO 3", "class_nbr": "100",
         "days": "M", "times": "8:00 AM - 9:00 AM", "room": "INST 1",
         "meetings": [{"days": "M", "times": "8:00 AM - 9:00 AM", "room": "INST 1"},
                      {"days": "W", "times": "2:00 PM - 3:00 PM", "room": "LAB 7"}]}
    b = {"term": 2248, "course": "CHEM 5", "class_nbr": "200",
         "days": "T", "times": "8:00 AM - 9:00 AM", "room": "INST 9",
         "meetings": [{"days": "T", "times": "8:00 AM - 9:00 AM", "room": "INST 9"},
                      {"days": "W", "times": "2:30 PM - 3:30 PM", "room": "LAB 7"}]}
    findings = _room_collisions([a, b])
    assert len(findings) == 1
    assert findings[0]["room"] == "LAB 7"
    assert set(findings[0]["courses"]) == {"BIO 3", "CHEM 5"}


def test_room_collisions_single_block_records_unchanged():
    """A flat single-meeting record (no 'meetings' key) still works via the fallback,
    so synthetic/import records with one block behave exactly as before."""
    a = _sec("BIO 3", "100", room="INST 1", days="MW", times="9:00 AM - 10:00 AM")
    b = _sec("CHEM 5", "200", room="INST 1", days="MW", times="9:30 AM - 10:30 AM")
    findings = _room_collisions([a, b])
    assert len(findings) == 1 and findings[0]["room"] == "INST 1"


def test_section_meetings_by_course_unions_all_blocks():
    """The time-block collision detector's per-course meeting map now includes EVERY
    block of a section, so a clash on a secondary block is seen (M1)."""
    recs = [{"course": "BIO 3", "days": "M", "times": "8:00 AM - 9:00 AM",
             "meetings": [{"days": "M", "times": "8:00 AM - 9:00 AM"},
                          {"days": "F", "times": "1:00 PM - 2:00 PM"}]}]
    by_course = _section_meetings_by_course(recs)
    assert {b[0] for b in by_course["BIO 3"][0]} == {"M", "F"}


# --- co-scheduled (stacked) suppression: the live API has no Comb Sects ID -----

def _seci(course, class_nbr, *, instr="", **kw):
    """A flat section record with an instructor (the live fetch's stand-in for a
    combined-section id, which the live schedule API does not expose)."""
    r = _sec(course, class_nbr, **kw)
    r["instructor"] = instr
    return r


def test_stacked_same_instructor_same_room_not_flagged():
    """One instructor teaching multiple course numbers in the same room at the same
    time is an intentional STACKED offering, not a double-booking. With no
    Comb Sects ID on live data, a shared real instructor is the signal."""
    secs = [_seci("ART 204", "12089", facil="MAMP212", instr="J SMITH"),
            _seci("ART 205", "16121", facil="MAMP212", instr="J SMITH")]
    assert _room_collisions(secs) == []


def test_shared_staff_placeholder_still_flagged():
    """Two sections that merely share the 'STAFF' placeholder are NOT a known
    co-scheduled meeting — a real double-booking must still surface."""
    secs = [_seci("ACCTG 001", "30001", facil="MINST1006", instr="STAFF"),
            _seci("ENGL 101", "30002", facil="MINST1006", instr="STAFF")]
    out = _room_collisions(secs)
    assert len(out) == 1 and out[0]["kind"] == "double_book"


def test_different_instructors_same_room_flagged():
    """Two DIFFERENT instructors in one room at overlapping times is a genuine
    double-booking — never suppressed by the co-scheduled rule."""
    secs = [_seci("BIOTECH 002", "30001", facil="MCMS006", instr="M RAHMAN"),
            _seci("BIOTECH 003", "30002", facil="MCMS006", instr="C ARORA")]
    assert len(_room_collisions(secs)) == 1


# --- program scope: the live fetch is campus-wide, the report is one program ---

def test_program_scope_keeps_pair_touching_the_program():
    secs = [_sec("BIOL 003", "1", facil="MINST1006"),
            _sec("CHEM 101", "2", facil="MINST1006")]
    # unscoped (default) still flags it
    assert len(_room_collisions(secs)) == 1
    # scoped to a set containing ONE of the two -> still reported
    assert len(_room_collisions(secs, program_courses={"BIOL 003"})) == 1


def test_program_scope_drops_pair_outside_the_program():
    secs = [_sec("BIOL 003", "1", facil="MINST1006"),
            _sec("CHEM 101", "2", facil="MINST1006")]
    # neither course is in the program -> campus-wide noise, dropped
    assert _room_collisions(secs, program_courses={"DANCE 100"}) == []


def test_room_capacity_scoped_to_program():
    facility = load_facility(FAC)
    secs = [_sec("ACCTG 001", "30001", facil="MINST1006", tot=45),   # over by 15
            _sec("ENGL 101", "30002", facil="MINST1006", tot=45)]    # over by 15
    # both over capacity unscoped
    assert len(_room_capacity_findings(secs, facility)) == 2
    # scoping to one program course keeps only that one
    assert len(_room_capacity_findings(secs, facility,
                                       program_courses={"ACCTG 001"})) == 1
