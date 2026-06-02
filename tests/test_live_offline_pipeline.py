"""Offline proof that the LIVE pipeline works, driven by committed fixtures.

These fixtures are REAL responses captured once from the public LACCD APIs;
their provenance is recorded in the m3 fixture commit message. We replay them
through a FakeClient so the full chain runs with NO network:

    schedule.fetch_sections + program_mapper.fetch_program
        -> mapping.reconcile_courses
        -> mapping.write_workbook
        -> engine.run

If the live APIs drift, we re-capture the fixtures and these assertions tell
us whether the downstream contract still holds.
"""
import json
import pathlib

import build_live_workbook
import engine
from conftest import STEM_GID, load_fixture
from sources import mapping, program_mapper as pm, schedule

FIX = pathlib.Path(__file__).parent / "fixtures"

# The `lamc_routes` fixture (the shared live-fixture route map) and the
# STEM_GID identifier now live in tests/conftest.py so the live-pipeline and
# desktop-shell tests share one source of truth.


def test_fixtures_exist_and_are_shape_faithful(lamc_routes):
    listing = load_fixture("schedule_listing_LAMC_2268.json")
    assert listing["campuscode"] == "LAMC"
    assert isinstance(listing["subjects"], list) and listing["subjects"]
    home = load_fixture("pm_home_page_content_LAMC.json")
    assert any(g["masterRecordId"] == STEM_GID for g in home["programGroups"])
    pmap = load_fixture("pm_program_map_LAMC.json")
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
    # modality_mismatch stays inert (no fill % without IR); under_supply fires
    # from the live schedule Waitlist status (breadth, headcount 0).
    assert results["analysis"]["modality_mismatch"] == []
    us = results["analysis"]["under_supply"]
    assert us, "live waitlist status should fire under_supply"
    assert all(r["waitlisted"] == 0 and r.get("sections_waitlisted", 0) >= 1 for r in us)


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
    # under_supply is live-active now (fires from the schedule Waitlist status).
    # ge_scheduling is always present (inert when no transfer_goal given).
    assert {d["detector"] for d in inert} == {
        "modality_mismatch", "prerequisite_ordering", "ge_scheduling"}
    for d in inert:
        if d["detector"] == "ge_scheduling":
            continue  # ge_scheduling carries "reason" but no "remedy"
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


def test_analyze_live_with_ge(lamc_routes, make_client, tmp_path):
    routes = dict(lamc_routes)
    routes["/api/AcademicYears"] = json.loads((FIX / "assist_academic_years.json").read_text())
    routes["/api/transferability/courses"] = json.loads(
        (FIX / "assist_transferability_igetc_LAMC.json").read_text())
    client = make_client(routes)
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(tmp_path / "ge_live_test.xlsx"),
        client=client, transfer_goal="igetc", assist_year_id=77,
        ge_pattern_path=str(FIX / "ge_pattern_test.json"))
    assert report["ge_coverage"]["requested"] is True
    assert report["ge_coverage"]["pattern"] == "igetc"
    assert any(d["detector"] == "ge_scheduling" for d in report["inert_detectors"])
    assert report["results"] is not None


def test_analyze_live_ge_disabled_has_no_coverage(lamc_routes, make_client, tmp_path):
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(tmp_path / "no_ge_live.xlsx"),
        client=make_client(lamc_routes), transfer_goal="none")
    assert report.get("ge_coverage") is None
