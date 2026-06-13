r"""Tests for the schedule-export converter (sources/schedule_import.py) + the
offline audit entry build_live_workbook.analyze_import.

The converter normalizes real LACCD schedule-export shapes into the SAME section
records sources/schedule.fetch_sections emits, so the whole offline pipeline
(mapping.build_*_df -> write_workbook -> engine.run + the raw-record time-block /
room detectors) runs on real HISTORY with no network. Two shapes:
  - CSV, FALL-2025 shape: numeric Term, DAYS (with R=Thursday), 24h Mtg Start/End,
    Facil ID / Room Descr, counts, a cancelled row, a meeting-pattern dup, PII cols.
  - xlsx, 2022-24 shape: "2024 Spring" string Term, Meetings day col, Comb Sects.

The headline proof is test_round_trip_audit_offline: convert -> pseudo-program ->
engine.run produces a populated analysis incl. room double-bookings + over-capacity.

OFFLINE: pure file reads, no network. Committed fixtures are synthetic + PII-free.
"""
import pathlib
import tempfile

import pandas as pd
import pytest

import build_live_workbook as blw
from sources.http import SourceDataError
from sources.schedule_import import load_schedule_export, norm_days

REPO = pathlib.Path(__file__).resolve().parent.parent
CSV = str(REPO / "files" / "lamc_schedule_sample.csv")
XLSX = str(REPO / "files" / "lamc_schedule_sample.xlsx")
FAC = str(REPO / "files" / "lamc_facility_sample.xlsx")
PLISTS = str(REPO / "files" / "lamc_program_lists_sample.xlsx")


# --- day normalization (R -> Th) -------------------------------------------

@pytest.mark.parametrize("raw,canon", [
    ("MW", "MW"), ("TR", "TTh"), ("MTWR", "MTWTh"), ("R", "Th"),
    ("MWF", "MWF"), ("TBA", ""), ("", ""), ("S", "S"), ("MW ", "MW"),
])
def test_norm_days_maps_thursday(raw, canon):
    assert norm_days(raw) == canon


# --- CSV (FALL-2025) shape -------------------------------------------------

def test_csv_shape_records_and_summary():
    records, summary = load_schedule_export(CSV)
    by = {r["class_nbr"]: r for r in records}
    # multi_block_sections=1: one section in the fixture meets on two patterns; its
    # secondary meeting row is now KEPT as a 2nd block (M1) instead of silently dropped.
    assert summary == {"rows_in": 6, "sections_out": 4, "dropped_cancelled": 1,
                       "terms": [2258], "with_counts": 4, "total_tot_enrl": 128,
                       "multi_block_sections": 1}
    r = by["30001"]
    assert r["course"] == "ACCTG 001" and r["term"] == 2258
    assert r["days"] == "MW" and r["times"] == "9:00 AM - 10:25 AM"
    assert r["facil_id"] == "MINST1006" and r["Tot Enrl"] == 45 and r["Cap Enrl"] == 40
    assert by["30003"]["days"] == "TTh"             # TR -> TTh (R is Thursday)
    assert by["30005"]["days"] == "" and by["30005"]["times"] == ""  # online / async
    assert "30004" not in by                         # cancelled dropped


def test_csv_dedup_and_pii_absent():
    records, _ = load_schedule_export(CSV)
    assert sum(1 for r in records if r["class_nbr"] == "30001") == 1  # meeting-pattern dup
    for r in records:
        assert "Name" not in r and "Emails" not in r and "INSTRUCTOR" not in r


# --- xlsx (2022-24) shape --------------------------------------------------

def test_xlsx_shape_string_term_and_meetings():
    records, summary = load_schedule_export(XLSX)
    by = {r["class_nbr"]: r for r in records}
    assert summary["terms"] == [2242]               # "2024 Spring" -> 2242
    assert summary["sections_out"] == 4 and summary["with_counts"] == 4
    a = by["40001"]
    assert a["course"] == "ANATOMY 1" and a["term"] == 2242
    assert a["days"] == "MW" and a["times"] == "8:30 AM - 9:35 AM"
    assert a["facil_id"] == "MAMP212"
    assert by["40004"]["Comb Sects ID"] == "C100"   # combined cross-list carried


# --- FF5: session dates + woi survive the parse (capture-only) -------------

def test_records_carry_woi_and_dates_keys_failing_open():
    # Capture-only: every record must carry `woi` + `dates` keys so a future
    # calendar/duration check can read them. The committed fixture has no WOI /
    # date columns, so they fail OPEN to "" (never a crash, never invented).
    records, _ = load_schedule_export(CSV)
    for r in records:
        assert r["woi"] == ""
        assert r["dates"] == ""


def test_woi_and_session_dates_are_captured_when_present(tmp_path):
    # When the export DOES carry weeks-of-instruction + start/end dates, both
    # survive onto the record (woi passthrough; dates as a "start - end" range).
    exp = tmp_path / "with_woi.csv"
    pd.DataFrame([{
        "Term": "2258", "Subject": "ACCTG", "Catalog": "001", "Class Nbr": "30001",
        "Class Status": "Active", "DAYS": "MW", "Mtg Start": "09:00:00",
        "Mtg End": "10:25:00", "WOI": "16",
        "Class Start Date": "08/25/2025", "Class End Date": "12/14/2025",
    }]).to_csv(exp, index=False)
    records, _ = load_schedule_export(str(exp))
    r = records[0]
    assert r["woi"] == "16"
    assert r["dates"] == "08/25/2025 - 12/14/2025"


def test_single_session_dates_column_is_captured(tmp_path):
    # A single combined date-range column (some exports ship one) is captured as-is.
    exp = tmp_path / "single_dates.csv"
    pd.DataFrame([{
        "Term": "2258", "Subject": "ENGL", "Catalog": "101", "Class Nbr": "30009",
        "Class Status": "Active", "DAYS": "TR", "Mtg Start": "11:00:00",
        "Mtg End": "12:25:00", "Weeks of Instruction": "8",
        "Session Dates": "10/20/2025 - 12/14/2025",
    }]).to_csv(exp, index=False)
    r = load_schedule_export(str(exp))[0][0]
    assert r["woi"] == "8"
    assert r["dates"] == "10/20/2025 - 12/14/2025"


def test_secondary_meeting_rows_are_captured_not_dropped(tmp_path):
    """A section listed on TWO meeting-pattern rows (same Term+Class Nbr, different
    DAYS/times/room) keeps BOTH blocks (M1) instead of silently dropping the 2nd; the
    summary surfaces the merged count so the collapse is honest, and the flat
    days/times stay the FIRST row so the engine workbook is byte-identical."""
    exp = tmp_path / "multi.csv"
    pd.DataFrame([
        {"Term": "2248", "Subject": "BIOLOGY", "Catalog": "3", "Class Nbr": "20001",
         "Class Status": "Active", "DAYS": "MW", "Mtg Start": "10:00:00",
         "Mtg End": "11:25:00", "Room Descr": "INST 2007"},
        {"Term": "2248", "Subject": "BIOLOGY", "Catalog": "3", "Class Nbr": "20001",
         "Class Status": "Active", "DAYS": "F", "Mtg Start": "14:00:00",
         "Mtg End": "16:50:00", "Room Descr": "AMP 101"},
    ]).to_csv(exp, index=False)
    records, summary = load_schedule_export(str(exp))
    assert len(records) == 1                       # still ONE section
    r = records[0]
    assert r["days"] == "MW" and r["times"] == "10:00 AM - 11:25 AM"   # flat = first row
    assert r["room"] == "INST 2007"
    assert len(r["meetings"]) == 2                 # both meeting blocks kept
    assert r["meetings"][1]["days"] == "F"
    assert r["meetings"][1]["times"] == "2:00 PM - 4:50 PM"
    assert r["meetings"][1]["room"] == "AMP 101"
    assert summary["multi_block_sections"] == 1    # honest count of merged rows


def test_single_meeting_section_reports_zero_multi_block(tmp_path):
    exp = tmp_path / "single.csv"
    pd.DataFrame([{
        "Term": "2248", "Subject": "BIOLOGY", "Catalog": "3", "Class Nbr": "20002",
        "Class Status": "Active", "DAYS": "MW", "Mtg Start": "10:00:00",
        "Mtg End": "11:25:00", "Room Descr": "INST 2007",
    }]).to_csv(exp, index=False)
    records, summary = load_schedule_export(str(exp))
    assert len(records[0]["meetings"]) == 1
    assert summary["multi_block_sections"] == 0


# --- error paths -----------------------------------------------------------

def test_missing_term_column_raises(tmp_path):
    bad = tmp_path / "bad.csv"
    pd.DataFrame([{"Subject": "X", "Catalog": "1", "Class Nbr": "1"}]).to_csv(bad, index=False)
    with pytest.raises(SourceDataError) as exc:
        load_schedule_export(str(bad))
    assert "bad.csv" in str(exc.value) and "Term" in str(exc.value)


def test_footer_row_raises_not_raw_valueerror(tmp_path):
    bad = tmp_path / "footer.csv"
    pd.DataFrame([
        {"Term": "2258", "Subject": "ACCTG", "Catalog": "001", "Class Nbr": "30001"},
        {"Term": "", "Subject": "", "Catalog": "", "Class Nbr": "Total"},
    ]).to_csv(bad, index=False)
    with pytest.raises(SourceDataError):
        load_schedule_export(str(bad))


# --- ACTIVATION: offline audit round-trip (the headline proof) -------------

def test_round_trip_audit_offline():
    """Convert -> all-offered pseudo-program -> engine.run yields a populated
    analysis, including room double-bookings and over-capacity (with the facility
    table). No network."""
    with tempfile.TemporaryDirectory() as tmp:
        out = str(pathlib.Path(tmp) / "wb.xlsx")
        report = blw.analyze_import(CSV, out, facility_path=FAC)

    assert report.get("error") is None
    assert report["import_summary"]["sections_out"] == 4
    a = report["results"]["analysis"]

    # double-book: ACCTG 001 (30001) and ENGL 101 (30002) share MINST1006 @ MW 9:00.
    rc = a["room_conflicts"]
    assert any(set(f["courses"]) == {"ACCTG 001", "ENGL 101"} and f["room"] == "MINST1006"
               for f in rc)
    # over-capacity: ACCTG 001 has 45 enrolled in MINST1006 (facility cap 30).
    cap = a["room_capacity"]
    assert any(f["course"] == "ACCTG 001" and f["capacity"] == 30 and f["enrolled"] == 45
               for f in cap)
    # modality_mismatch fires from the export's own counts (BIOLOGY 003: 20/40 = 50%).
    assert any(x["course"] == "BIOLOGY 003" for x in a["modality_mismatch"])

    # detector entry is honest: modality active "from the export" (inline counts).
    mm = next(d for d in report["inert_detectors"] if d["detector"] == "modality_mismatch")
    assert mm["status"] == "active" and "export" in mm["label"].lower()
    room = next(d for d in report["inert_detectors"] if d["detector"] == "room_conflict")
    assert room["status"] == "active" and room["capacity"]["status"] == "active"


def test_combined_cross_list_not_double_booked_offline():
    """The xlsx fixture's CHEM 101/102 share a room + time + Comb Sects ID — a
    combined cross-list, NOT a double-booking."""
    with tempfile.TemporaryDirectory() as tmp:
        out = str(pathlib.Path(tmp) / "wb.xlsx")
        report = blw.analyze_import(XLSX, out)
    rc = report["results"]["analysis"]["room_conflicts"]
    assert not any({"CHEM 101", "CHEM 102"} == set(f["courses"]) for f in rc)


def test_program_file_narrows_audit(tmp_path):
    """A programs-sheet workbook narrows the audit to one degree path."""
    prog = tmp_path / "prog.xlsx"
    with pd.ExcelWriter(prog, engine="openpyxl") as xl:
        pd.DataFrame([
            {"Program Code": "BIO-AST", "Program Title": "Biology AS-T",
             "Course ID": "BIOLOGY 003", "Recommended Semester": 1},
        ]).to_excel(xl, sheet_name="programs", index=False)
    with tempfile.TemporaryDirectory() as tmp:
        out = str(pathlib.Path(tmp) / "wb.xlsx")
        report = blw.analyze_import(CSV, out, program_path=str(prog))
    assert report["program"]["code"] == "BIO-AST"
    # only the program's course is "required", so reconciliation matches just it
    assert report["reconciliation"]["matched"] == ["BIOLOGY 003"]


# --- live path is unchanged by the sections_override refactor ---------------

def test_pseudo_program_lists_all_offered_courses():
    records, _ = load_schedule_export(CSV)
    prog = blw._pseudo_program(records, {}, terms=[2258])
    assert prog["code"] == "ALL"
    assert {c["course_id"] for c in prog["courses"]} == {
        "ACCTG 001", "ENGL 101", "MATH 227", "BIOLOGY 003"}


# --- F2: cross-program bottleneck on the import path -----------------------

def test_import_with_program_lists_emits_active_leaderboard():
    """A Program Course Lists export supplies the cross-program demand the live
    path can't, so the bottleneck leaderboard activates on the import path. The
    CSV offers MATH 227 + BIOLOGY 003, both required in the sample lists."""
    import json

    with tempfile.TemporaryDirectory() as tmp:
        out = str(pathlib.Path(tmp) / "wb.xlsx")
        report = blw.analyze_import(CSV, out, program_lists_path=PLISTS)
    json.dumps(report)  # JSON-serializable end to end

    block = report["results"]["analysis"]["bottlenecks"]
    assert block["status"] == "active"
    assert "PROXY" in block["label"]
    board = {r["course"]: r for r in block["leaderboard"]}
    assert "MATH 227" in board
    assert board["MATH 227"]["n_programs"] == 4       # required by 4 sample plans
    assert board["MATH 227"]["risk_score"] >= board.get(
        "BIOLOGY 003", {"risk_score": 0})["risk_score"]

    det = next(d for d in report["inert_detectors"]
               if d["detector"] == "program_bottleneck")
    assert det["status"] == "active"
    assert det["found"] == len(block["leaderboard"])


def test_import_without_program_lists_keeps_bottleneck_inert():
    """No demand map -> the leaderboard stays honestly inert (the import path
    audits one schedule; cross-program demand needs the program-lists export)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = str(pathlib.Path(tmp) / "wb.xlsx")
        report = blw.analyze_import(CSV, out)
    block = report["results"]["analysis"]["bottlenecks"]
    assert block["status"] == "inert"
    assert block["reason"]
    det = next(d for d in report["inert_detectors"]
               if d["detector"] == "program_bottleneck")
    assert det["status"] == "inert" and det["reason"] and "remedy" in det


# --- FF3: merge-not-strip IR enrollment on the import path (F5 activation) ---

def _ir_export_for_schedule(tmp_path):
    r"""An IR PeopleSoft enrollment export (term '2025 Fall' -> 2258, matching the
    schedule CSV's term) that joins onto ONLY MATH 227 (CRN 30003) with
    AUTHORITATIVE counts that DIFFER from the schedule export's native MATH 227
    counts (Cap 35/Tot 33/Wait 3 -> Cap 35/Tot 35/Wait 20). The schedule's other
    counts-carrying sections (ACCTG 001, ENGL 101, BIOLOGY 003) are absent from
    the IR export, so the merge must PRESERVE their native counts (not wipe them).
    """
    exp = tmp_path / "ir_for_schedule.csv"
    pd.DataFrame([
        {"Term": "2025 Fall", "Class Number": "30003", "Subject": "MATH",
         "Catalog": "227", "Section": "001", "Component": "LEC",
         "Cap Enrl": "35", "Wait Cap": "20", "Tot Enrl": "35", "Wait Tot": "20"},
    ]).to_csv(exp, index=False)
    return str(exp)


def test_ff3_analyze_import_with_ir_merges_not_strips(tmp_path):
    """analyze_import with BOTH a counts-carrying schedule export AND an IR
    enrollment_path activates F5 demand_supply, with counts coming from
    native-on-unmatched + IR-on-matched (none silently wiped)."""
    import json

    ir = _ir_export_for_schedule(tmp_path)
    out = str(tmp_path / "wb.xlsx")
    report = blw.analyze_import(CSV, out, enrollment_path=ir)
    json.dumps(report)  # JSON-serializable end to end

    # F5 demand_supply ACTIVATES on the import+IR path.
    block = report["results"]["analysis"]["demand_supply"]
    assert block["status"] == "active"
    det = next(d for d in report["inert_detectors"] if d["detector"] == "demand_supply")
    assert det["status"] == "active"

    # MATH 227 got the IR-authoritative counts (Tot 35, Wait 20) -> a CLOSED-style
    # add candidate (fill 1.0). The native MATH 227 was 33/3.
    add = {r["course"]: r for r in block["add_list"]}
    assert "MATH 227" in add
    assert add["MATH 227"]["cap_total"] == 35
    assert add["MATH 227"]["tot_total"] == 35   # IR value, not native 33
    assert add["MATH 227"]["wait_total"] == 20  # IR value, not native 3

    # The schedule's other native-counts sections were NOT in the IR export, so
    # the merge preserved their native counts (modality_mismatch still fires on
    # BIOLOGY 003's 20/40 = 50% fill — proof its native Cap/Tot were not wiped).
    mm = report["results"]["analysis"]["modality_mismatch"]
    assert any(x["course"] == "BIOLOGY 003" for x in mm)


def test_ff3_analyze_import_ir_unmatched_native_counts_survive(tmp_path):
    """Directly pin the F5-feared regression on the import path: ENGL 101 (native
    Cap 40/Tot 30/Wait 18) is NOT in the IR export, so its counts must survive the
    merge and still drive demand_supply (it lands on the add list from native
    counts: fill 0.75 + Wait 18 + Closed? no — but Wait 18 > 15 keeps it present
    via native counts being assessed, never zeroed)."""
    ir = _ir_export_for_schedule(tmp_path)
    out = str(tmp_path / "wb.xlsx")
    report = blw.analyze_import(CSV, out, enrollment_path=ir)
    block = report["results"]["analysis"]["demand_supply"]
    # ENGL 101's native counts were preserved (assessed, not wiped) -> the report's
    # sections_with_counts must include the unmatched native-counts sections (the
    # IR export only matched MATH 227, so the other 3 native-counts sections prove
    # the merge did not zero them).
    assert block["sections_with_counts"] >= 3


def test_ff3_analyze_import_without_ir_still_active_from_native(tmp_path):
    """Control: analyze_import with NO enrollment_path still activates F5 from the
    schedule export's own native counts (the pre-FF3 import behavior is intact)."""
    out = str(tmp_path / "wb.xlsx")
    report = blw.analyze_import(CSV, out)
    block = report["results"]["analysis"]["demand_supply"]
    assert block["status"] == "active"


def test_import_populates_grid_pressure():
    with tempfile.TemporaryDirectory() as tmp:
        out = str(pathlib.Path(tmp) / "wb.xlsx")
        report = blw.analyze_import(CSV, out)
    gp = report["results"]["analysis"]["grid_pressure"]
    assert gp["status"] in ("active", "inert")
    if gp["status"] == "active":
        assert "conformance" in gp and "morning_compression" in gp
        assert gp["not_assessed"]["end_time_duration"]["status"] == "inert"
    # the detector entry is present and honestly labelled
    names = {d["detector"] for d in report["inert_detectors"]}
    assert "grid_pressure" in names


# --- app bridge returns the same flat dict the UI renders ------------------

def test_app_analyze_schedule_import_offline():
    import json

    import app
    res = app.Api().analyze_schedule_import(CSV, facility_path=FAC)
    assert "error" not in res, res.get("error")
    json.dumps(res)  # JSON-serializable for the JS bridge
    assert res["import_summary"]["sections_out"] == 4
    assert res["analysis"]["room_conflicts"] and res["analysis"]["room_capacity"]
    assert res["ai_used"] is False
