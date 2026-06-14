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

import pytest

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
        "modality_mismatch", "prerequisite_ordering", "ge_scheduling",
        "time_block_conflict", "room_conflict", "program_buildability",
        "program_bottleneck", "grid_pressure", "demand_supply",
        "equity_exposure", "gateway_momentum", "corequisite_availability",
        "infeasibility", "demand_success", "equity_success_gap",
        "minimal_perturbation", "contact_hours"}
    for d in inert:
        if d["detector"] == "ge_scheduling" or d.get("status") == "active":
            continue  # ge_scheduling / active detectors carry "reason" but no "remedy"
        assert d["reason"]            # human-readable why
        assert "remedy" in d          # what would un-inert it

    # engine results embedded
    assert report["results"]["terms_in_data"] >= 1
    assert "BIOLOGY" in report["results"]["programs"]


def test_analyze_live_emits_buildability(lamc_routes, make_client, tmp_path):
    """The program-map buildability audit (F1) is injected into
    results['analysis']['buildability'] and surfaced as a detector entry."""
    client = make_client(lamc_routes)
    out = tmp_path / "live.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(out), client=client)

    block = report["results"]["analysis"]["buildability"]
    assert block["status"] in ("active", "inert")
    assert "PROXY" in block["label"]          # honesty caveat travels with it
    if block["status"] == "active":
        assert block["programs"]
        prog0 = block["programs"][0]
        assert prog0["required_total"] >= 1
        assert "available" in prog0 and "score" in prog0 and "time_conflict" in prog0
        assert "score_major_only" in prog0 and "score_delta" in prog0 and "ge" in prog0

    det = next(d for d in report["inert_detectors"]
               if d["detector"] == "program_buildability")
    assert det["status"] == block["status"]
    json.dumps(report)  # still JSON-serializable end to end


def test_analyze_live_buildability_folds_ge(lamc_routes, make_client, tmp_path):
    """With a transfer GE goal, the buildability audit carries the GE-inclusive
    score, the major-only score, the signed delta, and the GE block — and the
    active envelope carries the GE label."""
    routes = dict(lamc_routes)
    routes["/api/AcademicYears"] = json.loads((FIX / "assist_academic_years.json").read_text())
    routes["/api/transferability/courses"] = json.loads(
        (FIX / "assist_transferability_igetc_LAMC.json").read_text())
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(tmp_path / "ge_build.xlsx"),
        client=make_client(routes), transfer_goal="igetc", assist_year_id=77,
        ge_pattern_path=str(FIX / "ge_pattern_test.json"))
    block = report["results"]["analysis"]["buildability"]
    assert block["status"] == "active"
    assert "GE-inclusive" in block["ge_label"]
    prog0 = block["programs"][0]
    assert {"score_major_only", "score_delta", "ge"} <= set(prog0)
    assert prog0["score"] == prog0["score_major_only"] + prog0["score_delta"]
    # GE actually folded (guards against regressing to the dead ge_rows= shim,
    # which would leave ge=None / delta=0):
    assert prog0["ge"] is not None and prog0["ge"]["status"] == "active"
    assert prog0["score_delta"] != 0
    # the detector reason advertises the GE fold
    det = next(d for d in report["inert_detectors"] if d["detector"] == "program_buildability")
    assert "GE requirements fold into the denominator" in det["reason"]
    json.dumps(report)  # still JSON-serializable end to end


def test_analyze_live_emits_evidence_appendix(lamc_routes, make_client, tmp_path):
    """The F7 evidence appendix is attached to results['analysis']['evidence'] by
    the post-detector pass — a PURE consumer, with NO detector entry (Q3) and the
    sector-wide / not-this-campus caveat in its label."""
    client = make_client(lamc_routes)
    out = tmp_path / "live.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(out), client=client)

    block = report["results"]["analysis"]["evidence"]
    assert block["status"] in ("active", "inert")
    # honesty caveat travels with it (sector-wide, NOT this campus)
    assert "NOT a measurement of this campus" in block["label"]
    assert block["claims"], "even the no-flags default carries positive context"
    # every emitted claim is graded 'vetted' (the curated ✅ set only)
    assert all(c["grade"] == "vetted" for c in block["claims"])
    # F7 adds NO detector entry — it is static curated evidence, not a signal.
    assert not any(d.get("detector") == "evidence"
                   for d in report["inert_detectors"])
    json.dumps(report)  # still JSON-serializable end to end


def test_analyze_live_evidence_stays_outside_engine_run(lamc_routes, make_client,
                                                        tmp_path):
    """F7 writes only the JSON 'evidence' key — the engine.run analysis (the
    workbook-derived dict) is untouched, so the determinism gate stays green."""
    client = make_client(lamc_routes)
    out = tmp_path / "live.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(out), client=client)
    # The workbook the engine wrote knows nothing about 'evidence': re-running the
    # engine over it yields the four engine-native analysis keys only.
    engine_analysis = engine.run(str(out))["analysis"]
    assert "evidence" not in engine_analysis
    assert set(engine_analysis) == {
        "rotation_gaps", "single_section", "modality_mismatch", "under_supply"}
    # but the live report DOES carry the F7 appendix (added outside engine.run).
    assert "evidence" in report["results"]["analysis"]


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
    # ge_pattern_test.json carries reviewed_by="test" -> reviewed, no draft notice.
    assert report["ge_coverage"]["reviewed"] is True
    assert "draft_warning" not in report["ge_coverage"]
    ge_det = next(d for d in report["inert_detectors"] if d["detector"] == "ge_scheduling")
    assert ge_det["reviewed"] is True and ge_det["draft_warning"] == ""


def test_analyze_live_unreviewed_pattern_emits_draft_warning(
        lamc_routes, make_client, tmp_path):
    # A shipped pattern (blank reviewed_by) must mark the coverage as a DRAFT so
    # the UI/CLI never present its placeholder counts as authoritative.
    routes = dict(lamc_routes)
    routes["/api/AcademicYears"] = json.loads((FIX / "assist_academic_years.json").read_text())
    routes["/api/transferability/courses"] = json.loads(
        (FIX / "assist_transferability_igetc_LAMC.json").read_text())
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(tmp_path / "ge_draft.xlsx"),
        client=make_client(routes), transfer_goal="igetc", assist_year_id=77)
    cov = report["ge_coverage"]
    assert cov["reviewed"] is False
    assert cov["draft_warning"].startswith("Draft — unverified:")
    ge_det = next(d for d in report["inert_detectors"] if d["detector"] == "ge_scheduling")
    assert ge_det["reviewed"] is False
    assert ge_det["draft_warning"] == cov["draft_warning"]


def test_fetch_program_by_id_matches_title_search(lamc_routes, make_client):
    # Resolving by exact masterRecordId must yield the same program as the title
    # search (the id path lets duplicate-titled programs be addressed uniquely).
    rec = pm.search_program("LAMC", "Biology", client=make_client(lamc_routes))
    by_id = pm.fetch_program_by_id(
        "LAMC", rec["masterRecordId"], title=rec["title"],
        award=rec.get("awardShortTitle", ""), client=make_client(lamc_routes))
    by_query = pm.fetch_program("LAMC", "Biology", client=make_client(lamc_routes))
    assert by_id["code"] == by_query["code"] == "BIOLOGY"
    assert by_id["title"] == by_query["title"]
    assert by_id["courses"] == by_query["courses"]


def test_analyze_live_by_program_id_with_injected_assist(lamc_routes, make_client, tmp_path):
    # The sweep path: resolve by id + inject a pre-fetched ASSIST map (so NO live
    # ASSIST call is made) + share an eLumen cache dict. assist_status is "ok"
    # from the injected map and the program resolves by id, not title.
    rec = pm.search_program("LAMC", "Biology", client=make_client(lamc_routes))
    injected_assist = {"5B": {"title": "Bio", "courses": ["BIOLOGY 7"]}}
    shared_cache = {}
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "", str(tmp_path / "by_id.xlsx"),
        client=make_client(lamc_routes),
        program_id=rec["masterRecordId"], program_title=rec["title"],
        program_award=rec.get("awardShortTitle", ""),
        transfer_goal="igetc", assist_year_id=77, assist_areas=injected_assist,
        elumen_cache=shared_cache, ge_pattern_path=str(FIX / "ge_pattern_test.json"))
    assert report["program"]["code"] == "BIOLOGY"
    assert report["ge_coverage"]["assist_status"] == "ok"
    assert report["ge_coverage"]["academic_year"] == {"id": 77}
    assert report["results"] is not None


def test_fetch_program_by_id_empty_title_falls_back_to_id_code(lamc_routes, make_client):
    # With no title supplied, the code falls back to the id prefix (uppercased).
    rec = pm.search_program("LAMC", "Biology", client=make_client(lamc_routes))
    prog = pm.fetch_program_by_id(
        "LAMC", rec["masterRecordId"], title="", award="", client=make_client(lamc_routes))
    assert prog["title"] == "" and prog["award"] == ""
    assert prog["code"] == rec["masterRecordId"][:8].upper()
    assert prog["courses"]  # still resolves the real course list by id


def test_fetch_program_by_id_unknown_id_raises(make_client, error_resp):
    # Unknown id is a programming error (ids come from the listing) -> it raises
    # loudly rather than silently returning None like a bad title query does.
    from sources.http import SourceError
    routes = {"/programs/bad-id": error_resp(404)}
    with pytest.raises(SourceError):
        pm.fetch_program_by_id("LAMC", "bad-id", title="X", client=make_client(routes))


def test_analyze_live_injected_empty_assist_is_unavailable(lamc_routes, make_client, tmp_path):
    # An empty/malformed injected ASSIST map must be labelled "unavailable", never
    # silently "ok" (no overclaiming coverage that isn't there).
    rec = pm.search_program("LAMC", "Biology", client=make_client(lamc_routes))
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "", str(tmp_path / "empty_assist.xlsx"),
        client=make_client(lamc_routes), program_id=rec["masterRecordId"],
        program_title=rec["title"], transfer_goal="igetc", assist_areas={},
        ge_pattern_path=str(FIX / "ge_pattern_test.json"))
    assert report["ge_coverage"]["assist_status"] == "unavailable"
    assert report["ge_coverage"]["error"]


def test_analyze_live_ge_disabled_has_no_coverage(lamc_routes, make_client, tmp_path):
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(tmp_path / "no_ge_live.xlsx"),
        client=make_client(lamc_routes), transfer_goal="none")
    assert report.get("ge_coverage") is None


def test_analyze_live_local_ge_from_catalog_json(lamc_routes, make_client, tmp_path):
    # Spec 2: transfer_goal="local" + an injected OpenDataLoader JSON (no Java)
    # sources GE from the catalog and reuses the same resolve/solver/panel path.
    odl = json.loads((FIX / "catalog_odl_sample.json").read_text())
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(tmp_path / "local_ge.xlsx"),
        client=make_client(lamc_routes), transfer_goal="local", odl_json=odl)
    cov = report["ge_coverage"]
    assert cov["requested"] is True and cov["pattern"] == "local"
    assert cov["source"] == "catalog"
    assert cov["reviewed"] is False and cov["draft_warning"]
    assert cov["areas"]                       # parsed areas were resolved
    assert cov["catalog_diagnostics"]["section_found"] is True
    ge_det = next(d for d in report["inert_detectors"] if d["detector"] == "ge_scheduling")
    assert ge_det["status"] == "active"
    assert report["results"] is not None


def test_analyze_live_local_ge_no_section_degrades(lamc_routes, make_client, tmp_path):
    # A catalog with no GE section degrades to honest, empty coverage (no schedule
    # silently shaped), never an exception.
    odl = {"kids": [{"type": "heading", "heading level": 1,
                     "content": "Course Descriptions"}]}
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(tmp_path / "local_none.xlsx"),
        client=make_client(lamc_routes), transfer_goal="local", odl_json=odl)
    cov = report["ge_coverage"]
    assert cov["pattern"] == "local" and cov["areas"] == []
    assert cov.get("error")
    assert report["results"] is not None


# --- time-block collision detector (Workstream C) -----------------------------
def test_time_block_collisions_detects_hard_pair():
    """Two required courses whose only sections overlap are flagged as a hard pair,
    and the redundant term-level finding for that pair is suppressed."""
    sections = [
        {"course": "CHEM 101", "days": "MW", "times": "9:00 AM - 10:00 AM"},
        {"course": "MATH 245", "days": "MW", "times": "9:30 AM - 10:30 AM"},
        {"course": "ENGL 101", "days": "T Th", "times": "9:00 AM - 10:00 AM"},
    ]
    program = {"code": "X", "title": "X", "courses": [
        {"course_id": "CHEM 101"}, {"course_id": "MATH 245"}, {"course_id": "ENGL 101"}]}
    results = {"programs": {"X": {"cohorts": {
        "full_time": {"plan": {1: ["CHEM 101", "MATH 245", "ENGL 101"]}}}}}}
    findings = build_live_workbook._time_block_collisions(sections, program, results)
    assert any(f["kind"] == "pair" and set(f["courses"]) == {"CHEM 101", "MATH 245"}
               for f in findings)
    assert all(f["kind"] != "term" for f in findings)   # pair already covers it


def test_time_block_collisions_none_when_no_overlap():
    sections = [
        {"course": "CHEM 101", "days": "MW", "times": "9:00 AM - 10:00 AM"},
        {"course": "MATH 245", "days": "MW", "times": "10:00 AM - 11:00 AM"},
    ]
    program = {"code": "X", "title": "X",
               "courses": [{"course_id": "CHEM 101"}, {"course_id": "MATH 245"}]}
    results = {"programs": {"X": {"cohorts": {
        "full_time": {"plan": {1: ["CHEM 101", "MATH 245"]}}}}}}
    assert build_live_workbook._time_block_collisions(sections, program, results) == []


def test_time_block_collisions_joint_three_way():
    """Three courses with only two non-overlapping slots can't all fit a term, with
    no single hard pair -> a term-level (joint) finding, not a pair finding."""
    sections = []
    for c in ("A 1", "B 1", "C 1"):
        sections.append({"course": c, "days": "M", "times": "9:00 AM - 10:00 AM"})
        sections.append({"course": c, "days": "M", "times": "10:00 AM - 11:00 AM"})
    program = {"code": "X", "title": "X", "courses": [
        {"course_id": "A 1"}, {"course_id": "B 1"}, {"course_id": "C 1"}]}
    results = {"programs": {"X": {"cohorts": {
        "full_time": {"plan": {1: ["A 1", "B 1", "C 1"]}}}}}}
    findings = build_live_workbook._time_block_collisions(sections, program, results)
    assert any(f["kind"] == "term" for f in findings)
    assert all(f["kind"] != "pair" for f in findings)


def test_time_block_detector_entry_active_vs_inert():
    live = [{"course": "CHEM 101", "days": "MW", "times": "9:00 AM - 10:00 AM"}]
    async_only = [{"course": "X 1", "days": "", "times": ""}]
    assert build_live_workbook._time_block_detector_entry(live, [])["status"] == "active"
    assert build_live_workbook._time_block_detector_entry(async_only, [])["status"] == "inert"


def test_off_grid_sections_flags_nonstandard_start():
    secs = [
        {"course": "CHEM 101", "term": 2248, "days": "MW", "times": "8:55 AM - 10:20 AM"},  # on grid
        {"course": "MATH 245", "term": 2248, "days": "MW", "times": "9:05 AM - 10:30 AM"},  # off grid
        {"course": "ENGL 101", "term": 2248, "days": "", "times": ""},                       # async, skip
    ]
    findings = build_live_workbook._off_grid_sections(secs)
    courses = {f["course"] for f in findings}
    assert "MATH 245" in courses
    assert "CHEM 101" not in courses and "ENGL 101" not in courses


# --- F5: demand-vs-supply action list ----------------------------------------
def test_analyze_live_demand_supply_inert_without_counts(lamc_routes, make_client, tmp_path):
    """A bare live fetch has no Cap/Tot/Wait -> F5 inert with an honest reason."""
    client = make_client(lamc_routes)
    out = tmp_path / "live.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(out), client=client)
    block = report["results"]["analysis"]["demand_supply"]
    assert block["status"] == "inert"
    assert "PROXY" in block["label"]
    det = next(d for d in report["inert_detectors"] if d["detector"] == "demand_supply")
    assert det["status"] == "inert"
    json.dumps(report)


def test_analyze_live_demand_supply_active_with_counts(tmp_path):
    """Sections carrying Cap/Tot/Wait (import-style) -> F5 active: an
    over-subscribed course lands on the add list, an under-filled multi-section
    course on the capacity-slack list."""
    records = [
        {"course": "MATH 227", "term": 2268, "class_nbr": "30001 (LEC)", "days": "MW",
         "times": "9:00 AM - 10:15 AM", "Cap Enrl": 40, "Tot Enrl": 40, "Wait Tot": 22,
         "status": "Closed", "units": 5},
        {"course": "MATH 227", "term": 2268, "class_nbr": "30002 (LEC)", "days": "TR",
         "times": "9:00 AM - 10:15 AM", "Cap Enrl": 40, "Tot Enrl": 39, "Wait Tot": 12,
         "status": "Waitlist", "units": 5},
        {"course": "ART 101", "term": 2268, "class_nbr": "30003 (LEC)", "days": "MW",
         "times": "1:00 PM - 2:15 PM", "Cap Enrl": 40, "Tot Enrl": 6, "Wait Tot": 0,
         "status": "Open", "units": 3},
        {"course": "ART 101", "term": 2268, "class_nbr": "30004 (LEC)", "days": "TR",
         "times": "1:00 PM - 2:15 PM", "Cap Enrl": 40, "Tot Enrl": 5, "Wait Tot": 0,
         "status": "Open", "units": 3},
    ]
    program = {"code": "TEST", "title": "Test", "award": "AS",
               "courses": [{"course_id": "MATH 227", "recommended_semester": 1}],
               "major_choices": []}
    out = tmp_path / "imp.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "(test)", str(out),
        sections_override=records, program_override=program)
    block = report["results"]["analysis"]["demand_supply"]
    assert block["status"] == "active"
    add_courses = [r["course"] for r in block["add_list"]]
    assert any("MATH 227" in c for c in add_courses)
    slack_courses = [s["course"] for s in block["capacity_slack"]]
    assert any("ART 101" in c for c in slack_courses)
    det = next(d for d in report["inert_detectors"] if d["detector"] == "demand_supply")
    assert det["status"] == "active"
    json.dumps(report)


# --- FF4: bounded live cross-program demand fan-out activates F2 --------------
def test_analyze_live_bottleneck_inert_without_fanout(lamc_routes, make_client, tmp_path):
    """A bare live fetch (no fan-out requested) keeps F2 honestly INERT — the
    live path resolves one program per run, so there is no cross-program demand."""
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(tmp_path / "no_fanout.xlsx"),
        client=make_client(lamc_routes))
    block = report["results"]["analysis"]["bottlenecks"]
    assert block["status"] == "inert"
    det = next(d for d in report["inert_detectors"] if d["detector"] == "program_bottleneck")
    assert det["status"] == "inert"
    json.dumps(report)


def test_analyze_live_bottleneck_active_with_fanout(lamc_routes, make_client, tmp_path):
    """Opting into the fan-out (a bounded list of program ids) populates the
    cross-program demand map on the LIVE path, flipping F2 to ACTIVE — no
    offline Program Course Lists upload needed."""
    home = load_fixture("pm_home_page_content_LAMC.json")
    # Resolve the two real LAMC programs that have committed program-map fixtures
    # (both Biology variants share the STEM group; only the AS-T has a map route),
    # so we fan out over the AS-T Biology id plus itself-by-title resolution is
    # avoided by passing explicit ids. Use the Biology AS-T id from conftest.
    from conftest import BIOLOGY_PID
    fanout_ids = [{"program_id": BIOLOGY_PID, "title": "Biology",
                   "award": "Associate in Science for Transfer"}]
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(tmp_path / "fanout.xlsx"),
        client=make_client(lamc_routes), demand_program_ids=fanout_ids)
    block = report["results"]["analysis"]["bottlenecks"]
    assert block["status"] == "active", block.get("reason")
    assert block["leaderboard"]            # at least one required-AND-offered course
    det = next(d for d in report["inert_detectors"] if d["detector"] == "program_bottleneck")
    assert det["status"] == "active"
    json.dumps(report)


def test_analyze_live_fanout_failopen_stays_inert(lamc_routes, make_client, tmp_path):
    """If the fan-out yields nothing (every requested id errors), F2 fails OPEN
    to its honest inert envelope — never a fabricated demand map."""
    routes = dict(lamc_routes)
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "Biology", str(tmp_path / "fanout_fail.xlsx"),
        client=make_client(routes),
        demand_program_ids=[{"program_id": "no-such-program-id", "title": "X"}])
    block = report["results"]["analysis"]["bottlenecks"]
    assert block["status"] == "inert"
    det = next(d for d in report["inert_detectors"] if d["detector"] == "program_bottleneck")
    assert det["status"] == "inert"
    json.dumps(report)


# --- F6: equity / archetype exposure -----------------------------------------
def test_analyze_live_equity_exposure_active_online_computable(tmp_path):
    """Records carrying modality (live shape) -> F6 active, all three archetypes;
    online is computable and an in-person-only required course collapses under it."""
    records = [
        {"course": "MATH 227", "term": 2268, "class_nbr": "1", "days": "MW",
         "times": "9:00 AM - 10:15 AM", "modality": ["IN-PERSON"], "units": 5},
        {"course": "ENGLISH 101", "term": 2268, "class_nbr": "2", "days": "",
         "times": "", "modality": ["ONLINE"], "room": "Mission-Online", "units": 3},
    ]
    program = {"code": "TEST", "title": "Test", "award": "AS", "courses": [
        {"course_id": "MATH 227", "recommended_semester": 1},
        {"course_id": "ENGLISH 101", "recommended_semester": 1}], "major_choices": []}
    out = tmp_path / "imp.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "(test)", str(out),
        sections_override=records, program_override=program)
    block = report["results"]["analysis"]["equity_exposure"]
    assert block["status"] == "active"
    assert [a["key"] for a in block["archetypes"]] == ["evening", "online", "two_day"]
    online = next(a for a in block["archetypes"] if a["key"] == "online")
    assert online["computable"] is True
    p = next(p for p in online["programs"] if p["code"] == "TEST")
    assert "MATH 227" in p["newly_unavailable"]      # in-person drops under online
    assert "ENGLISH 101" not in p["newly_unavailable"]  # online survives
    det = next(d for d in report["inert_detectors"] if d["detector"] == "equity_exposure")
    assert det["status"] == "active"
    json.dumps(report)


# --- F8: first-year gateway momentum -----------------------------------------
def test_analyze_live_gateway_momentum_active_identifies_english_and_math(tmp_path):
    """End-to-end through the registry: a program with required ENGL/MATH courses
    offered in the first-year window -> F8 active, both gateways identified via the
    major-subject fallback and schedulable, with an active inert-detector entry."""
    records = [
        {"course": "ENGLISH 101", "term": 2268, "class_nbr": str(i),
         "days": "MW", "times": "9:00 AM - 10:15 AM", "units": 3} for i in (1, 2)
    ] + [
        {"course": "MATH 227", "term": 2268, "class_nbr": str(i),
         "days": "TTh", "times": "11:00 AM - 12:15 PM", "units": 5} for i in (3, 4)
    ]
    program = {"code": "TEST", "title": "Test", "award": "AS", "courses": [
        {"course_id": "ENGLISH 101", "recommended_semester": 1},
        {"course_id": "MATH 227", "recommended_semester": 1}], "major_choices": []}
    out = tmp_path / "gw.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "(test)", str(out),
        sections_override=records, program_override=program)
    block = report["results"]["analysis"]["gateway_momentum"]
    assert block["status"] == "active"
    assert block["english"]["course"] == "ENGLISH 101"
    assert block["english"]["via"] == "major_subject"
    assert block["english"]["schedulable_year1"] is True
    assert block["math"]["course"] == "MATH 227"
    assert block["both_gateways_year1"] is True
    assert "PROXY" in block["label"]
    det = next(d for d in report["inert_detectors"] if d["detector"] == "gateway_momentum")
    assert det["status"] == "active" and det["found"] == 2
    json.dumps(report)


# --- F9: AB1705 corequisite co-availability ----------------------------------
def test_analyze_live_corequisite_availability_active_with_injected_coreq(tmp_path):
    """End-to-end through the registry: a transfer-level gateway whose injected
    corequisite is co-offered in the same first-year term -> F9 active, co-offering
    detected, with an active inert-detector entry. The coreq map is injected (the
    live path derives the same shape from eLumen --elumen-live)."""
    records = [
        {"course": "MATH 227", "term": 2268, "class_nbr": str(i),
         "days": "MW", "times": "9:00 AM - 10:15 AM", "units": 5} for i in (1, 2)
    ] + [
        {"course": "MATH 227L", "term": 2268, "class_nbr": str(i),
         "days": "TTh", "times": "11:00 AM - 12:15 PM", "units": 1} for i in (3, 4)
    ]
    program = {"code": "TEST", "title": "Test", "award": "AS", "courses": [
        {"course_id": "MATH 227", "recommended_semester": 1}], "major_choices": []}
    out = tmp_path / "coreq.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "(test)", str(out),
        sections_override=records, program_override=program,
        elumen_coreq={"MATH 227": ["MATH 227L"]})
    block = report["results"]["analysis"]["corequisite_availability"]
    assert block["status"] == "active"
    assert block["math"]["course"] == "MATH 227"
    assert block["math"]["has_corequisite"] is True
    assert block["math"]["co_offered_year1"] is True
    assert block["math"]["co_offered_terms"] == ["2268"]
    assert "STRUCTURE proxy" in block["label"]
    det = next(d for d in report["inert_detectors"]
               if d["detector"] == "corequisite_availability")
    assert det["status"] == "active" and det["found"] == 1
    json.dumps(report)


def test_analyze_live_corequisite_availability_inert_without_coreq_map(tmp_path):
    """The default path supplies no corequisite linkage (coreqs are excluded from
    the prereq fetch) -> F9 inert with a remedy naming --elumen-live."""
    records = [{"course": "MATH 227", "term": 2268, "class_nbr": "1",
                "days": "MW", "times": "9:00 AM - 10:15 AM", "units": 5}]
    program = {"code": "TEST", "title": "Test", "award": "AS", "courses": [
        {"course_id": "MATH 227", "recommended_semester": 1}], "major_choices": []}
    out = tmp_path / "coreq_inert.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "(test)", str(out),
        sections_override=records, program_override=program)
    block = report["results"]["analysis"]["corequisite_availability"]
    assert block["status"] == "inert"
    det = next(d for d in report["inert_detectors"]
               if d["detector"] == "corequisite_availability")
    assert det["status"] == "inert" and "elumen-live" in det["remedy"].lower()


# --- E11: infeasibility explainer (MUS) --------------------------------------
def test_analyze_live_infeasibility_active_isolates_minimal_conflict(tmp_path):
    """End-to-end: a required course whose units (20) exceed BOTH cohort caps is
    unbuildable in every cohort -> engine returns None plans -> E11 fires active
    and isolates the minimal conflicting set (the over-unit course itself)."""
    records = [{"course": "OVER 500", "term": 2268, "class_nbr": "1",
                "days": "MW", "times": "9:00 AM - 10:15 AM", "units": 20}]
    program = {"code": "TEST", "title": "Overload", "award": "AS", "courses": [
        {"course_id": "OVER 500", "recommended_semester": 1}], "major_choices": []}
    out = tmp_path / "infeasible.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "(test)", str(out),
        sections_override=records, program_override=program)
    block = report["results"]["analysis"]["infeasibility"]
    assert block["status"] == "active"
    ft = next(e for e in block["explained"] if e["cohort"] == "Full-time")
    assert ft["reproduced"] is True
    assert ft["minimal_conflict_set"] == ["OVER 500"]
    assert ft["background_only"] is False
    assert "STRUCTURAL" in block["label"]
    det = next(d for d in report["inert_detectors"] if d["detector"] == "infeasibility")
    assert det["status"] == "active" and det["found"] >= 1
    json.dumps(report)


def test_analyze_live_infeasibility_inert_when_buildable(tmp_path):
    """A normal buildable program -> every cohort has a plan -> E11 inert."""
    records = [{"course": "MATH 227", "term": 2268, "class_nbr": "1",
                "days": "MW", "times": "9:00 AM - 10:15 AM", "units": 5}]
    program = {"code": "TEST", "title": "Test", "award": "AS", "courses": [
        {"course_id": "MATH 227", "recommended_semester": 1}], "major_choices": []}
    out = tmp_path / "buildable.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "(test)", str(out),
        sections_override=records, program_override=program)
    block = report["results"]["analysis"]["infeasibility"]
    assert block["status"] == "inert"
    det = next(d for d in report["inert_detectors"] if d["detector"] == "infeasibility")
    assert det["status"] == "inert" and det.get("remedy")


# --- E14: minimal-perturbation recommender (inverse of E11) -------------------
def test_analyze_live_minimal_perturbation_active_recommends_add_section(tmp_path):
    """A required course with NO offered section -> F1 not-buildable -> E14 fires
    active and recommends ONE add_section that flips it buildable."""
    records = [{"course": "MATH 227", "term": 2268, "class_nbr": "1",
                "days": "MW", "times": "9:00 AM - 10:15 AM", "units": 5}]
    program = {"code": "TEST", "title": "Two-course", "award": "AS", "courses": [
        {"course_id": "MATH 227", "recommended_semester": 1},
        {"course_id": "ENGL 101", "recommended_semester": 1}],  # ENGL not offered
        "major_choices": []}
    out = tmp_path / "perturb.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "(test)", str(out),
        sections_override=records, program_override=program)
    block = report["results"]["analysis"]["minimal_perturbation"]
    assert block["status"] == "active"
    p = block["programs"][0]
    assert p["total_changes"] == 1
    adds = [a for a in p["actions"] if a["action"] == "add_section"]
    assert [a["course"] for a in adds] == ["ENGL 101"]
    assert p["buildable_after"] is True
    assert "OFFERING" in block["label"]
    det = next(d for d in report["inert_detectors"]
               if d["detector"] == "minimal_perturbation")
    assert det["status"] == "active" and det["found"] >= 1
    json.dumps(report)


def test_analyze_live_minimal_perturbation_inert_when_buildable(tmp_path):
    """Every required course offered, no conflict -> already buildable -> E14 inert
    (with a remedy describing what would activate it)."""
    records = [{"course": "MATH 227", "term": 2268, "class_nbr": "1",
                "days": "MW", "times": "9:00 AM - 10:15 AM", "units": 5}]
    program = {"code": "TEST", "title": "Test", "award": "AS", "courses": [
        {"course_id": "MATH 227", "recommended_semester": 1}], "major_choices": []}
    out = tmp_path / "perturb_inert.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "(test)", str(out),
        sections_override=records, program_override=program)
    block = report["results"]["analysis"]["minimal_perturbation"]
    assert block["status"] == "inert"
    det = next(d for d in report["inert_detectors"]
               if d["detector"] == "minimal_perturbation")
    assert det["status"] == "inert" and det.get("remedy")


# --- E15/F10: Title 5 contact-hour conformance --------------------------------
def test_analyze_live_contact_hours_active_flags_implausible_section(tmp_path):
    """A 1-unit section scheduled MTWThF 8 AM-12 PM (20 hrs/week) with woi=18 is
    implausibly OVER the Title 5 lecture band -> F10 active, flagged high."""
    records = [{"course": "PE 1", "term": 2268, "class_nbr": "1",
                "days": "MTWThF", "times": "8:00 AM - 12:00 PM",
                "units": "1", "woi": "18", "contact": "LEC"}]
    program = {"code": "TEST", "title": "Test", "award": "AS", "courses": [
        {"course_id": "PE 1", "recommended_semester": 1}], "major_choices": []}
    out = tmp_path / "ch.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "(test)", str(out),
        sections_override=records, program_override=program)
    block = report["results"]["analysis"]["contact_hours"]
    assert block["status"] == "active"
    assert len(block["flagged"]) == 1
    assert block["flagged"][0]["direction"] == "high"
    assert "CONFORMANCE" in block["label"].upper()
    det = next(d for d in report["inert_detectors"] if d["detector"] == "contact_hours")
    assert det["status"] == "active" and det["found"] >= 1
    json.dumps(report)


def test_analyze_live_contact_hours_inert_without_woi(tmp_path):
    """A section with a meeting time + units but NO weeks-of-instruction cannot be
    normalized -> F10 inert (with a remedy), never a false flag."""
    records = [{"course": "MATH 227", "term": 2268, "class_nbr": "1",
                "days": "MW", "times": "9:00 AM - 10:15 AM", "units": "5"}]
    program = {"code": "TEST", "title": "Test", "award": "AS", "courses": [
        {"course_id": "MATH 227", "recommended_semester": 1}], "major_choices": []}
    out = tmp_path / "ch_inert.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "(test)", str(out),
        sections_override=records, program_override=program)
    block = report["results"]["analysis"]["contact_hours"]
    assert block["status"] == "inert"
    det = next(d for d in report["inert_detectors"] if d["detector"] == "contact_hours")
    assert det["status"] == "inert" and det.get("remedy")


# --- E9: course-success / demand-vs-success escalation ------------------------
def test_analyze_live_demand_success_active_with_export(tmp_path):
    """A supplied CCCCO Data Mart export -> E9 active, joining the measured success
    rate onto the offered course; inert (with remedy) when no export is supplied."""
    success = tmp_path / "success.csv"
    success.write_text("Course,Success Rate,Retention Rate\nMATH 227,55%,82%\n")
    records = [{"course": "MATH 227", "term": 2268, "class_nbr": "1",
                "days": "MW", "times": "9:00 AM - 10:15 AM", "units": 5}]
    program = {"code": "TEST", "title": "Test", "award": "AS", "courses": [
        {"course_id": "MATH 227", "recommended_semester": 1}], "major_choices": []}
    out = tmp_path / "ds.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "(test)", str(out),
        sections_override=records, program_override=program,
        course_success_path=str(success))
    block = report["results"]["analysis"]["demand_success"]
    assert block["status"] == "active"
    assert block["granularity"] == "Course"
    assert block["matched"] == 1
    row = block["with_outcome"][0]
    assert row["course"] == "MATH 227" and row["success_rate"] == 0.55
    assert "MEASURED" in block["label"]
    det = next(d for d in report["inert_detectors"] if d["detector"] == "demand_success")
    assert det["status"] == "active"
    json.dumps(report)


def test_analyze_live_demand_success_inert_without_export(tmp_path):
    records = [{"course": "MATH 227", "term": 2268, "class_nbr": "1",
                "days": "MW", "times": "9:00 AM - 10:15 AM", "units": 5}]
    program = {"code": "TEST", "title": "Test", "award": "AS", "courses": [
        {"course_id": "MATH 227", "recommended_semester": 1}], "major_choices": []}
    out = tmp_path / "ds_inert.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "(test)", str(out),
        sections_override=records, program_override=program)
    block = report["results"]["analysis"]["demand_success"]
    assert block["status"] == "inert"
    det = next(d for d in report["inert_detectors"] if d["detector"] == "demand_success")
    assert det["status"] == "inert" and det.get("remedy")


# --- E13: equity-disaggregated course-success gap ----------------------------
def test_analyze_live_equity_success_gap_active_with_disaggregated_export(tmp_path):
    """A supplied disaggregated export -> E13 active: a below-reference subgroup gap
    surfaces and a <10 small cell is SUPPRESSED (counted, not shown)."""
    disagg = tmp_path / "disagg.csv"
    disagg.write_text("Course,Subgroup,Enrollment,Success Rate\n"
                      "MATH 227,All,1000,0.62\n"
                      "MATH 227,Group B,300,0.45\n"
                      "MATH 227,Group C,7,0.40\n")   # count 7 < 10 -> suppressed
    records = [{"course": "MATH 227", "term": 2268, "class_nbr": "1",
                "days": "MW", "times": "9:00 AM - 10:15 AM", "units": 5}]
    program = {"code": "TEST", "title": "Test", "award": "AS", "courses": [
        {"course_id": "MATH 227", "recommended_semester": 1}], "major_choices": []}
    out = tmp_path / "eq.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "(test)", str(out),
        sections_override=records, program_override=program,
        equity_success_path=str(disagg))
    block = report["results"]["analysis"]["equity_success_gap"]
    assert block["status"] == "active"
    course = block["courses"][0]
    assert course["course"] == "MATH 227" and course["reference_subgroup"] == "All"
    assert "Group B" in {g["subgroup"] for g in course["below_reference"]}
    assert course["suppressed_subgroups"] == 1            # Group C (7) suppressed
    assert "MEASURED" in block["label"]
    det = next(d for d in report["inert_detectors"] if d["detector"] == "equity_success_gap")
    assert det["status"] == "active"
    json.dumps(report)


def test_analyze_live_equity_success_gap_inert_without_export(tmp_path):
    records = [{"course": "MATH 227", "term": 2268, "class_nbr": "1",
                "days": "MW", "times": "9:00 AM - 10:15 AM", "units": 5}]
    program = {"code": "TEST", "title": "Test", "award": "AS", "courses": [
        {"course_id": "MATH 227", "recommended_semester": 1}], "major_choices": []}
    out = tmp_path / "eq_inert.xlsx"
    report = build_live_workbook.analyze_live(
        "LAMC", [2268], "(test)", str(out),
        sections_override=records, program_override=program)
    block = report["results"]["analysis"]["equity_success_gap"]
    assert block["status"] == "inert"
    det = next(d for d in report["inert_detectors"] if d["detector"] == "equity_success_gap")
    assert det["status"] == "inert" and det.get("remedy")


def test_analyze_import_equity_online_inert_no_modality(tmp_path):
    """A real schedule export carries no modality -> F6 active with evening/two_day
    computable but the online archetype NOT ASSESSED (computable:False)."""
    out = tmp_path / "imp.xlsx"
    report = build_live_workbook.analyze_import(
        "files/lamc_schedule_sample.xlsx", str(out))
    block = report["results"]["analysis"]["equity_exposure"]
    assert block["status"] == "active"
    assert block["by_design_count"] == 0          # no Notes column on the import path
    online = next(a for a in block["archetypes"] if a["key"] == "online")
    assert online["computable"] is False
    assert "modality" in online["reason"]
    for key in ("evening", "two_day"):
        assert next(a for a in block["archetypes"] if a["key"] == key)["computable"] is True
    json.dumps(report)
