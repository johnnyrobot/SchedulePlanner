"""Malformed-payload coverage for the live source parsers (m8-C t8).

Closes a real gap: nothing tested the source mappers against
well-formed-JSON-but-WRONG-SHAPE (200 OK, structurally wrong) — the most likely
real-world API drift. A 4xx/5xx or a non-JSON body is already covered by
test_http.py / the client tests via sources.http.get_json; here every payload
is valid JSON returned with a 200, but the *structure* is wrong:

  - schedule.fetch_sections: a section/course missing required keys;
  - program_mapper.fetch_program: a matched program missing masterRecordId,
    and a program whose course list is legitimately empty (must NOT error);
  - mapping.build_sections_df / build_catalog_df / build_programs_df: records
    missing 'term'/'course'/'course_id', a non-numeric term, and bad/blank
    units ('3-4' / '').

Where a parser previously raised an OPAQUE error (raw KeyError / ValueError /
AttributeError) on bad shape, the matching sources/*.py grew a MINIMAL guard
turning it into a SourceError/SourceDataError that names the endpoint/context.
Units coercion is verified to coerce or fail cleanly (never a raw ValueError).

Finally, a cross-check confirms app.fetch_live's existing try/except renders a
malformed live payload as an {error: ...} dict rather than letting it raise.
"""
import math

import pytest

import app
from sources import mapping, program_mapper as pm, schedule
from sources.http import SourceDataError, SourceError


# ---- schedule.fetch_sections: 200-OK but wrong shape ----------------------

def test_fetch_sections_subjects_not_a_list_raises_named_source_error(make_client):
    """'subjects' present but the wrong type (a dict, not a list). Iterating it
    must fail by endpoint name, not as an opaque AttributeError/TypeError."""
    client = make_client({"/listing/LAMC/2268": {"subjects": {"oops": "dict"}}})
    with pytest.raises(SourceError) as ei:
        schedule.fetch_sections("LAMC", [2268], client=client)
    msg = str(ei.value)
    assert "listing endpoint" in msg
    assert "LAMC 2268" in msg


def test_fetch_sections_course_missing_sections_key_is_tolerated(make_client):
    """A course with no 'sections' key is benign (no sections to emit) — the
    parser already uses .get() there, so it yields no records, not an error."""
    payload = {"subjects": [{"courses": [
        {"subject": "CS", "catalogNbr": "101"}  # no 'sections'
    ]}]}
    client = make_client({"/listing/LAMC/2268": payload})
    assert schedule.fetch_sections("LAMC", [2268], client=client) == []


# ---- program_mapper.fetch_program: 200-OK but wrong shape -----------------

def _pm_routes(group_programs):
    """Build a minimal Program Mapper route map where the STEM group's program
    list is whatever the test supplies (to forge wrong-shape programs)."""
    return {
        "/home-page-content": {"programGroups": [
            {"masterRecordId": "g1", "title": "STEM"}]},
        "/program-groups/g1": {"programs": group_programs},
    }


def test_fetch_program_matched_program_missing_master_record_id_raises_named(
        make_client):
    """A program matched by title but missing 'masterRecordId' (schema drift)
    must raise a named SourceDataError, not a bare KeyError 'masterRecordId'."""
    routes = _pm_routes([{"title": "Computer Science"}])  # no masterRecordId
    client = make_client(routes)
    with pytest.raises(SourceDataError) as ei:
        pm.fetch_program("LAMC", "computer science", client=client)
    msg = str(ei.value)
    assert "Program Mapper" in msg
    assert "masterRecordId" in msg


def test_fetch_program_empty_courses_is_not_an_error(make_client):
    """A well-formed program whose map has only non-COURSE elements yields an
    empty courses list — a legitimate response, NOT an error."""
    routes = _pm_routes([{"masterRecordId": "p1", "title": "Computer Science"}])
    routes["/programs/p1"] = {"pathways": [
        {"defaultPathway": True, "programMapId": "m1"}]}
    routes["/program-maps/m1"] = {"pathwayElements": [
        {"name": None, "recommendedOpportunity": {"type": "MILESTONE"}}]}
    client = make_client(routes)
    prog = pm.fetch_program("LAMC", "computer science", client=client)
    assert prog is not None
    assert prog["courses"] == []


# ---- mapping.build_sections_df: 200-OK records, wrong shape ---------------

def test_build_sections_df_missing_term_key_raises_named_source_error():
    with pytest.raises(SourceDataError) as ei:
        mapping.build_sections_df([{"course": "CS 101"}])  # no 'term'
    assert "term" in str(ei.value)


def test_build_sections_df_missing_course_key_raises_named_source_error():
    with pytest.raises(SourceDataError) as ei:
        mapping.build_sections_df([{"term": 2268}])  # no 'course'
    assert "course" in str(ei.value)


def test_build_sections_df_non_numeric_term_raises_named_source_error():
    with pytest.raises(SourceDataError) as ei:
        mapping.build_sections_df([{"term": "Fall", "course": "CS 101"}])
    msg = str(ei.value)
    assert "term" in msg


def test_build_sections_df_well_formed_still_works():
    df = mapping.build_sections_df([{"term": 2268, "course": "CS 101"}])
    assert list(df["CLASS"]) == ["CS 101"]
    assert int(df["Term"].iloc[0]) == 2268


# ---- mapping.build_catalog_df / build_programs_df: wrong shape -------------

def test_build_catalog_df_section_missing_course_raises_named():
    with pytest.raises(SourceDataError) as ei:
        mapping.build_catalog_df([{"term": 2268}], None)  # section has no course
    assert "course" in str(ei.value)


def test_build_catalog_df_program_course_missing_id_raises_named():
    program = {"courses": [{"title": "no id here"}]}  # course_id missing
    with pytest.raises(SourceDataError) as ei:
        mapping.build_catalog_df([], program)
    assert "course_id" in str(ei.value)


def test_build_programs_df_missing_program_code_raises_named():
    program = {"title": "X", "courses": [{"course_id": "CS 101"}]}  # no 'code'
    with pytest.raises(SourceDataError) as ei:
        mapping.build_programs_df(program)
    assert "code" in str(ei.value)


def test_build_programs_df_course_missing_id_raises_named():
    program = {"code": "X", "title": "X", "courses": [{"recommended_semester": 1}]}
    with pytest.raises(SourceDataError) as ei:
        mapping.build_programs_df(program)
    assert "course_id" in str(ei.value)


# ---- units coercion: bad/blank values coerce or fail cleanly --------------

def test_to_units_bad_and_blank_values_coerce_cleanly():
    """'3-4', '', None and outright garbage must coerce to a float (the default
    on failure) — never escape as a raw ValueError/TypeError."""
    assert mapping._to_units("3-4") == 3.0      # range -> low end
    assert mapping._to_units("") == 3.0         # blank -> default
    assert mapping._to_units("   ") == 3.0      # whitespace -> default
    assert mapping._to_units(None) == 3.0       # missing -> default
    assert mapping._to_units("not-a-number") == 3.0  # garbage -> default
    assert mapping._to_units(float("nan")) == 3.0    # NaN cell -> default
    assert mapping._to_units("4.5") == 4.5      # plain value preserved
    # whatever comes back is always a real float
    for v in ("3-4", "", None, "x", float("nan"), "4.5"):
        out = mapping._to_units(v)
        assert isinstance(out, float) and not math.isnan(out)


def test_build_catalog_df_blank_and_range_units_produce_clean_floats():
    """Through the real build path: a section with blank units and a program
    course with a range ('3-4') both yield clean float Units, no raw error."""
    sections = [{"term": 2268, "course": "CS 101", "units": ""}]
    program = {"code": "X", "title": "X", "courses": [
        {"course_id": "MATH 245", "units": "3-4"}]}
    df = mapping.build_catalog_df(sections, program)
    units = dict(zip(df["Course ID"], df["Units"]))
    assert units["CS 101"] == 3.0     # blank -> default
    assert units["MATH 245"] == 3.0   # '3-4' -> low end
    assert df["Units"].map(lambda v: isinstance(v, float)).all()


# ---- cross-check: app.fetch_live renders malformed payloads as {error} ----

def test_fetch_live_malformed_listing_shape_renders_as_error_dict(
        lamc_routes, make_client):
    """A 200-OK schedule listing with a structurally wrong 'subjects' (a dict)
    drives the new schedule guard -> SourceDataError -> analyze_live ->
    fetch_live's except path, surfacing a readable {error} dict, not a crash."""
    lamc_routes["/listing/LAMC/2268"] = {"subjects": {"wrong": "shape"}}
    client = make_client(lamc_routes)
    res = app.Api().fetch_live("LAMC", "2268", "Biology", client=client)
    assert isinstance(res, dict)
    assert "error" in res
    assert isinstance(res["error"], str) and res["error"]
    assert "live" in res["error"].lower()
