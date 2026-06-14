"""E6 — tolerant-reader schema-drift CONTRACT guard.

The four LACCD live sources (schedule, Program Mapper, eLumen, ASSIST) are
undocumented and can drift without notice. The "tolerant reader" discipline is to
bind ONLY the fields we use, against the ACTUAL captured wire — and to fail LOUD,
by endpoint name, when that shape changes.

This module is the systematic, one-place version of the scattered per-parser
guards (it covers the M4/M5 class root-cause): for every endpoint it pins both

  * the POSITIVE contract — the real parser consumes its committed fixture and
    yields the well-formed output downstream code binds to (so a re-captured
    fixture, or a parser change, that drifts the shape fails HERE); and
  * the NEGATIVE contract — a container-type drift raises a NAMED ``SourceDataError``
    (never a bare ``AttributeError`` / ``TypeError`` / ``KeyError``), so drift
    surfaces by endpoint instead of an opaque stack trace.

Pure + offline: the committed ``tests/fixtures`` payloads are replayed through the
shared ``FakeClient`` (no network). The schedule contract is additionally wired as
an advisory CI canary (``.github/workflows/ci.yml``) that re-parses the committed
fixture on a schedule, so a real drift trips it without gating PRs.
"""
import json
import pathlib

import pytest

from sources import assist, elumen, elumen_client, program_mapper, schedule
from sources.http import SourceDataError

_FX = pathlib.Path(__file__).parent / "fixtures"
# Real LAMC identifiers the committed fixtures were captured under (mirrors conftest).
BIOLOGY_PID = "a4060608-61af-8a69-5d00-66fc77c61774"


def _fx(name):
    return json.loads((_FX / name).read_text())


# ===================================================================== schedule
def test_schedule_positive_contract_binds_the_wire_shape(make_client, lamc_routes):
    """The committed listing parses into records carrying every field downstream
    code binds (course / term / days / times / units / woi / class_nbr)."""
    records = schedule.fetch_sections("LAMC", [2268], client=make_client(lamc_routes))
    assert records, "the committed listing fixture must yield section records"
    required = {"course", "term", "days", "times", "units", "woi", "class_nbr", "status"}
    for r in records:
        assert required <= set(r), f"record missing bound fields: {required - set(r)}"
    assert {r["term"] for r in records} == {2268}


def test_schedule_negative_contract_non_dict_listing_is_named(make_client):
    client = make_client({"/listing/LAMC/2268": ["not", "a", "dict"]})
    with pytest.raises(SourceDataError) as ei:
        schedule.fetch_sections("LAMC", [2268], client=client)
    assert "listing endpoint" in str(ei.value)


def test_schedule_negative_contract_subjects_wrong_type_is_named(make_client):
    # 'subjects' present but a dict, not a list -> would blow up opaquely on
    # subject.get(...); must name the endpoint instead.
    client = make_client({"/listing/LAMC/2268": {"subjects": {"oops": "dict"}}})
    with pytest.raises(SourceDataError) as ei:
        schedule.fetch_sections("LAMC", [2268], client=client)
    assert "subjects" in str(ei.value) and "list" in str(ei.value)


# =============================================================== program mapper
def test_program_mapper_positive_contract_lists_programs(make_client, lamc_routes):
    programs = program_mapper.get_all_programs("LAMC", client=make_client(lamc_routes))
    assert programs, "the committed home + program-group fixtures must yield programs"
    assert all(isinstance(p, dict) and "title" in p for p in programs)


def test_program_mapper_positive_contract_program_courses(make_client, lamc_routes):
    out = program_mapper.get_program_courses("LAMC", BIOLOGY_PID,
                                             client=make_client(lamc_routes))
    # get_program_courses returns the required courses + GE requirements + choices.
    courses = out[0] if isinstance(out, tuple) else out.get("courses")
    assert courses, "the committed program-map fixture must yield required courses"


def test_program_mapper_negative_contract_home_missing_groups_is_named(make_client):
    client = make_client({"/home-page-content": {"unexpected": "shape"}})
    with pytest.raises(SourceDataError) as ei:
        program_mapper.get_all_programs("LAMC", client=client)
    assert "home-page-content" in str(ei.value)


def test_program_mapper_negative_contract_program_map_not_dict_is_named(make_client):
    routes = {
        f"/programs/{BIOLOGY_PID}": {
            "pathways": [{"defaultPathway": True, "programMapId": "MAP1"}]},
        "/program-maps/MAP1": ["not", "a", "dict"],
    }
    with pytest.raises(SourceDataError) as ei:
        program_mapper.get_program_courses("LAMC", BIOLOGY_PID,
                                           client=make_client(routes))
    assert "program-maps" in str(ei.value)


# ===================================================================== eLumen
def test_elumen_positive_contract_parses_raw_wire_to_prereq_map():
    # The raw eLumen HAL+JSON binds at _embedded.courses; each wrapper -> a record
    # via course_record, then build_prereq_map. If that path drifts, this fails.
    payload = _fx("elumen_courses_LAMC_response.json")
    wrappers = payload["_embedded"]["courses"]
    assert wrappers, "the committed eLumen fixture must carry course wrappers"
    records = [r for r in (elumen_client.course_record(w) for w in wrappers) if r]
    assert records
    prereqs, results = elumen.build_prereq_map(records)
    assert isinstance(prereqs, dict) and isinstance(results, dict)


def test_elumen_negative_contract_non_dict_wrapper_is_named():
    with pytest.raises(SourceDataError) as ei:
        elumen_client.course_record(["not", "a", "dict"])
    assert "wrapper" in str(ei.value).lower()


def test_elumen_negative_contract_missing_courses_list_is_named(tmp_path):
    # load_elumen_fixture (the self-defined simple-shape reader) names the file.
    bad = tmp_path / "drifted.json"
    bad.write_text(json.dumps({"unexpected": "shape"}))
    with pytest.raises(SourceDataError) as ei:
        elumen.load_elumen_fixture(bad)
    assert "courses" in str(ei.value)


def test_elumen_negative_contract_malformed_dnf_is_named():
    with pytest.raises(SourceDataError) as ei:
        elumen.parse_elumen_dnf({"course_id": "MATH 245", "dnf": "MATH 125"})
    assert "MATH 245" in str(ei.value) and "dnf" in str(ei.value)


# ===================================================================== ASSIST
def test_assist_positive_contract_parses_transfer_areas():
    data = _fx("assist_transferability_igetc_LAMC.json")
    areas = assist._areas_from_courses(data, source="ASSIST transferability (test)")
    assert isinstance(areas, dict) and areas
    for entry in areas.values():
        assert "title" in entry and isinstance(entry["courses"], list)
        assert entry["courses"] == sorted(entry["courses"])  # deterministic order


def test_assist_negative_contract_missing_course_list_is_named():
    with pytest.raises(SourceDataError) as ei:
        assist._areas_from_courses({"unexpected": "shape"}, source="ASSIST (test)")
    assert "courseInformationList" in str(ei.value)


# ============================================================ live wire canary
@pytest.mark.live
def test_schedule_live_wire_still_matches_the_contract():
    """CANARY (network): hit the REAL LACCD schedule endpoint and assert it STILL
    parses into the bound shape. Deselected by default (``-m "not live"``); run
    only from the scheduled, continue-on-error live canary job, so real wire drift
    surfaces early WITHOUT gating PRs."""
    records = schedule.fetch_sections("LAMC", [schedule.DEFAULT_TERMS[0]])
    assert records, "the live schedule endpoint returned no parseable sections"
    required = {"course", "term", "days", "times", "units", "woi", "class_nbr", "status"}
    for r in records[:50]:
        assert required <= set(r), f"live record missing bound fields: {required - set(r)}"
