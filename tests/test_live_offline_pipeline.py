"""Offline proof that the LIVE pipeline works, driven by committed fixtures.

These fixtures are REAL responses captured once from the public LACCD APIs
(see tests/fixtures/README is not used; provenance is in the m3 commit). We
replay them through a FakeClient so the full chain runs with NO network:

    schedule.fetch_sections + program_mapper.fetch_program
        -> mapping.reconcile_courses
        -> mapping.write_workbook
        -> engine.run

If the live APIs drift, we re-capture the fixtures and these assertions tell
us whether the downstream contract still holds.
"""
import json
import pathlib

import pytest

import build_live_workbook
import engine
from sources import mapping, program_mapper as pm, schedule

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# Real LAMC identifiers the fixtures were captured under.
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


def test_fixtures_exist_and_are_shape_faithful(lamc_routes):
    listing = _load("schedule_listing_LAMC_2268.json")
    assert listing["campuscode"] == "LAMC"
    assert isinstance(listing["subjects"], list) and listing["subjects"]
    home = _load("pm_home_page_content_LAMC.json")
    assert any(g["masterRecordId"] == STEM_GID for g in home["programGroups"])
    pmap = _load("pm_program_map_LAMC.json")
    assert any((e.get("recommendedOpportunity") or {}).get("type") == "COURSE"
               for e in pmap["pathwayElements"])


def test_full_chain_offline_through_engine(lamc_routes, make_client, tmp_path):
    client = make_client(lamc_routes)

    sections = schedule.fetch_sections("LAMC", [2268], client=client)
    program = pm.fetch_program("LAMC", "Biology", client=client)

    # schedule + program both resolved from real fixtures
    assert len(sections) > 0
    assert program is not None
    assert program["title"] == "Biology"
    assert program["code"] == "BIOLOGY"
    assert len(program["courses"]) > 0

    matched, unmatched = mapping.reconcile_courses(sections, program)
    # Biology's mapped courses overlap the captured Fall listing.
    assert matched, "expected at least one program course offered in the listing"

    out = tmp_path / "live_offline.xlsx"
    mapping.write_workbook(sections, program, str(out))
    results = engine.run(str(out))

    # valid results dict: data summary, analysis shape, program present
    assert results["terms_in_data"] >= 1
    assert set(results["analysis"]) == {
        "rotation_gaps", "single_section", "modality_mismatch", "under_supply"}
    assert "BIOLOGY" in results["programs"]
    # enrollment-driven detectors stay inert on live-shaped data (no counts)
    assert results["analysis"]["modality_mismatch"] == []
    assert results["analysis"]["under_supply"] == []


def test_build_live_workbook_emits_structured_report(lamc_routes, make_client,
                                                      tmp_path, monkeypatch):
    """build_live_workbook.analyze_live returns a structured, JSON-serializable
    report (reconciliation + inert detectors + engine results) so a UI can
    render it without re-parsing a human banner."""
    client = make_client(lamc_routes)
    out = tmp_path / "live.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(out), client=client)

    # JSON-serializable end to end
    json.dumps(report)

    assert report["campus"] == "LAMC"
    assert report["terms"] == [2268]
    assert report["program"]["code"] == "BIOLOGY"
    assert report["program"]["title"] == "Biology"
    assert report["section_count"] > 0

    rec = report["reconciliation"]
    assert isinstance(rec["matched"], list) and rec["matched"]
    assert isinstance(rec["unmatched"], list)
    assert rec["matched_count"] == len(rec["matched"])
    assert rec["unmatched_count"] == len(rec["unmatched"])

    # inert-detector gaps surfaced as structured machine-readable fields
    inert = report["inert_detectors"]
    assert {d["detector"] for d in inert} >= {
        "modality_mismatch", "under_supply"}
    for d in inert:
        assert d["reason"]            # human-readable why
        assert "remedy" in d          # what would un-inert it

    # engine results embedded
    assert report["results"]["terms_in_data"] >= 1
    assert "BIOLOGY" in report["results"]["programs"]


def test_build_live_workbook_report_program_not_found(lamc_routes, make_client,
                                                       tmp_path):
    client = make_client(lamc_routes)
    out = tmp_path / "live.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Underwater Basket Weaving", str(out), client=client)
    assert report["program"] is None
    assert report["error"]
    assert "no program" in report["error"].lower()
