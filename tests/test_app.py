"""Headless tests for the pywebview desktop shell (app.py).

The pywebview window is only created inside main() under
`if __name__ == "__main__"`, so importing `app` and exercising `Api`
directly never opens a window — safe for CI and offline runs.

Covers the m2 "one-click demo" path: Api._demo_path() resolves the bundled
synthetic workbook, Api.load_demo() runs the same code path as a normal
analyze, and a non-workbook path surfaces a readable error dict instead of
raising.

Also covers the m4 "live LACCD data inside the UI" path: Api.fetch_live()
runs the full live pipeline through an injected FakeClient (replaying the
committed fixtures, no network) and returns the engine results merged with the
reconciliation + inert-detector fields the UI renders; a no-match program
surfaces a readable error dict instead of raising.
"""
import json
import os
import pathlib

import pytest

import app

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# Real LAMC identifiers the committed fixtures were captured under (mirrors
# tests/test_live_offline_pipeline.py so the FakeClient routes line up).
ARTS_GID = "e2068320-d2f3-421d-bbf8-a0014e859702"
STEM_GID = "fd4c554f-6a1c-4180-9c09-900520f4d4a8"
BIOLOGY_PID = "a4060608-61af-8a69-5d00-66fc77c61774"
BIOLOGY_MAPID = "c9380a8d-158f-44a8-b8b3-ddebba81a8a8"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def lamc_routes():
    """URL-substring -> fixture payload map for the full LAMC Biology chain."""
    return {
        "/listing/LAMC/2268": _load("schedule_listing_LAMC_2268.json"),
        "/subjects/LAMC/2268": _load("schedule_subjects_LAMC_2268.json"),
        "/home-page-content": _load("pm_home_page_content_LAMC.json"),
        f"/program-groups/{ARTS_GID}": _load("pm_program_group_arts_LAMC.json"),
        f"/program-groups/{STEM_GID}": _load("pm_program_group_LAMC.json"),
        f"/programs/{BIOLOGY_PID}": _load("pm_program_LAMC.json"),
        f"/program-maps/{BIOLOGY_MAPID}": _load("pm_program_map_LAMC.json"),
    }


def test_demo_path_points_at_bundled_workbook():
    p = app.Api()._demo_path()
    assert p.replace(os.sep, "/").endswith("files/lamc_data.xlsx")
    assert os.path.exists(p), f"bundled demo workbook missing at {p}"


def test_load_demo_returns_full_analysis():
    res = app.Api().load_demo()
    assert "error" not in res, res.get("error")
    assert res["terms_in_data"] == 8
    # all four supply-diagnostic detectors present
    assert set(res["analysis"]) == {
        "rotation_gaps", "single_section", "modality_mismatch", "under_supply",
    }
    # the four bundled AS-T programs
    assert set(res["programs"]) == {
        "AS-T-CSCI", "AS-T-BUS", "AS-T-BIOL", "AS-T-ENGR",
    }


def test_load_demo_uses_same_path_as_analyze():
    api = app.Api()
    via_analyze = api.analyze(api._demo_path())
    via_demo = api.load_demo()
    assert via_demo["terms_in_data"] == via_analyze["terms_in_data"]
    assert set(via_demo["programs"]) == set(via_analyze["programs"])


def test_analyze_non_workbook_returns_error_not_exception(tmp_path):
    bad = tmp_path / "not_a_workbook.txt"
    bad.write_text("this is plainly not an xlsx workbook")
    res = app.Api().analyze(str(bad))
    assert isinstance(res, dict)
    assert "error" in res
    assert isinstance(res["error"], str) and res["error"]


def test_analyze_missing_file_returns_error():
    res = app.Api().analyze("/no/such/file/anywhere.xlsx")
    assert res == {"error": "File not found."}


# ---- m4: live LACCD data inside the UI ------------------------------------

def test_fetch_live_returns_results_plus_reconciliation_and_inert(
        lamc_routes, make_client):
    """Api.fetch_live drives the full live pipeline through an injected
    FakeClient (no network) and returns the engine results merged with the
    reconciliation + inert-detector fields the UI renders."""
    client = make_client(lamc_routes)
    res = app.Api().fetch_live("LAMC", "2268", "Biology", client=client)

    assert "error" not in res, res.get("error")
    # whole payload is JSON-serializable (it is marshalled to JS)
    json.dumps(res)

    # engine results are present at the top level (so showResult() renders)
    assert res["terms_in_data"] >= 1
    assert set(res["analysis"]) == {
        "rotation_gaps", "single_section", "modality_mismatch", "under_supply"}
    assert "BIOLOGY" in res["programs"]
    # enrollment-driven detectors stay inert on live-shaped data (no counts)
    assert res["analysis"]["modality_mismatch"] == []
    assert res["analysis"]["under_supply"] == []

    # reconciliation surfaced for the live panel
    rec = res["reconciliation"]
    assert isinstance(rec["matched"], list) and rec["matched"]
    assert isinstance(rec["unmatched"], list)
    assert rec["matched_count"] == len(rec["matched"])
    assert rec["unmatched_count"] == len(rec["unmatched"])

    # inert-detector notes surfaced for the live panel
    inert = res["inert_detectors"]
    assert {d["detector"] for d in inert} >= {
        "modality_mismatch", "under_supply", "prerequisite_ordering"}
    for d in inert:
        assert d["reason"]
        assert d["remedy"]


def test_fetch_live_parses_comma_terms(lamc_routes, make_client):
    """A comma-separated terms string is parsed into ints; with a single
    fixture term it still resolves the live chain."""
    client = make_client(lamc_routes)
    res = app.Api().fetch_live("LAMC", " 2268 ", "Biology", client=client)
    assert "error" not in res, res.get("error")
    assert res["terms_in_data"] >= 1


def test_fetch_live_no_match_returns_error(lamc_routes, make_client):
    client = make_client(lamc_routes)
    res = app.Api().fetch_live(
        "LAMC", "2268", "Underwater Basket Weaving", client=client)
    assert isinstance(res, dict)
    assert "error" in res
    assert isinstance(res["error"], str) and res["error"]
    assert "no program matched" in res["error"].lower()


def test_fetch_live_source_error_returns_error_not_exception(make_client):
    """A SourceError (here: a route the FakeClient can't satisfy) surfaces a
    readable error dict rather than raising into the UI."""
    client = make_client({})  # no routes -> the chain blows up
    res = app.Api().fetch_live("LAMC", "2268", "Biology", client=client)
    assert isinstance(res, dict)
    assert "error" in res
    assert isinstance(res["error"], str) and res["error"]


def test_fetch_live_blank_terms_returns_error():
    res = app.Api().fetch_live("LAMC", "   ", "Biology")
    assert isinstance(res, dict)
    assert "error" in res
    assert isinstance(res["error"], str) and res["error"]
