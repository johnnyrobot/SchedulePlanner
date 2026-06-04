"""FF4 — bounded live cross-program demand fan-out (sources.live_demand).

These tests prove the producer aggregates N already-fetchable program maps into
the SAME ``ProgramDemand`` shape ``sources.program_lists.load_program_lists``
emits (``required`` / ``listed`` / ``titles``) so F2's
``cross_program_bottleneck.bottleneck_report`` needs no change.

Everything runs through the shared FakeClient (tests/conftest.py) — NO network.
Multi-program fan-out is exercised with synthetic in-test program maps so the
programs-per-course count can be asserted exactly.
"""
import pytest

from sources import live_demand, program_lists
from sources.mapping import _norm


# --- synthetic multi-program fan-out fixtures --------------------------------
# A program-map response is the /program-maps/{id} payload: a pathwayElements
# list. We build minimal ones so fetch_program_by_id resolves a known course set
# per program (recommendedOpportunity.type == "COURSE").
def _course_element(code, *, units=3, term=1):
    return {
        "name": code,
        "shortDescription": f"{code} title",
        "requirement": {"requirementType": "MAJOR_CORE"},
        "recommendedOpportunity": {
            "type": "COURSE", "courseCode": code, "courseName": f"{code} title",
            "minUnits": units, "term": {"termNumber": term},
        },
    }


def _program_map(*codes):
    return {"pathwayElements": [_course_element(c) for c in codes]}


def _routes_for(programs):
    """programs: list of (pid, mapid, title, award, [course codes]).

    Builds a FakeClient route map where each program resolves via:
      /programs/{pid}        -> a detail doc pointing at its default pathway/map
      /program-maps/{mapid}  -> the synthetic program map of those course codes
    """
    routes = {}
    for pid, mapid, _title, _award, codes in programs:
        routes[f"/programs/{pid}"] = {
            "pathways": [{"defaultPathway": True, "programMapId": mapid}]}
        routes[f"/program-maps/{mapid}"] = _program_map(*codes)
    return routes


def test_fanout_aggregates_programs_per_course(make_client):
    # Three programs; MATH 245 is required by all three, CHEM 101 by two,
    # BIOLOGY 7 by one. The aggregate counts must reflect that exactly.
    specs = [
        ("pid-A", "map-A", "Alpha", "AS", ["MATH 245", "CHEM 101", "BIOLOGY 7"]),
        ("pid-B", "map-B", "Beta", "AA", ["MATH 245", "CHEM 101"]),
        ("pid-C", "map-C", "Gamma", "AS-T", ["MATH 245"]),
    ]
    client = make_client(_routes_for(specs))
    ids = [{"program_id": pid, "title": title, "award": award}
           for pid, _m, title, award, _c in specs]

    demand = live_demand.fan_out_demand("LAMC", ids, client=client)

    # Same ProgramDemand shape as the offline loader.
    assert isinstance(demand, program_lists.ProgramDemand)
    assert demand.n_plans == 3
    # programs-per-course counts (keyed on _norm, like the offline loader).
    assert len(demand.required[_norm("MATH 245")]) == 3
    assert len(demand.required[_norm("CHEM 101")]) == 2
    assert len(demand.required[_norm("BIOLOGY 7")]) == 1
    # listed mirrors required for the fan-out (every fetched course is listed).
    assert demand.listed[_norm("MATH 245")] == demand.required[_norm("MATH 245")]
    # titles map each plan id -> its program title.
    assert set(demand.titles.values()) >= {"Alpha", "Beta", "Gamma"}


def test_fanout_plan_codes_are_unique_per_program_id(make_client):
    # Two DISTINCT programs that share a title (the real "Biology AS-T" vs
    # "Biology AS" case) must count as TWO programs, not collapse to one — so the
    # plan code is keyed on the unique program id, never the title slug.
    specs = [
        ("pid-1", "map-1", "Biology", "AS-T", ["BIOLOGY 7"]),
        ("pid-2", "map-2", "Biology", "AS", ["BIOLOGY 7"]),
    ]
    client = make_client(_routes_for(specs))
    ids = [{"program_id": pid, "title": title, "award": award}
           for pid, _m, title, award, _c in specs]

    demand = live_demand.fan_out_demand("LAMC", ids, client=client)
    assert demand.n_plans == 2
    assert len(demand.required[_norm("BIOLOGY 7")]) == 2


def test_fanout_empty_id_list_returns_empty_demand(make_client):
    # The bound: an empty id list yields an EMPTY ProgramDemand (F2 stays inert),
    # NEVER a fabricated map.
    demand = live_demand.fan_out_demand("LAMC", [], client=make_client({}))
    assert isinstance(demand, program_lists.ProgramDemand)
    assert demand.n_plans == 0
    assert demand.required == {} and demand.listed == {}


def test_fanout_skips_a_failing_program_and_keeps_the_rest(make_client, error_resp):
    # FAIL OPEN per program: one program 404s, the others still aggregate. The
    # fan-out never aborts wholesale (that would silently drop ALL demand).
    specs = [
        ("pid-ok", "map-ok", "Good", "AS", ["MATH 245"]),
    ]
    routes = _routes_for(specs)
    routes["/programs/pid-bad"] = error_resp(404)
    client = make_client(routes)
    ids = [
        {"program_id": "pid-ok", "title": "Good", "award": "AS"},
        {"program_id": "pid-bad", "title": "Bad", "award": "AS"},
    ]
    demand = live_demand.fan_out_demand("LAMC", ids, client=client)
    # Only the good program contributed; the bad one was skipped, not raised.
    assert demand.n_plans == 1
    assert len(demand.required[_norm("MATH 245")]) == 1


def test_fanout_respects_max_programs_cap(make_client):
    # The bound is OPT-IN AND CAPPED: max_programs truncates the fan-out so a
    # caller can never accidentally fan over an unbounded list.
    specs = [
        ("pid-A", "map-A", "Alpha", "AS", ["MATH 245"]),
        ("pid-B", "map-B", "Beta", "AS", ["CHEM 101"]),
        ("pid-C", "map-C", "Gamma", "AS", ["BIOLOGY 7"]),
    ]
    client = make_client(_routes_for(specs))
    ids = [{"program_id": pid, "title": title, "award": award}
           for pid, _m, title, award, _c in specs]

    demand = live_demand.fan_out_demand("LAMC", ids, client=client, max_programs=2)
    assert demand.n_plans == 2  # only the first two ids were fetched
    # The third program's course was never fetched.
    assert _norm("BIOLOGY 7") not in demand.required


def test_fanout_feeds_bottleneck_report_active(make_client):
    # End-to-end: the fan-out ProgramDemand drives F2 to ACTIVE against offered
    # sections (the headline win — F2 was inert on a bare live fetch).
    import cross_program_bottleneck
    specs = [
        ("pid-A", "map-A", "Alpha", "AS", ["MATH 245"]),
        ("pid-B", "map-B", "Beta", "AS", ["MATH 245"]),
    ]
    client = make_client(_routes_for(specs))
    ids = [{"program_id": pid, "title": title, "award": award}
           for pid, _m, title, award, _c in specs]
    demand = live_demand.fan_out_demand("LAMC", ids, client=client)

    sections = [{"course": "MATH 245", "term": 2268, "days": "MW",
                 "times": "9:00 AM - 10:15 AM", "Cap Enrl": 40, "Tot Enrl": 40,
                 "status": "Closed", "facil_id": ""}]
    block = cross_program_bottleneck.bottleneck_report(demand, sections)
    assert block["status"] == "active"
    top = block["leaderboard"][0]
    assert top["course"] == _norm("MATH 245")
    assert top["n_programs"] == 2
