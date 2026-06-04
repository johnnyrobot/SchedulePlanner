r"""Tests for the tolerant IR/PeopleSoft enrollment ADAPTER (sources/enrollment_ir.py).

The adapter normalizes real LACCD export shapes (CSV with a 'Class Number' column
and a "2024 Fall" string term; xlsx on a 'Data and formulas' sheet with
'Class Status'/'Comb Sects ID'; meeting-pattern grain) into the SAME
(int term, bare-CRN) -> {Cap,Tot,Wait} map the strict reader produces, so the
existing enrich_sections join + engine.analyze detectors light up on real data.

The load-bearing proof is `test_activation_flips_detectors_inert_to_active`:
real-shaped export -> enrich -> build_sections_df -> engine.analyze flips
modality_mismatch / under_supply from EMPTY (inert) to POPULATED (active).

OFFLINE: pure file reads, no network. Committed fixtures are synthetic + PII-free.
"""
import pathlib

import pandas as pd
import pytest

import engine
from sources import enrollment, mapping
from sources.enrollment_ir import (load_ir_export, load_ir_export_with_report,
                                    parse_term)
from sources.http import SourceDataError

REPO = pathlib.Path(__file__).resolve().parent.parent
CSV = str(REPO / "files" / "lamc_ir_export_sample.csv")
XLSX = str(REPO / "files" / "lamc_ir_export_sample.xlsx")
STRICT = str(REPO / "files" / "lamc_sample_enrollment.xlsx")


# --- parse_term: crosswalk + round-trip vs engine decoders -----------------

@pytest.mark.parametrize("text,code", [
    ("2024 Fall", 2248), ("Fall 2024", 2248), ("2025 Spring", 2252),
    ("2023 Summer", 2236), ("2024 Winter", 2241), ("2025 Fall", 2258),
    ("2248", 2248), (2248, 2248), ("2248.0", 2248),
])
def test_parse_term_normalizes_to_peoplesoft_code(text, code):
    assert parse_term(text) == code


@pytest.mark.parametrize("year,season", [
    (2024, "Fall"), (2025, "Spring"), (2023, "Summer"), (2024, "Winter"),
])
def test_parse_term_round_trips_with_engine_decoders(year, season):
    """The crosswalk is the exact inverse of engine.season_of_code/year_of_code,
    so it can never drift from the term model the engine uses."""
    code = parse_term(f"{year} {season}")
    assert engine.season_of_code(code) == season
    assert engine.year_of_code(code) == year


def test_parse_term_blank_or_unparseable_raises():
    with pytest.raises(SourceDataError):
        parse_term("")
    with pytest.raises(SourceDataError):
        parse_term("not a term")


# --- CSV real-shape ingest -------------------------------------------------

def _counts_only(v):
    return {k: v[k] for k in ("Cap Enrl", "Tot Enrl", "Wait Tot")}


def test_csv_aliases_term_string_and_dedups_meeting_patterns():
    m, summary = load_ir_export_with_report(CSV)
    # 'Class Number' aliased, "2024 Fall" -> 2248, the duplicate 30001 row deduped.
    assert _counts_only(m[(2248, "30001")]) == {"Cap Enrl": 40, "Tot Enrl": 10, "Wait Tot": 0}
    assert _counts_only(m[(2248, "30002")]) == {"Cap Enrl": 40, "Tot Enrl": 40, "Wait Tot": 18}
    assert _counts_only(m[(2248, "30003")]) == {"Cap Enrl": 35, "Tot Enrl": 33, "Wait Tot": 3}
    assert summary == {"rows_in": 4, "sections_out": 3, "dropped_cancelled": 0,
                       "combined_rows": 0, "terms": [2248], "total_tot_enrl": 83}


# --- FF5: IR Component (contact category) preserved (capture-only) ----------

def test_csv_carries_component():
    # The CSV fixture's first dedup-winning row for 30001 is the LEC component.
    m = load_ir_export(CSV)
    assert m[(2248, "30001")]["Component"] == "LEC"
    assert m[(2248, "30003")]["Component"] == "LEC"


def test_component_fails_open_when_column_absent():
    # The xlsx adapter fixture has NO Component column -> fail open to "" (never
    # a crash, never invented).
    m = load_ir_export(XLSX)
    assert m[(2248, "40001")]["Component"] == ""


# --- xlsx real-shape ingest (sheet pick, cancelled, combined) --------------

def test_xlsx_picks_data_sheet_drops_cancelled_handles_combined():
    m, summary = load_ir_export_with_report(XLSX)
    assert set(m) == {(2248, "40001"), (2248, "40002"),
                      (2248, "40004"), (2248, "40005")}
    assert m[(2248, "40002")]["Wait Tot"] == 22
    # HIST 40003 was Cancelled -> dropped; the duplicate 40004 row deduped.
    assert (2248, "40003") not in m
    assert summary["dropped_cancelled"] == 1
    assert summary["combined_rows"] == 2          # 40004 + 40005 share Comb Sects ID
    assert summary["sections_out"] == 4
    assert summary["total_tot_enrl"] == 90


def test_xlsx_explicit_sheet_argument():
    m = load_ir_export(XLSX, sheet="Data and formulas")
    assert (2248, "40001") in m


# --- back-compat with the strict committed fixture -------------------------

def test_back_compat_matches_strict_loader_on_committed_fixture():
    """The adapter is a superset: on the strict fixture format (sheet 'sections',
    numeric Term, 'Class Nbr') it returns exactly what load_enrollment returns."""
    assert load_ir_export(STRICT) == enrollment.load_enrollment(STRICT)


# --- ACTIVATION: inert -> active end to end (the headline proof) -----------

def _schedule_records():
    # Live-shaped records (decorated class_nbr) whose (term, CRN) overlap the CSV
    # fixture, so the join lands. Mirrors schedule.fetch_sections output keys.
    return [
        {"term": 2248, "course": "ACCTG 001", "class_nbr": "30001 (LEC)",
         "days": "MW", "times": "9:00 AM - 10:25 AM", "status": ""},
        {"term": 2248, "course": "ENGL 101", "class_nbr": "30002 (LEC)",
         "days": "TR", "times": "9:00 AM - 10:25 AM", "status": ""},
        {"term": 2248, "course": "MATH 227", "class_nbr": "30003 (LEC)",
         "days": "MW", "times": "11:00 AM - 12:25 PM", "status": ""},
    ]


_PROG = pd.DataFrame([{"Program Code": "TEST", "Course ID": c}
                      for c in ("ACCTG 001", "ENGL 101", "MATH 227")])


def test_activation_flips_detectors_inert_to_active():
    records = _schedule_records()

    # Baseline (no enrollment): Cap/Tot/Wait default 0 -> both detectors INERT.
    base = mapping.build_sections_df(records)
    base_out = engine.analyze(base, _PROG, n_terms=1)
    assert base_out["modality_mismatch"] == []
    assert base_out["under_supply"] == []

    # With the real-shaped export joined: the detectors light up (ACTIVE).
    enriched = enrollment.enrich_sections(records, load_ir_export(CSV))
    active = mapping.build_sections_df(enriched)
    out = engine.analyze(active, _PROG, n_terms=1)
    assert {x["course"] for x in out["modality_mismatch"]} == {"ACCTG 001"}  # fill 25%
    assert any(x["course"] == "ENGL 101" and x["waitlisted"] == 18
               for x in out["under_supply"])


# --- error paths (dirty real exports) --------------------------------------

def test_missing_required_column_raises_naming_file(tmp_path):
    bad = tmp_path / "bad.csv"
    pd.DataFrame([{"Term": "2024 Fall", "Class Number": 30001,
                   "Cap Enrl": 40, "Tot Enrl": 10}]).to_csv(bad, index=False)
    with pytest.raises(SourceDataError) as exc:
        load_ir_export(str(bad))
    assert "bad.csv" in str(exc.value) and "Wait Tot" in str(exc.value)


def test_footer_row_raises_not_raw_valueerror(tmp_path):
    bad = tmp_path / "footer.csv"
    pd.DataFrame([
        {"Term": "2024 Fall", "Class Number": 30001, "Cap Enrl": 40,
         "Tot Enrl": 10, "Wait Tot": 0},
        {"Term": "", "Class Number": "Total", "Cap Enrl": 40,
         "Tot Enrl": 10, "Wait Tot": 0},   # subtotal/footer row
    ]).to_csv(bad, index=False)
    with pytest.raises(SourceDataError) as exc:
        load_ir_export(str(bad))
    assert "invalid literal" not in str(exc.value)


# --- PII: ignored on input, never in the map; fixtures are PII-free ---------

def test_pii_columns_are_ignored_not_emitted(tmp_path):
    withpii = tmp_path / "withpii.csv"
    pd.DataFrame([{"Term": "2024 Fall", "Class Number": 30001, "Cap Enrl": 40,
                   "Tot Enrl": 10, "Wait Tot": 0,
                   "Name": "DOE,JANE", "Emails": "jane@example.invalid"}]
                 ).to_csv(withpii, index=False)
    m = load_ir_export(str(withpii))
    assert _counts_only(m[(2248, "30001")]) == {"Cap Enrl": 40, "Tot Enrl": 10, "Wait Tot": 0}
    # the map values carry only counts + the (non-PII) Component contact category
    # -- no instructor PII (Name/Emails) leaks through.
    for counts in m.values():
        assert set(counts) == {"Cap Enrl", "Tot Enrl", "Wait Tot", "Component"}


@pytest.mark.parametrize("path", [CSV, XLSX])
def test_committed_fixtures_carry_no_pii(path):
    if path.endswith(".csv"):
        cols = list(pd.read_csv(path, nrows=0).columns)
    else:
        cols = [c for s in pd.ExcelFile(path).sheet_names
                for c in pd.ExcelFile(path).parse(s, nrows=0).columns]
    for pii in ("Name", "Emails", "Email", "Instructor"):
        assert pii not in cols, f"committed fixture {path} must not carry PII column {pii!r}"
