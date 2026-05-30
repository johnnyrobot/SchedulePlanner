"""Privacy invariants codifying PRD N1-N4 (m8-A t5).

PRD §7.1:
  * N1 — No student-level data processed.
  * N2 — No PII persisted; an instructor field may be loaded transiently by the
        live schedule source but is dropped at mapping (before workbook/results/AI).
  * N3 — No network calls except optional localhost Ollama.
  * N4 — No telemetry, analytics, or external logging.

The tests here are FULLY OFFLINE and add no `live` mark.

  * PII (N1/N2): feed the engine a workbook AND a CSV directory whose sections
    sheet carries must-exclude PII columns (Instructor / ID / Name / Email)
    alongside the required columns; assert NONE of those column names or their
    values appear anywhere in the serialized results.
  * No-network (N3/N4): sabotage httpx.Client, urllib.request.urlopen, AND
    socket.socket so any Python-level network/socket attempt raises; assert
    engine.run on the bundled demo still completes — proving engine.run does no
    network IO of its own.
  * Network-outside-engine: a structural guard that the live entry points keep
    their network behind build()/sources via an injected client, and that
    engine.run's source never references httpx or urllib.
"""
from __future__ import annotations

import inspect
import json
import socket
import urllib.request

import httpx
import pandas as pd
import pytest

import build_live_workbook
import engine
from sources import mapping, schedule

# Column names that must NEVER be loaded/emitted (instructor + student PII).
PII_COLUMNS = ["Instructor", "ID", "Name", "Email"]
# Concrete fabricated PII values planted in the fixture; none may surface.
PII_VALUES = [
    "Jane Q. Public", "88123456", "John A. Smith", "99000111",
    "jpublic@laccd.edu", "jsmith@laccd.edu",
]


def _sections_with_pii():
    """Required engine columns PLUS planted PII columns/values."""
    return pd.DataFrame([
        {"Term": 2248, "CLASS": "CS 101", "Class Status": "Active",
         "Cap Enrl": 30, "Tot Enrl": 10, "Wait Tot": 0,
         "Instructor": "Jane Q. Public", "ID": "88123456",
         "Name": "Jane Q. Public", "Email": "jpublic@laccd.edu"},
        {"Term": 2252, "CLASS": "CS 101", "Class Status": "Active",
         "Cap Enrl": 30, "Tot Enrl": 10, "Wait Tot": 0,
         "Instructor": "John A. Smith", "ID": "99000111",
         "Name": "John A. Smith", "Email": "jsmith@laccd.edu"},
        {"Term": 2248, "CLASS": "CS 102", "Class Status": "Active",
         "Cap Enrl": 30, "Tot Enrl": 5, "Wait Tot": 0,
         "Instructor": "Jane Q. Public", "ID": "88123456",
         "Name": "Jane Q. Public", "Email": "jpublic@laccd.edu"},
    ])


def _catalog():
    return pd.DataFrame([
        {"Course ID": "CS 101", "Units": 3, "Prerequisites (structured)": ""},
        {"Course ID": "CS 102", "Units": 3,
         "Prerequisites (structured)": "(CS 101)"},
    ])


def _programs():
    return pd.DataFrame([
        {"Program Code": "P", "Program Title": "Test Program",
         "Course ID": "CS 101", "Recommended Semester": 1},
        {"Program Code": "P", "Program Title": "Test Program",
         "Course ID": "CS 102", "Recommended Semester": 2},
    ])


def _assert_no_pii(results):
    """No PII column name or value may appear anywhere in the results JSON."""
    blob = json.dumps(results)
    for col in PII_COLUMNS:
        assert col not in blob, (
            f"PII column name {col!r} leaked into results JSON (PRD N2)"
        )
    for val in PII_VALUES:
        assert val not in blob, (
            f"PII value {val!r} leaked into results JSON (PRD N1/N2)"
        )


# --------------------------------------------------------------------------- #
# N1/N2 — PII never surfaces in results (workbook path).                       #
# --------------------------------------------------------------------------- #
def test_pii_columns_not_in_results_workbook(tmp_path):
    wb = tmp_path / "pii.xlsx"
    with pd.ExcelWriter(wb) as xl:
        _sections_with_pii().to_excel(xl, sheet_name="sections", index=False)
        _catalog().to_excel(xl, sheet_name="catalog", index=False)
        _programs().to_excel(xl, sheet_name="programs", index=False)

    results = engine.run(str(wb))
    # sanity: the run actually produced analysis for our program
    assert "P" in results["programs"]
    _assert_no_pii(results)


# --------------------------------------------------------------------------- #
# N1/N2 — same invariant via the CSV-directory ingestion path.                 #
# --------------------------------------------------------------------------- #
def test_pii_columns_not_in_results_csv_dir(tmp_path):
    _sections_with_pii().to_csv(tmp_path / "sections.csv", index=False)
    _catalog().to_csv(tmp_path / "catalog.csv", index=False)
    _programs().to_csv(tmp_path / "programs.csv", index=False)

    results = engine.run(str(tmp_path))
    assert "P" in results["programs"]
    _assert_no_pii(results)


def test_bundled_demo_results_carry_no_pii_tokens():
    """The shipped demo itself emits no instructor/student PII tokens."""
    results = engine.run(engine._default_data_path())
    blob = json.dumps(results)
    for col in PII_COLUMNS:
        assert col not in blob, f"{col!r} present in bundled demo results (PRD N2)"


# --------------------------------------------------------------------------- #
# N2 — instructor in the LIVE-SOURCE key shape is dropped at mapping.          #
# --------------------------------------------------------------------------- #
def test_live_source_instructor_dropped_through_mapping(make_client, tmp_path):
    """Real-key regression (N2): an instructor name arriving in the actual
    live-source shape (raw API ``instr`` -> schedule record ``instructor``) is
    loaded transiently by the schedule client, then DROPPED at the mapping stage.
    It must never reach the sections/catalog frames, the written workbook, or the
    engine results. Complements the tests above, which plant the capitalized
    ``Instructor`` workbook column rather than the live-source key shape."""
    SENTINEL = "Zzyzx, Q. PRIVACY-SENTINEL"
    listing = {
        "campuscode": "LAMC", "termcode": "2268",
        "subjects": [{"code": "CS", "courses": [{
            "subject": "CS", "catalogNbr": "101", "descr": "Intro", "units": "3.00",
            "sections": [{"classNbr": "10001 (LEC)", "status": "Open",
                "meetings": [{"days": "M", "times": "9 AM", "room": "X",
                              "instr": SENTINEL}],
                "relsections": [], "classType": ["INPER"]}]}]}],
    }
    records = schedule.fetch_sections(
        "LAMC", [2268], client=make_client({"/listing/LAMC/2268": listing}))

    # Honest about N2: the schedule record DOES carry the instructor under the
    # lowercase live-source key (this is why N2 says "dropped at mapping", not
    # "never loaded").
    assert any(r.get("instructor") == SENTINEL for r in records)

    # Mapping drops it: absent from both frames; the live keys are not columns.
    program = {"code": "P", "title": "P",
               "courses": [{"course_id": "CS 101", "recommended_semester": 1}]}
    sections = mapping.build_sections_df(records)
    catalog = mapping.build_catalog_df(records, program)
    for df in (sections, catalog):
        assert SENTINEL not in df.to_csv(index=False)
        assert "instructor" not in df.columns and "instr" not in df.columns

    # The persisted workbook and the engine results carry no trace of it.
    wb = tmp_path / "live.xlsx"
    mapping.write_workbook(records, program, str(wb))
    sheets = pd.read_excel(wb, sheet_name=None)
    assert SENTINEL not in "".join(df.to_csv(index=False) for df in sheets.values())
    assert SENTINEL not in json.dumps(engine.run(str(wb)))


# --------------------------------------------------------------------------- #
# N3/N4 — engine.run does no Python-level network IO.                          #
# --------------------------------------------------------------------------- #
def test_engine_run_does_no_network_io(monkeypatch):
    """Sabotage every network primitive; engine.run on the demo must still
    complete. If engine.run attempted any httpx/urllib/socket call it would
    raise here instead of returning results (PRD N3/N4)."""
    def boom(*args, **kwargs):
        raise AssertionError(
            "engine.run attempted a network/socket call (violates PRD N3/N4)"
        )

    monkeypatch.setattr(httpx, "Client", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(socket, "socket", boom)
    # Also block the lower-level connection helper urllib/httpx ultimately use.
    monkeypatch.setattr(socket, "create_connection", boom, raising=False)

    results = engine.run(engine._default_data_path())
    assert results["terms_in_data"] == 8
    assert results["programs"], "engine.run produced no program results offline"


def test_engine_run_offline_matches_normal_run(monkeypatch):
    """Determinism under network sabotage: the offline run equals a plain run
    (proves the network block changed nothing about engine behavior)."""
    normal = engine.run(engine._default_data_path())

    def boom(*args, **kwargs):
        raise AssertionError("network call attempted")
    monkeypatch.setattr(httpx, "Client", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom, raising=False)

    offline = engine.run(engine._default_data_path())
    assert json.dumps(offline, sort_keys=True) == json.dumps(normal, sort_keys=True)


# --------------------------------------------------------------------------- #
# Network-outside-engine — structural guards.                                  #
# --------------------------------------------------------------------------- #
def test_engine_module_does_not_import_network_libs():
    """engine.run's source must not reference httpx or urllib; network IO lives
    in build_live_workbook/sources, behind an injected client."""
    run_src = inspect.getsource(engine.run)
    assert "httpx" not in run_src
    assert "urllib" not in run_src
    assert "requests" not in run_src
    # The whole engine module references no network client library. Use bare
    # tokens (not "import httpx") so a `from httpx import Client` regression is
    # also caught.
    engine_src = inspect.getsource(engine)
    assert "httpx" not in engine_src
    assert "urllib" not in engine_src
    assert "requests" not in engine_src


def test_live_pipeline_keeps_network_behind_injected_client():
    """build_live_workbook.build()/analyze_live() expose a `client` seam so the
    network can be injected (and thus replaced/blocked); engine.run does not."""
    build_params = inspect.signature(build_live_workbook.build).parameters
    assert "client" in build_params, (
        "build() must accept an injectable client (network seam outside engine)"
    )
    analyze_params = inspect.signature(build_live_workbook.analyze_live).parameters
    assert "client" in analyze_params

    # engine.run has NO client seam — it never does network IO, so it needs none.
    assert "client" not in inspect.signature(engine.run).parameters


def test_build_live_workbook_uses_injected_client_no_network(
        lamc_routes, make_client, tmp_path, monkeypatch):
    """End-to-end: with an injected FakeClient, the live pipeline runs with the
    real network primitives sabotaged — proving every outbound call goes through
    the injected client, never a hidden direct httpx/urllib call."""
    def boom(*args, **kwargs):
        raise AssertionError("live pipeline made a direct (non-injected) network call")
    monkeypatch.setattr(httpx, "Client", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)

    client = make_client(lamc_routes)
    out = tmp_path / "live.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(out), client=client)

    assert report["error"] is None, report["error"]
    assert report["program"]["code"] == "BIOLOGY"
    # the injected client recorded the calls (network went through the seam)
    assert client.calls, "injected client recorded no calls"
    json.dumps(report)  # JSON-serializable, no PII objects embedded
