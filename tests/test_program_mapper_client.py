from sources import program_mapper as pm

HOME = {"programGroups": [{"masterRecordId": "g1", "title": "STEM"}]}
GROUP_G1 = {"programs": [{
    "masterRecordId": "p1", "title": "Computer Science",
    "awardShortTitle": "Associate in Science for Transfer"}]}
PROGRAM_P1 = {"pathways": [{"defaultPathway": True, "programMapId": "m1"}]}
MAP_M1 = {"pathwayElements": [
    {"name": "CS 101", "shortDescription": "Intro CS",
     "requirement": {"requirementType": "MAJOR_CORE"},
     "recommendedOpportunity": {"type": "COURSE", "term": {"termNumber": 1},
                                "courseCode": "CS 101", "minUnits": 3.0}},
    {"name": "MATH 245", "shortDescription": "Calculus I",
     "requirement": {"requirementType": "MAJOR_REQUIRED"},
     "recommendedOpportunity": {"type": "COURSE", "term": {"termNumber": 2},
                                "courseCode": "MATH 245", "minUnits": 5.0}},
    {"name": None, "recommendedOpportunity": {"type": "MILESTONE"}},
]}

ROUTES = {
    "/home-page-content": HOME,
    "/program-groups/g1": GROUP_G1,
    "/programs/p1": PROGRAM_P1,
    "/program-maps/m1": MAP_M1,
}


def test_search_program_matches_by_title(make_client):
    client = make_client(ROUTES)
    found = pm.search_program("LAMC", "computer science", client=client)
    assert found["masterRecordId"] == "p1"


def test_fetch_program_returns_courses_with_semester_and_units(make_client):
    client = make_client(ROUTES)
    prog = pm.fetch_program("LAMC", "computer science", client=client)
    assert prog["title"] == "Computer Science"
    assert prog["code"] == "COMPUTER-SCIENCE"          # derived from title, not award
    ids = [c["course_id"] for c in prog["courses"]]
    assert ids == ["CS 101", "MATH 245"]               # MILESTONE element skipped
    cs = prog["courses"][0]
    assert cs["recommended_semester"] == 1
    assert cs["units"] == 3.0


def test_fetch_program_returns_none_when_no_match(make_client):
    client = make_client(ROUTES)
    assert pm.fetch_program("LAMC", "underwater basket weaving", client=client) is None


def test_get_all_programs_empty_when_no_groups(make_client):
    client = make_client({"/home-page-content": {"somethingElse": []}})
    assert pm.get_all_programs("LAMC", client=client) == []


def test_get_program_courses_falls_back_to_first_pathway(make_client):
    routes = {
        "/programs/p2": {"pathways": [{"programMapId": "m2"}]},  # no defaultPathway
        "/program-maps/m2": {"pathwayElements": [
            {"name": "ENGL 101", "recommendedOpportunity": {
                "type": "COURSE", "term": {"termNumber": 1}, "minUnits": 3.0}}]},
    }
    client = make_client(routes)
    courses = pm.get_program_courses("LAMC", "p2", client=client)
    assert [c["course_id"] for c in courses] == ["ENGL 101"]
