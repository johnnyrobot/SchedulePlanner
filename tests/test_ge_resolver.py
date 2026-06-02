# tests/test_ge_resolver.py
from sources import ge

PATTERN = {
    "pattern": "test", "display_name": "Test",
    "areas": [
        {"code": "1A", "title": "English", "count": 1, "units_min": 3},
        {"code": "3A", "title": "Arts", "count": 1, "units_min": 3},
        {"code": "5B", "title": "Bio", "count": 1, "units_min": 4},
        {"code": "4", "title": "Social", "count": 1, "units_min": 3},
    ],
}


def _assist():
    return {
        "1A": {"title": "English", "courses": ["ENGL 101", "ENGL 102", "ENGL 103", "ENGL 104"]},
        "3A": {"title": "Arts", "courses": ["ART 101"]},
        "5B": {"title": "Bio", "courses": ["BIOLOGY 7"]},  # satisfied by the major
        "4": {"title": "Social", "courses": ["PSYCH 1"]},  # eligible but not offered
    }


def _program():
    return {"courses": [{"course_id": "BIOLOGY 7", "recommended_semester": 1}],
            "ge_requirements": [{"area": "3A", "recommended_course": "ART 101"}]}


def test_resolve_hybrid_concrete_and_reserve():
    offered = {"ENGL 101", "ENGL 102", "ENGL 103", "ENGL 104", "ART 101"}  # PSYCH 1 NOT offered
    rows, cov = ge.resolve(PATTERN, _assist(), offered, _program(), concrete_threshold=3)
    by_area = {r["area"]: r for r in rows}
    # 1A has 4 offered options, no PM recommendation -> reserve.
    assert by_area["1A"]["resolution"] == "reserve"
    # 3A has 1 offered option AND a PM recommendation -> concrete.
    assert by_area["3A"]["resolution"] == "concrete"
    assert by_area["3A"]["candidates"] == ["ART 101"]
    assert by_area["3A"]["recommended"] == "ART 101"
    # 5B is satisfied by the major course BIOLOGY 7 -> no row emitted, recorded as shared.
    assert "5B" not in by_area
    assert {"area": "5B", "course": "BIOLOGY 7"} in cov["shared_with_major"]
    # 4 has an eligible course but none offered -> reserve + no_offering flag.
    assert by_area["4"]["resolution"] == "reserve"
    assert "no_offering" in next(a["flags"] for a in cov["areas"] if a["area"] == "4")


def test_resolve_flags_no_assist_data():
    pattern = {"areas": [{"code": "6", "title": "Ethnic Studies", "count": 1, "units_min": 3}]}
    rows, cov = ge.resolve(pattern, {}, set(), {"courses": [], "ge_requirements": []})
    assert rows[0]["resolution"] == "reserve"
    assert "no_assist_data" in next(a["flags"] for a in cov["areas"] if a["area"] == "6")


def test_resolve_disjoint_candidates_ge_to_ge_off():
    # A course eligible for two areas is assigned to exactly one (the smaller set).
    pattern = {"areas": [{"code": "3A", "title": "Arts", "count": 1, "units_min": 3},
                         {"code": "3B", "title": "Hum", "count": 1, "units_min": 3}]}
    assist = {"3A": {"title": "Arts", "courses": ["X 1"]},
              "3B": {"title": "Hum", "courses": ["X 1", "Y 2"]}}
    offered = {"X 1", "Y 2"}
    rows, _ = ge.resolve(pattern, assist, offered, {"courses": [], "ge_requirements": []},
                         concrete_threshold=3)
    placed = [c for r in rows for c in r["candidates"]]
    assert placed.count("X 1") == 1  # appears in exactly one area's candidate set
