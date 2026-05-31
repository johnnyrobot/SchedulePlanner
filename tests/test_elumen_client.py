"""Tests for the REAL eLumen Public Portal prereq client (sources/elumen_client.py).

OFFLINE by default: every test except the one ``@pytest.mark.live`` test runs
with NO network. The fixture-backed tests replay the committed, sanitized,
real-shape capture (tests/fixtures/elumen_courses_LAMC_response.json — 7 real
LAMC courses) and assert the AUTHORITATIVE golden CNF map produced by the REAL
``sources.elumen.build_prereq_map``, then round-trip a couple through the REAL
``engine.parse_prereq``.

The eLumen source is REAL / public / unauthenticated; the leaf ``itemType`` is
THE prereq discriminator (Co-Requisite + Advisory are EXCLUDED); the conversion
is UNDER-approximate (never false-infeasible). ToU / rate-limit / human approval
for the live endpoint remain PENDING — the one networked test is deselected by
the ``-m "not live"`` default in pytest.ini.

The constructed parser trees below are LABELLED 'constructed' — they exercise the
verified grammar (nodes with an "item" are leaves; type AND|OR|SINGLE) but are
hand-built, NOT captured responses.
"""
import json
import pathlib

import pytest

import engine
from sources import elumen, elumen_client as ec
from sources.http import SourceDataError
from sources.mapping import _norm as mapping_norm
from sources.prereq_cnf import ConversionResult

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "elumen_courses_LAMC_response.json"


def _groups(s):  # parse a catalog CNF string with the REAL engine parser
    return engine.parse_prereq(s)


@pytest.fixture
def fixture_payload():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def fixture_wrappers(fixture_payload):
    return fixture_payload["_embedded"]["courses"]


@pytest.fixture
def fixture_records(fixture_wrappers):
    return [ec.course_record(w) for w in fixture_wrappers]


# ============================================================ FIXTURE GOLDEN
GOLDEN_CNF = {
    "ANATOMY 1": "(BIOLOGY 3 OR BIOLOGY 5)",
    "BIOTECH 102": "(BIOTECH 2) AND (BIOTECH 3)",
    "MICRO 20": "(BIOLOGY 3 OR BIOLOGY 5 OR BIOTECH 2)",
    "BIOTECH 3": "(BIOTECH 2)",
    "BIOTECH 8": "",
    "BIOLOGY 3": "",
    "KIN MAJ 102": "",
}


def test_fixture_is_real_shape(fixture_payload):
    # Envelope sanity: the verified _embedded.courses + pagination shape.
    assert set(fixture_payload) >= {"_embedded", "_links", "pagination"}
    courses = fixture_payload["_embedded"]["courses"]
    assert len(courses) == 7
    # Every fullCourseInfo is a JSON STRING that parses.
    for w in courses:
        assert isinstance(w["fullCourseInfo"], str)
        json.loads(w["fullCourseInfo"])  # must not raise


def test_build_prereq_map_matches_golden(fixture_records):
    # The AUTHORITATIVE assertion: records -> REAL build_prereq_map -> exact
    # golden CNF strings (dnf_to_cnf alphabetizes literals within an OR clause).
    prereqs, results = elumen.build_prereq_map(fixture_records)
    assert prereqs == GOLDEN_CNF
    # results carry ConversionResult flags for every course.
    assert set(results) == set(GOLDEN_CNF)
    for cid, res in results.items():
        assert isinstance(res, ConversionResult)


def test_golden_round_trips_through_engine(fixture_records):
    prereqs, _ = elumen.build_prereq_map(fixture_records)
    # A real OR of two prereq alternatives.
    assert _groups(prereqs["ANATOMY 1"]) == [["BIOLOGY 3", "BIOLOGY 5"]]
    # A real AND of two prereqs.
    assert _groups(prereqs["BIOTECH 102"]) == [["BIOTECH 2"], ["BIOTECH 3"]]
    # MICRO 20: Co-Requisite CHEM leaves excluded, duplicate prereq branches
    # deduped down to a single OR clause.
    assert _groups(prereqs["MICRO 20"]) == [["BIOLOGY 3", "BIOLOGY 5", "BIOTECH 2"]]
    # No-prereq courses parse to [].
    assert _groups(prereqs["BIOTECH 8"]) == []
    assert _groups(prereqs["BIOLOGY 3"]) == []


def test_micro20_excludes_corequisites(fixture_wrappers):
    # MICRO 20's only raw paths each pair a Prerequisite with a Co-Requisite
    # CHEM (051/065). The parser must EXCLUDE the Co-Req leaves entirely.
    micro = next(w for w in fixture_wrappers if w["code"] == "MICRO020")
    rec = ec.course_record(micro)
    flat = {lit for branch in rec["dnf"] for lit in branch}
    assert flat == {"BIOLOGY 3", "BIOLOGY 5", "BIOTECH 2"}
    assert "CHEM 51" not in flat and "CHEM 65" not in flat
    # Provenance keeps the dropped Co-Req leaves visible (residual-risk surfacing).
    assert "CHEM 51 [Co-Requisite]" in rec["raw"]
    assert "CHEM 65 [Co-Requisite]" in rec["raw"]


def test_biotech8_advisory_and_coreq_only_has_no_prereq(fixture_wrappers):
    # BIOTECH 8: every leaf is Co-Requisite or Advisory -> no hard prereq.
    bt8 = next(w for w in fixture_wrappers if w["code"] == "BIOTECH008")
    rec = ec.course_record(bt8)
    assert rec["dnf"] == []
    assert "MATH 227 [Advisory]" in rec["raw"]


def test_course_identity_uses_wrapper_code_not_subject_number(fixture_wrappers):
    # MICRO020's wrapper number is "020" but identity comes from the code.
    micro = next(w for w in fixture_wrappers if w["code"] == "MICRO020")
    assert micro["number"] == "020"
    assert ec.course_record(micro)["course_id"] == "MICRO 20"


# ============================================================ PARSER (constructed)
def _leaf(code, item_type="Prerequisite", is_course=True, node_type="SINGLE"):
    """Build a CONSTRUCTED leaf node in the verified grammar (a node carrying an
    'item' is a leaf regardless of its 'type')."""
    return {"type": node_type, "blockList": [],
            "item": {"isCourse": is_course, "itemType": item_type, "code": code}}


def test_parser_and_of_two_constructed():
    # CONSTRUCTED: AND of two Prerequisite leaves -> one branch with both.
    node = {"type": "AND", "blockList": [_leaf("BIOTECH002"), _leaf("BIOTECH003")]}
    assert ec.requisites_to_dnf(node) == [["BIOTECH 2", "BIOTECH 3"]]


def test_parser_or_of_two_alternatives_constructed():
    # CONSTRUCTED: OR of two Prerequisite alternatives -> two branches.
    node = {"type": "OR", "blockList": [_leaf("BIOLOGY005"), _leaf("BIOLOGY003")]}
    assert ec.requisites_to_dnf(node) == [["BIOLOGY 5"], ["BIOLOGY 3"]]


def test_parser_extracts_prerequisite_only_constructed():
    # CONSTRUCTED: an AND mixing a Prerequisite with a Co-Requisite keeps ONLY
    # the prerequisite (the Co-Req contributes nothing).
    node = {"type": "AND", "blockList": [
        _leaf("CHEM051", item_type="Co-Requisite"),
        _leaf("BIOLOGY003", item_type="Prerequisite"),
    ]}
    assert ec.requisites_to_dnf(node) == [["BIOLOGY 3"]]


def test_parser_excludes_advisory_and_corequisite_constructed():
    # CONSTRUCTED: a tree of ONLY Advisory + Co-Requisite leaves -> no prereq.
    node = {"type": "OR", "blockList": [
        {"type": "AND", "blockList": [
            _leaf("BIOTECH002", item_type="Co-Requisite"),
            _leaf("MATH227", item_type="Advisory"),
        ]},
    ]}
    assert ec.requisites_to_dnf(node) == []


def test_parser_corequisite_hyphen_collapse_constructed():
    # CONSTRUCTED: "Corequisite" (no hyphen) must collapse-equal "Co-Requisite"
    # and still be EXCLUDED.
    node = _leaf("CHEM051", item_type="Corequisite")
    assert ec.requisites_to_dnf(node) == []


def test_parser_single_node_leaf_constructed():
    # CONSTRUCTED: a SINGLE node carrying an item is a LEAF (despite its type).
    node = _leaf("BIOTECH002", node_type="SINGLE")
    assert ec.requisites_to_dnf(node) == [["BIOTECH 2"]]


def test_parser_non_course_leaf_dropped_constructed():
    # CONSTRUCTED: a Prerequisite leaf with isCourse=false contributes nothing
    # (e.g. an assessment/placement requirement, not a course).
    node = {"type": "OR", "blockList": [
        _leaf("ENGL101", is_course=True),
        _leaf("PLACEMENT", is_course=False),
    ]}
    assert ec.requisites_to_dnf(node) == [["ENGL 101"]]


def test_parser_empty_requisites_constructed():
    # CONSTRUCTED: an empty / None requisites tree -> no prereq ([]).
    assert ec.requisites_to_dnf({}) == []
    assert ec.requisites_to_dnf(None) == []
    assert ec.requisites_to_dnf({"type": "OR", "blockList": []}) == []


def test_parser_nested_and_within_or_constructed():
    # CONSTRUCTED: OR of [AND(A,B)] and [single C] -> two branches.
    node = {"type": "OR", "blockList": [
        {"type": "AND", "blockList": [_leaf("MATH245"), _leaf("MATH246")]},
        _leaf("PHYS185"),
    ]}
    assert ec.requisites_to_dnf(node) == [["MATH 245", "MATH 246"], ["PHYS 185"]]


def test_parser_self_reference_via_build_prereq_map():
    # A course listing ITSELF as a prereq must be dropped by build_prereq_map
    # (which passes course_id as gated_course to dnf_to_cnf).
    node = {"type": "OR", "blockList": [_leaf("CHEM101"), _leaf("CHEM102")]}
    rec = {"course_id": "CHEM 102", "raw": "...", "dnf": ec.requisites_to_dnf(node)}
    prereqs, _ = elumen.build_prereq_map([rec])
    flat = [lit for g in _groups(prereqs["CHEM 102"]) for lit in g]
    assert "CHEM 102" not in flat          # self-ref dropped
    assert flat == ["CHEM 101"]


# ============================================================ NORMALIZER
@pytest.mark.parametrize("raw,expected", [
    ("BIOTECH002", "BIOTECH 2"),       # strip leading zeros of the digit run
    ("ACCTG002", "ACCTG 2"),
    ("NRS-HCA060", "NRS-HCA 60"),      # hyphenated multi-token subject
    ("KIN MAJ102", "KIN MAJ 102"),     # multi-word subject
    ("STATC1000", "STAT C1000"),       # C-ID split
    ("PSYC065", "PSYC 65"),            # C-guard: NOT mis-split to 'PSY C065'
    ("MATH261", "MATH 261"),
    ("201A", "201A"),                  # bare number, letter suffix preserved
    ("010CE", "10CE"),                 # strip zeros, keep CE suffix
    ("C1000", "C1000"),                # bare C-ID stays intact
    ("MICRO020", "MICRO 20"),
    ("ANATOMY001", "ANATOMY 1"),
])
def test_normalize_course_code(raw, expected):
    assert ec.normalize_course_code(raw) == expected


def test_normalizer_output_idempotent_under_mapping_norm():
    # Output MUST be stable under sources.mapping._norm (UPPERCASE + single-space)
    # so normalized literals match catalog Course IDs.
    for raw in ("BIOTECH002", "KIN MAJ102", "STATC1000", "PSYC065", "C1000",
                "010CE", "201A", "MATH261"):
        out = ec.normalize_course_code(raw)
        assert mapping_norm(out) == out


def test_normalizer_degrades_gracefully():
    # No clean match -> upper + collapsed, never a crash.
    assert ec.normalize_course_code("  weird input  ") == "WEIRD INPUT"
    assert ec.normalize_course_code("") == ""


# ============================================================ COVERAGE
def test_coverage_surfaces_unmatched_prereq_target():
    # A prereq literal whose target course is NOT among the fetched ids (nor the
    # known program/section set) is surfaced as an advising gap.
    records = [
        {"course_id": "PHYS 102", "raw": "", "dnf": [["MATH 245"], ["PHYS 185"]]},
        {"course_id": "MATH 245", "raw": "", "dnf": []},
    ]
    cov = ec.compute_coverage(records, known_course_ids={"PHYS 102"})
    assert cov["courses_fetched"] == 2
    assert cov["courses_with_prereqs"] == 1
    # MATH 245 is known (fetched); PHYS 185 is not -> surfaced.
    assert cov["unmatched_prereq_targets"] == ["PHYS 185"]


def test_coverage_known_course_ids_resolve_targets():
    records = [{"course_id": "PHYS 102", "raw": "", "dnf": [["PHYS 185"]]}]
    # When PHYS 185 IS in the known program/section set, it is NOT unmatched.
    cov = ec.compute_coverage(records, known_course_ids={"PHYS 185"})
    assert cov["unmatched_prereq_targets"] == []


def test_coverage_surfaces_requested_courses_without_record():
    records = [{"course_id": "MATH 245", "raw": "", "dnf": []}]
    cov = ec.compute_coverage(
        records, requested_course_ids={"MATH 245", "PHYS 102", "chem 101"}
    )
    # Requested-but-not-returned (normalized): PHYS 102 + CHEM 101.
    assert cov["requested_courses_without_record"] == ["CHEM 101", "PHYS 102"]


# ============================================================ FETCH (offline)
def test_fetch_courses_single_page_offline(make_client, fixture_payload):
    # conftest FakeClient matches by URL substring + ignores query params, so a
    # route on "/public/courses" returns the whole fixture payload (single page).
    client = make_client({"/public/courses": fixture_payload})
    wrappers = ec.fetch_courses("LAMC", "BIOTECH", client=client)
    assert len(wrappers) == 7
    # Exactly one request: the 7-course page is < pageSize(25) so paging stops.
    assert len(client.calls) == 1
    # The verified request params were sent.
    params = client.calls[0]["params"]
    assert params["status"] == "approved"
    assert params["tenant"] == "lamission.elumenapp.com"
    assert params["query"] == "BIOTECH"
    assert params["pageSize"] == 25
    assert params["page"] == 1


def test_fetch_prereq_records_offline(make_client, fixture_payload):
    client = make_client({"/public/courses": fixture_payload})
    records, fetched = ec.fetch_prereq_records("LAMC", ["BIOTECH"], client=client)
    assert fetched == {
        "ANATOMY 1", "BIOTECH 102", "MICRO 20", "BIOTECH 3",
        "BIOTECH 8", "BIOLOGY 3", "KIN MAJ 102",
    }
    by_id = {r["course_id"] for r in records}
    assert by_id == fetched  # no duplicates


def test_prereq_map_for_campus_offline(make_client, fixture_payload):
    client = make_client({"/public/courses": fixture_payload})
    prereqs, results, coverage = ec.prereq_map_for_campus(
        "LAMC", ["BIOTECH"], client=client,
    )
    assert prereqs == GOLDEN_CNF
    assert coverage["courses_fetched"] == 7
    assert coverage["courses_with_prereqs"] == 4  # ANATOMY1, BIOTECH102, MICRO20, BIOTECH3
    # BIOLOGY 5 and BIOTECH 2 are real PREREQ targets in this fixture, but the
    # fixture's own catalog only carries BIOLOGY 3 / BIOTECH 3 — so those two
    # targets are correctly surfaced as unmatched (an advising gap, not silent).
    assert coverage["unmatched_prereq_targets"] == ["BIOLOGY 5", "BIOTECH 2"]
    assert set(results) == set(GOLDEN_CNF)


def test_tenant_for_known_and_unknown():
    assert ec.tenant_for("LAMC") == "lamission.elumenapp.com"
    assert ec.tenant_for("elac") == "elac.elumenapp.com"  # case-insensitive
    with pytest.raises(SourceDataError):
        ec.tenant_for("NOPE")


# ============================================================ PAGING
class _PagingFakeClient:
    """A tiny local fake client whose get(url, params, headers) returns a full
    page then a short final page based on params['page']. (conftest's FakeClient
    ignores params, so it cannot drive multi-page behavior.)"""

    def __init__(self, page_size=25):
        self.page_size = page_size
        self.pages_requested = []

    def _wrapper(self, code):
        info = json.dumps({"requisites": {}})
        return {"code": code, "subject": code, "number": "1", "fullCourseInfo": info}

    def get(self, url, params=None, headers=None):
        from tests.conftest import FakeResponse
        page = params["page"]
        self.pages_requested.append(page)
        if page == 1:
            courses = [self._wrapper(f"FULL{i:03d}") for i in range(self.page_size)]
        elif page == 2:
            courses = [self._wrapper("SHORT001"), self._wrapper("SHORT002")]
        else:  # pragma: no cover - paging must stop before here
            courses = []
        payload = {"_embedded": {"courses": courses},
                   "pagination": {"page": page, "pageSize": self.page_size}}
        return FakeResponse(payload, url=url)

    def close(self):
        return None


def test_fetch_courses_follows_then_stops_paging():
    client = _PagingFakeClient(page_size=25)
    wrappers = ec.fetch_courses("LAMC", "FULL", client=client, page_size=25)
    # Page 1 (25, == page_size) -> fetch page 2 (2, < page_size) -> STOP.
    assert client.pages_requested == [1, 2]
    assert len(wrappers) == 27


# ============================================================ PURITY
def test_pure_path_opens_no_socket(monkeypatch, fixture_records):
    # The normalizer, parser, and build_prereq_map must open NO socket. Mirror
    # tests/test_elumen_prereq_mapping.py: forbid httpx.Client construction.
    import httpx

    def _boom(*a, **k):  # pragma: no cover - only fires on a regression
        raise AssertionError("a pure eLumen-client path opened a network client")

    monkeypatch.setattr(httpx, "Client", _boom)
    # normalize + parse
    assert ec.normalize_course_code("BIOTECH002") == "BIOTECH 2"
    node = {"type": "AND", "blockList": [
        {"type": "SINGLE", "blockList": [],
         "item": {"isCourse": True, "itemType": "Prerequisite", "code": "BIOTECH002"}},
    ]}
    assert ec.requisites_to_dnf(node) == [["BIOTECH 2"]]
    # build map from already-fetched records
    prereqs, _ = elumen.build_prereq_map(fixture_records)
    assert prereqs == GOLDEN_CNF


# ============================================================ DRIFT / ERRORS
def test_course_record_malformed_fullcourseinfo_raises():
    # A malformed fullCourseInfo JSON string is loud drift, named to the source.
    bad = {"code": "CHEM101", "fullCourseInfo": "{not valid json"}
    with pytest.raises(SourceDataError) as exc:
        ec.course_record(bad)
    assert "eLumen" in str(exc.value)
    assert "CHEM 101" in str(exc.value)


def test_course_record_missing_fullcourseinfo_is_no_prereq():
    # A wrapper with no course info is a valid "no prerequisite" record.
    rec = ec.course_record({"code": "CHEM101"})
    assert rec == {"course_id": "CHEM 101", "raw": "", "dnf": []}


def test_course_record_missing_code_falls_back_to_subject_number():
    rec = ec.course_record({"subject": "CHEM", "number": "101",
                            "fullCourseInfo": json.dumps({"requisites": {}})})
    assert rec["course_id"] == "CHEM 101"


def test_course_record_no_identity_returns_none():
    assert ec.course_record({"fullCourseInfo": json.dumps({"requisites": {}})}) is None


def test_fetch_courses_missing_embedded_stops_cleanly(make_client):
    # A page with no _embedded block is the documented "no more courses" signal:
    # paging stops, no error, empty result.
    client = make_client({"/public/courses": {"pagination": {"page": 1}}})
    assert ec.fetch_courses("LAMC", "ZZZ", client=client) == []


def test_fetch_courses_embedded_wrong_type_raises(make_client):
    # _embedded present but the wrong type is drift -> SourceDataError, named.
    client = make_client({"/public/courses": {"_embedded": ["oops"]}})
    with pytest.raises(SourceDataError) as exc:
        ec.fetch_courses("LAMC", "ZZZ", client=client)
    assert "eLumen" in str(exc.value)


def test_fetch_courses_non_dict_response_raises(make_client, fake_response):
    # A top-level JSON array (not an object) is drift -> SourceDataError.
    client = make_client({"/public/courses": fake_response(["not", "an", "object"])})
    with pytest.raises(SourceDataError):
        ec.fetch_courses("LAMC", "ZZZ", client=client)


# ============================================================ GUARDRAILS
# Production-use guardrails: throttle, bounded backoff retry, session cache, and
# bounded (no broad crawl) fetch. These are the safety rails that make
# --elumen-live a polite, opt-in admin/testing path. All offline.
import httpx as _httpx  # noqa: E402  (local to the guardrail tests)

from sources.http import SourceError, SourceHTTPError  # noqa: E402


def _status_response(status_code):
    """A FakeResponse that raise_for_status()es with the given HTTP status."""
    from tests.conftest import FakeResponse
    return FakeResponse(None, status_code=status_code, text="", url="https://x/")


class _CountingClient:
    """Returns a queued sequence of responses/exceptions per get(), counting calls.

    Each queue item is either a FakeResponse (returned) or an Exception INSTANCE
    (raised), letting a test script transient failures then a success.
    """

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def get(self, url, params=None, headers=None):
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        return None


def _one_page_response():
    # A FakeResponse (not a bare dict): _CountingClient returns it straight to
    # get_json, which calls .raise_for_status()/.json() on it.
    from tests.conftest import FakeResponse
    return FakeResponse(
        {"_embedded": {"courses": []}, "pagination": {"page": 1, "pageSize": 25}})


def test_throttle_spaces_successive_requests(monkeypatch):
    # The shared _RateLimiter must sleep between successive requests (never before
    # the first). We capture sleeps instead of actually waiting.
    slept = []
    monkeypatch.setattr(ec.time, "sleep", lambda s: slept.append(s))
    # Force monotonic to advance by 0 so the full interval is "remaining".
    monkeypatch.setattr(ec.time, "monotonic", lambda: 0.0)
    limiter = ec._RateLimiter(1.0)
    limiter.wait()  # first: no sleep
    assert slept == []
    limiter.wait()  # second: sleep ~1.0
    limiter.wait()  # third: sleep ~1.0
    assert len(slept) == 2
    assert all(abs(s - 1.0) < 1e-9 for s in slept)


def test_throttle_disabled_when_delay_zero(monkeypatch):
    slept = []
    monkeypatch.setattr(ec.time, "sleep", lambda s: slept.append(s))
    limiter = ec._RateLimiter(0)
    limiter.wait(); limiter.wait(); limiter.wait()
    assert slept == []  # request_delay=0 -> no throttling (injected-client case)


def test_fetch_prereq_records_uses_one_shared_limiter(monkeypatch, make_client,
                                                      fixture_payload):
    # Across MULTIPLE subjects the throttle must span queries (a shared limiter),
    # not reset per subject. Two subjects, single page each -> exactly one sleep.
    slept = []
    monkeypatch.setattr(ec.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(ec.time, "monotonic", lambda: 0.0)
    client = make_client({"/public/courses": fixture_payload})
    ec.fetch_prereq_records("LAMC", ["BIOTECH", "BIOLOGY"], client=client,
                            request_delay=1.0)
    # 2 subjects: 1st request no sleep, 2nd request one sleep -> exactly 1 sleep.
    assert len(slept) == 1


def test_retry_then_succeed_on_transient_500(monkeypatch):
    # A 500 (transient) is retried with bounded backoff, then succeeds.
    sleeps = []
    monkeypatch.setattr(ec.time, "sleep", lambda s: sleeps.append(s))
    client = _CountingClient([
        _status_response(503),
        _status_response(500),
        _one_page_response(),  # 3rd attempt succeeds
    ])
    wrappers = ec.fetch_courses("LAMC", "ZZZ", client=client, request_delay=0)
    assert wrappers == []           # empty page, but the call SUCCEEDED
    assert client.calls == 3        # two failures + one success
    assert sleeps == [1.0, 2.0]     # exponential backoff: 1, 2 (BACKOFF_BASE*2**n)


def test_retry_exhausts_then_raises_clean_error(monkeypatch):
    # Persistent 429s exhaust MAX_RETRIES and then propagate a clean
    # SourceHTTPError (never a raw httpx traceback).
    monkeypatch.setattr(ec.time, "sleep", lambda s: None)
    client = _CountingClient([_status_response(429)] * (ec.MAX_RETRIES + 1))
    with pytest.raises(SourceHTTPError):
        ec.fetch_courses("LAMC", "ZZZ", client=client, request_delay=0)
    # Initial try + MAX_RETRIES retries.
    assert client.calls == ec.MAX_RETRIES + 1


def test_non_retryable_404_fails_immediately(monkeypatch):
    # A 404 is a hard client error: fail on the FIRST try, no retry/backoff.
    sleeps = []
    monkeypatch.setattr(ec.time, "sleep", lambda s: sleeps.append(s))
    client = _CountingClient([_status_response(404)])
    with pytest.raises(SourceHTTPError):
        ec.fetch_courses("LAMC", "ZZZ", client=client, request_delay=0)
    assert client.calls == 1
    assert sleeps == []  # no backoff on a non-transient error


def test_shape_drift_does_not_retry(monkeypatch):
    # SourceDataError (JSON/shape drift) must NOT be retried — retrying can't fix
    # bad data. A non-dict body raises immediately on the first call.
    sleeps = []
    monkeypatch.setattr(ec.time, "sleep", lambda s: sleeps.append(s))
    from tests.conftest import FakeResponse
    client = _CountingClient([FakeResponse(["not", "an", "object"])])
    with pytest.raises(SourceDataError):
        ec.fetch_courses("LAMC", "ZZZ", client=client, request_delay=0)
    assert client.calls == 1
    assert sleeps == []


def test_timeout_is_retryable(monkeypatch):
    # A transport timeout (bare SourceError, not a status error) is transient.
    monkeypatch.setattr(ec.time, "sleep", lambda s: None)
    client = _CountingClient([
        SourceError("eLumen: request timed out"),
        _one_page_response(),
    ])
    wrappers = ec.fetch_courses("LAMC", "ZZZ", client=client, request_delay=0)
    assert wrappers == []
    assert client.calls == 2


def test_session_cache_dedupes_repeated_subject(make_client, fixture_payload):
    # A session cache memoizes (tenant, query, pageSize): the SAME subject twice
    # hits the network once.
    client = make_client({"/public/courses": fixture_payload})
    cache = {}
    ec.fetch_courses("LAMC", "BIOTECH", client=client, request_delay=0, cache=cache)
    first = len(client.calls)
    ec.fetch_courses("LAMC", "BIOTECH", client=client, request_delay=0, cache=cache)
    assert len(client.calls) == first  # second call served from cache, no request
    assert (("lamission.elumenapp.com", "BIOTECH", 25)) in cache


def test_cache_returns_independent_copies(make_client, fixture_payload):
    # A caller mutating the returned list must not corrupt the cached entry.
    client = make_client({"/public/courses": fixture_payload})
    cache = {}
    a = ec.fetch_courses("LAMC", "BIOTECH", client=client, request_delay=0, cache=cache)
    a.clear()
    b = ec.fetch_courses("LAMC", "BIOTECH", client=client, request_delay=0, cache=cache)
    assert len(b) == 7  # cached entry intact despite mutation of the first result


def test_fetch_prereq_records_dedupes_repeated_subject(make_client, fixture_payload):
    # fetch_prereq_records shares one cache across queries, so a duplicate subject
    # in the query list is fetched once.
    client = make_client({"/public/courses": fixture_payload})
    ec.fetch_prereq_records("LAMC", ["BIOTECH", "BIOTECH"], client=client,
                            request_delay=0)
    assert len(client.calls) == 1  # second BIOTECH served from the shared cache


def test_is_retryable_classification():
    # Direct unit coverage of the transient/permanent classifier.
    req = _httpx.Request("GET", "https://x/")
    resp429 = _httpx.Response(429, request=req)
    resp404 = _httpx.Response(404, request=req)
    e429 = SourceHTTPError("429"); e429.__cause__ = _httpx.HTTPStatusError(
        "429", request=req, response=resp429)
    e404 = SourceHTTPError("404"); e404.__cause__ = _httpx.HTTPStatusError(
        "404", request=req, response=resp404)
    assert ec._is_retryable(e429) is True
    assert ec._is_retryable(e404) is False
    assert ec._is_retryable(SourceError("timeout")) is True   # transport error
    assert ec._is_retryable(SourceDataError("drift")) is False  # never retry data


# ============================================================ LIVE (deselected)
@pytest.mark.live
def test_live_lamc_endpoint_schema():
    # Hits the REAL public eLumen endpoint for LAMC. Deselected by default
    # (pytest.ini: -m "not live"). Asserts ONLY the documented schema, not any
    # specific course content. ToU/rate-limit/human-approval still pending.
    wrappers = ec.fetch_courses("LAMC", "BIOLOGY", page_size=25, max_pages=1)
    assert isinstance(wrappers, list) and wrappers, "expected >=1 LAMC course"
    saw_requisites_tree = False
    for w in wrappers:
        assert isinstance(w.get("fullCourseInfo"), str)
        info = json.loads(w["fullCourseInfo"])  # fullCourseInfo parses
        if isinstance(info.get("requisites"), dict) and info["requisites"]:
            saw_requisites_tree = True
    assert saw_requisites_tree, "expected >=1 course to expose a requisites tree"
