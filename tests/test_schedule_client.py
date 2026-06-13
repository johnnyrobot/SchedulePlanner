import pytest

from sources import schedule
from sources.http import SourceDataError

LISTING_2268 = {
    "campuscode": "LAMC", "campusname": "Mission College",
    "termcode": "2268", "termname": "Fall 2026",
    "subjects": [{
        "code": "MATH", "name": "Mathematics", "courses": [{
            "subject": "MATH", "catalogNbr": "215", "descr": "Math Concepts",
            "units": "3.00", "sections": [{
                "classNbr": "13955 (LEC)", "seats": "35", "woi": "16",
                "dates": "08/31/26 - 12/20/26", "status": "Open",
                "meetings": [{"days": "T", "times": "8:50 AM", "room": "CMS 128",
                              "instr": "STAFF"}],
                "relsections": [{
                    "classNbr": "13956 (LAB)", "seats": "35", "woi": "16",
                    "status": "Open",
                    "meetings": [{"days": "Th", "times": "9:50 AM", "room": "CMS 128",
                                  "instr": "STAFF"}],
                    "relsections": [], "classType": ["HYFLEX", "OER"]}],
                "classType": ["HYFLEX", "OER"]}]}]}],
}


def test_fetch_sections_flattens_relsections(make_client):
    client = make_client({"/listing/LAMC/2268": LISTING_2268})
    records = schedule.fetch_sections("LAMC", [2268], client=client)
    # one LEC + its one LAB relsection
    assert len(records) == 2
    assert {r["course"] for r in records} == {"MATH 215"}
    assert records[0]["term"] == 2268
    assert records[0]["units"] == "3.00"
    assert records[0]["status"] == "Open"
    assert records[0]["modality"] == ["HYFLEX", "OER"]
    assert records[1]["class_nbr"] == "13956 (LAB)"


def test_fetch_sections_carries_session_dates_and_woi(make_client):
    # FF5 (capture-only): the live API exposes a per-section session `dates` range
    # and `woi` (weeks of instruction). Both must SURVIVE onto the section record
    # so a future calendar/duration check can use them; nothing consumes them yet.
    client = make_client({"/listing/LAMC/2268": LISTING_2268})
    records = schedule.fetch_sections("LAMC", [2268], client=client)
    lec = records[0]
    assert lec["dates"] == "08/31/26 - 12/20/26"
    assert lec["woi"] == "16"
    # A relsection with no `dates` key fails open to "" (never a crash/KeyError).
    lab = records[1]
    assert lab["dates"] == ""
    assert lab["woi"] == "16"


def test_fetch_sections_requests_each_term(make_client):
    client = make_client({"/listing/LAMC/": LISTING_2268})
    schedule.fetch_sections("LAMC", [2264, 2268], client=client)
    urls = [c["url"] for c in client.calls]
    assert any("/listing/LAMC/2264" in u for u in urls)
    assert any("/listing/LAMC/2268" in u for u in urls)
    assert len(client.calls) == 2


def test_get_subjects_returns_payload(make_client):
    client = make_client({"/subjects/LAMC/2268": [{"code": "MATH", "name": "Mathematics"}]})
    subjects = schedule.get_subjects("LAMC", "2268", client=client)
    assert subjects == [{"code": "MATH", "name": "Mathematics"}]


def test_fetch_sections_empty_subjects_yields_no_records(make_client):
    # A term with no published classes is legitimate: 'subjects' present, empty.
    client = make_client({"/listing/LAMC/2270": {"subjects": []}})
    assert schedule.fetch_sections("LAMC", [2270], client=client) == []


def test_fetch_sections_raises_endpoint_named_on_missing_subjects_key(make_client):
    # Drift: the listing came back without a 'subjects' key. Fail by endpoint name.
    client = make_client({"/listing/LAMC/2268": {"unexpected": "shape"}})
    with pytest.raises(SourceDataError) as ei:
        schedule.fetch_sections("LAMC", [2268], client=client)
    msg = str(ei.value)
    assert "listing endpoint" in msg
    assert "LAMC 2268" in msg


# --- element-level schema-drift guards (review M5) ------------------------
# The list-type guard above only ensures 'subjects' is a list; it does NOT
# ensure each element is a JSON object. A string/null/number element makes
# subject.get(...) / course.get(...) raise a bare AttributeError one level
# deeper — the exact opaque failure the list guard exists to prevent. Each
# nesting level (subject -> course -> section/relsection -> meeting) must fail
# loud by endpoint + path instead.

def test_fetch_sections_raises_on_non_dict_subject_element(make_client):
    # 'subjects' is a list (passes the list guard) but an element is a bare
    # string. Must name the endpoint + path, not raise a bare AttributeError.
    client = make_client({"/listing/LAMC/2268": {"subjects": ["MATH"]}})
    with pytest.raises(SourceDataError) as ei:
        schedule.fetch_sections("LAMC", [2268], client=client)
    msg = str(ei.value)
    assert "LAMC 2268" in msg
    assert "subjects[0]" in msg
    assert "str" in msg            # names the offending type


def test_fetch_sections_raises_on_non_dict_course_element(make_client):
    client = make_client({"/listing/LAMC/2268": {
        "subjects": [{"code": "MATH", "courses": ["MATH 215"]}]}})
    with pytest.raises(SourceDataError) as ei:
        schedule.fetch_sections("LAMC", [2268], client=client)
    msg = str(ei.value)
    assert "subjects[0].courses[0]" in msg
    assert "str" in msg


def test_fetch_sections_raises_on_non_dict_section_element(make_client):
    client = make_client({"/listing/LAMC/2268": {"subjects": [{"code": "MATH", "courses": [
        {"subject": "MATH", "catalogNbr": "215", "sections": ["13955"]}]}]}})
    with pytest.raises(SourceDataError) as ei:
        schedule.fetch_sections("LAMC", [2268], client=client)
    msg = str(ei.value)
    assert "sections[0]" in msg
    assert "str" in msg


def test_fetch_sections_raises_on_non_dict_relsection_element(make_client):
    client = make_client({"/listing/LAMC/2268": {"subjects": [{"code": "MATH", "courses": [
        {"subject": "MATH", "catalogNbr": "215", "sections": [
            {"classNbr": "13955 (LEC)", "meetings": [], "relsections": ["13956"]}]}]}]}})
    with pytest.raises(SourceDataError) as ei:
        schedule.fetch_sections("LAMC", [2268], client=client)
    msg = str(ei.value)
    assert "relsections[0]" in msg
    assert "str" in msg


def test_fetch_sections_raises_on_non_dict_meeting_element(make_client):
    client = make_client({"/listing/LAMC/2268": {"subjects": [{"code": "MATH", "courses": [
        {"subject": "MATH", "catalogNbr": "215", "sections": [
            {"classNbr": "13955 (LEC)", "meetings": ["T 8:50 AM"], "relsections": []}]}]}]}})
    with pytest.raises(SourceDataError) as ei:
        schedule.fetch_sections("LAMC", [2268], client=client)
    msg = str(ei.value)
    assert "meetings[0]" in msg
    assert "str" in msg


def test_fetch_sections_raises_on_null_meeting_element(make_client):
    # A FALSY non-dict meeting element (null/0/"") must ALSO fail loud by path,
    # not slip a truthiness gate and bare-crash on meeting.get(...). M5 names
    # 'null' element drift explicitly, and a null meeting is the most realistic
    # drift value; sibling levels already catch None, so this must too.
    client = make_client({"/listing/LAMC/2268": {"subjects": [{"code": "MATH", "courses": [
        {"subject": "MATH", "catalogNbr": "215", "sections": [
            {"classNbr": "13955 (LEC)", "meetings": [None], "relsections": []}]}]}]}})
    with pytest.raises(SourceDataError) as ei:
        schedule.fetch_sections("LAMC", [2268], client=client)
    msg = str(ei.value)
    assert "meetings[0]" in msg
    assert "NoneType" in msg            # names the offending type, no bare crash


def test_fetch_sections_tolerates_section_without_meetings(make_client):
    # Non-overfire: an empty meetings list is legitimate (async/online) and must
    # yield a record with empty day/time, NOT trip the new meeting-element guard.
    client = make_client({"/listing/LAMC/2268": {"subjects": [{"code": "MATH", "courses": [
        {"subject": "MATH", "catalogNbr": "215", "descr": "Async",
         "sections": [{"classNbr": "99999 (LEC)", "meetings": [], "relsections": [],
                       "classType": ["ONLINE"]}]}]}]}})
    records = schedule.fetch_sections("LAMC", [2268], client=client)
    assert len(records) == 1
    assert records[0]["course"] == "MATH 215"
    assert records[0]["days"] == ""
    assert records[0]["times"] == ""
