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


# ---- E7: per-term fail-open + shared retry/backoff -------------------------
def test_fetch_sections_fail_open_skips_failing_term_and_surfaces_it(make_client, error_resp):
    # term 2266 errors (503) but 2268 succeeds: the good term's records still come
    # back AND the failed term is surfaced in status["skipped"] — never silently
    # dropped. max_retries=0 makes the failing term fail fast (no real sleep).
    client = make_client({
        "/listing/LAMC/2268": LISTING_2268,
        "/listing/LAMC/2266": error_resp(503),
    })
    status = {}
    records = schedule.fetch_sections("LAMC", [2266, 2268], client=client,
                                      status=status, max_retries=0)
    assert records, "the surviving term's sections must still be returned"
    assert {r["term"] for r in records} == {2268}
    assert status["skipped"] and status["skipped"][0]["term"] == 2266
    assert "error" in status["skipped"][0]


def test_fetch_sections_raises_when_every_term_fails(make_client, error_resp):
    # total failure stays LOUD — an all-terms-down fetch must raise, not return an
    # empty list that masquerades as "no classes offered".
    client = make_client({
        "/listing/LAMC/2266": error_resp(503),
        "/listing/LAMC/2268": error_resp(503),
    })
    with pytest.raises(Exception):
        schedule.fetch_sections("LAMC", [2266, 2268], client=client, max_retries=0)


def test_fetch_sections_no_skips_leaves_status_skipped_empty(make_client):
    status = {}
    schedule.fetch_sections("LAMC", [2268], client=make_client({"/listing/LAMC/2268": LISTING_2268}),
                            status=status)
    assert status.get("skipped") == []
