from sources import schedule

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
                              "instr": "E. Sargsyan"}],
                "relsections": [{
                    "classNbr": "13956 (LAB)", "seats": "35", "woi": "16",
                    "status": "Open",
                    "meetings": [{"days": "Th", "times": "9:50 AM", "room": "CMS 128",
                                  "instr": "E. Sargsyan"}],
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
