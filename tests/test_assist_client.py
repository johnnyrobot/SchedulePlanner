# tests/test_assist_client.py
import json
import pathlib

import pytest

from sources import assist
from sources.http import SourceDataError

FIX = pathlib.Path(__file__).parent / "fixtures"


def _routes():
    return {
        "/api/AcademicYears": json.loads((FIX / "assist_academic_years.json").read_text()),
        "/api/transferability/courses":
            json.loads((FIX / "assist_transferability_igetc_LAMC.json").read_text()),
    }


def test_institution_id_for_known_campus():
    assert assist.institution_id_for("LAMC") == 47
    assert assist.institution_id_for("lamc") == 47  # case-insensitive


def test_institution_id_for_unknown_campus_raises():
    with pytest.raises(SourceDataError) as ei:
        assist.institution_id_for("NOPE")
    assert "NOPE" in str(ei.value)


def test_fetch_ge_courses_maps_areas_and_filters_inactive(make_client):
    client = make_client(_routes())
    areas, year_id = assist.fetch_ge_courses("LAMC", "igetc", client=client)
    assert year_id == 77                       # latest academic year picked
    # ART 200 (endTermCode != "") is filtered out; ART 101 stays.
    assert sorted(areas["3A"]["courses"]) == ["ART 101"]
    assert areas["3A"]["title"] == "Arts"
    # One course can satisfy multiple areas.
    assert "BIOLOGY 7" in areas["5C"]["courses"]   # normalized (leading zero stripped)
    assert "BIOLOGY 7" in areas["5B"]["courses"]


def test_fetch_ge_courses_unknown_goal_raises(make_client):
    client = make_client(_routes())
    with pytest.raises(SourceDataError):
        assist.fetch_ge_courses("LAMC", "ap-credit", client=client)


def test_fetch_ge_courses_schema_drift_raises(make_client):
    client = make_client({"/api/AcademicYears": json.loads((FIX / "assist_academic_years.json").read_text()),
                          "/api/transferability/courses": {"wrong": "shape"}})
    with pytest.raises(SourceDataError) as ei:
        assist.fetch_ge_courses("LAMC", "igetc", client=client)
    assert "courseInformationList" in str(ei.value)


def test_400_refreshes_token_and_retries_once():
    # A purpose-built stateful client: first /transferability call 400s, second 200s.
    from tests.conftest import FakeResponse, load_fixture

    class Stateful:
        def __init__(self):
            self.cookies = {"X-XSRF-TOKEN": "tok"}
            self.api_calls = 0
        def get(self, url, params=None, headers=None):
            if url.rstrip("/").endswith("assist.org"):
                return FakeResponse({}, text="<html></html>")  # handshake
            if "/api/AcademicYears" in url:
                return FakeResponse(load_fixture("assist_academic_years.json"))
            if "/api/transferability/courses" in url:
                self.api_calls += 1
                if self.api_calls == 1:
                    return FakeResponse(None, status_code=400, text="")
                return FakeResponse(load_fixture("assist_transferability_igetc_LAMC.json"))
            raise AssertionError(url)
        def close(self): pass

    c = Stateful()
    areas, _ = assist.fetch_ge_courses("LAMC", "igetc", client=c)
    assert c.api_calls == 2          # retried exactly once after the 400
    assert "ART 101" in areas["3A"]["courses"]
