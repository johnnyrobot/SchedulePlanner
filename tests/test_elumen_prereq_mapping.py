"""Tests for the FIXTURE-ONLY eLumen DNF parser -> CNF prereq map (sources/elumen.py).

FIXTURE-ONLY / NOT-VALIDATED-ON-REAL-DATA: there is no real eLumen endpoint,
auth, or captured response. This whole slice parses the committed self-defined
fixture (tests/fixtures/elumen_prereqs_LAMC.json) whose shape we DEFINE and
DOCUMENT; nothing here touches a socket. The asserted CNF strings are the exact
output of the (already-committed) sources/prereq_cnf.dnf_to_cnf converter and
round-trip through the REAL engine.parse_prereq parser.

Coverage: parse_elumen_dnf normalizes the committed DNF; build_prereq_map yields
CNF catalog strings that round-trip engine.parse_prereq exactly (incl. the
PHYS 102 distribution and the BIO 200 shared-coreq factoring case);
build_prereq_map passes course_id as gated_course so dnf_to_cnf normalizes it;
no socket is opened (pure fixture read, no http client used).
"""
import json
import pathlib

import pytest

import engine
from sources.elumen import (
    load_elumen_fixture,
    parse_elumen_dnf,
    build_prereq_map,
)
from sources.http import SourceDataError
from sources.prereq_cnf import DEFAULT_MAX_CLAUSES, ConversionResult

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "elumen_prereqs_LAMC.json"


def _groups(s):  # parse with the REAL engine parser
    return engine.parse_prereq(s)


@pytest.fixture
def fixture_records():
    return load_elumen_fixture(FIXTURE)


# --------------------------------------------------- fixture loader
def test_load_elumen_fixture_returns_course_records(fixture_records):
    # Loader is a pure file read; it returns the list of course records as-is.
    ids = [rec["course_id"] for rec in fixture_records]
    assert ids == ["ENGL 102", "MATH 246", "PHYS 102", "CHEM 102", "BIO 200", "STAT 101"]
    # Provenance fields carried through.
    assert all("raw" in rec and "dnf" in rec for rec in fixture_records)


def test_load_elumen_fixture_uses_no_socket(fixture_records, monkeypatch):
    # Defensive: the loader must not open an httpx client. If it did, this would
    # blow up — we forbid httpx.Client construction for the duration of the load.
    import httpx

    def _boom(*a, **k):  # pragma: no cover - only fires on a regression
        raise AssertionError("eLumen fixture loader opened a network client")

    monkeypatch.setattr(httpx, "Client", _boom)
    again = load_elumen_fixture(FIXTURE)
    assert [r["course_id"] for r in again] == [r["course_id"] for r in fixture_records]


# --------------------------------------------------- parse_elumen_dnf
def test_parse_elumen_dnf_normalizes(fixture_records):
    by_id = {rec["course_id"]: rec for rec in fixture_records}
    # Whitespace/case collapse via mapping._norm; nesting preserved.
    assert parse_elumen_dnf(by_id["PHYS 102"]) == [
        ["MATH 245", "MATH 246"], ["PHYS 185"],
    ]
    assert parse_elumen_dnf(by_id["ENGL 102"]) == [["ENGL 101"]]
    # Empty DNF (no prereq) stays empty.
    assert parse_elumen_dnf(by_id["STAT 101"]) == []


def test_parse_elumen_dnf_normalizes_messy_spellings():
    rec = {"course_id": "X 1", "raw": "...", "dnf": [["math  245", " chem 101 "]]}
    assert parse_elumen_dnf(rec) == [["MATH 245", "CHEM 101"]]


def test_parse_elumen_dnf_rejects_malformed_nesting():
    # A non-list-of-lists payload is a malformed record -> SourceDataError.
    with pytest.raises(SourceDataError):
        parse_elumen_dnf({"course_id": "X 1", "dnf": "MATH 245"})
    with pytest.raises(SourceDataError):
        parse_elumen_dnf({"course_id": "X 1", "dnf": [["A"], "B"]})


def test_parse_elumen_dnf_missing_dnf_key_is_no_prereq():
    # A record with no 'dnf' field is treated as "no prerequisite" ([]),
    # not an error (a real export may omit the field for unrestricted courses).
    assert parse_elumen_dnf({"course_id": "X 1", "raw": "none"}) == []
    assert parse_elumen_dnf({"course_id": "X 1", "dnf": None}) == []


# --------------------------------------------------- build_prereq_map
def test_build_prereq_map_yields_round_tripping_cnf(fixture_records):
    prereqs, results = build_prereq_map(fixture_records)

    # Exact expected catalog strings (the committed dnf_to_cnf output).
    assert prereqs == {
        "ENGL 102": "(ENGL 101)",
        "MATH 246": "(MATH 245)",
        "PHYS 102": "(MATH 245 OR PHYS 185) AND (MATH 246 OR PHYS 185)",
        "CHEM 102": "(CHEM 101 OR CHEM 105)",
        "BIO 200": "(BIO 101 OR BIO 102) AND (CHEM 101)",
        "STAT 101": "",
    }

    # Every value round-trips exactly through the REAL engine parser.
    assert _groups(prereqs["ENGL 102"]) == [["ENGL 101"]]
    assert _groups(prereqs["MATH 246"]) == [["MATH 245"]]
    assert _groups(prereqs["PHYS 102"]) == [
        ["MATH 245", "PHYS 185"], ["MATH 246", "PHYS 185"],
    ]
    assert _groups(prereqs["CHEM 102"]) == [["CHEM 101", "CHEM 105"]]
    assert _groups(prereqs["BIO 200"]) == [["BIO 101", "BIO 102"], ["CHEM 101"]]
    assert _groups(prereqs["STAT 101"]) == []


def test_build_prereq_map_phys_102_distribution(fixture_records):
    # The load-bearing exact-distribution case called out in the acceptance:
    # (MATH 245 AND MATH 246) OR PHYS 185 -> CNF distributes to two OR-clauses.
    prereqs, _ = build_prereq_map(fixture_records)
    assert prereqs["PHYS 102"] == "(MATH 245 OR PHYS 185) AND (MATH 246 OR PHYS 185)"
    assert _groups(prereqs["PHYS 102"]) == [
        ["MATH 245", "PHYS 185"], ["MATH 246", "PHYS 185"],
    ]


def test_build_prereq_map_bio_200_shared_coreq_factoring(fixture_records):
    # The shared-coreq factoring case: CHEM 101 is common to both branches, so
    # it factors out into its own AND-clause: (BIO 101 OR BIO 102) AND (CHEM 101).
    prereqs, _ = build_prereq_map(fixture_records)
    assert prereqs["BIO 200"] == "(BIO 101 OR BIO 102) AND (CHEM 101)"
    assert _groups(prereqs["BIO 200"]) == [["BIO 101", "BIO 102"], ["CHEM 101"]]


def test_build_prereq_map_results_are_all_exact(fixture_records):
    # For this small fixture every conversion is exact (no budget fallback).
    _, results = build_prereq_map(fixture_records)
    assert set(results) == {
        "ENGL 102", "MATH 246", "PHYS 102", "CHEM 102", "BIO 200", "STAT 101",
    }
    for cid, res in results.items():
        assert isinstance(res, ConversionResult)
        assert res.exact is True, cid
        assert res.fallback_reason is None, cid


def test_build_prereq_map_passes_course_id_as_gated_course():
    # A course listing ITSELF as a prereq must be dropped (a course can't be its
    # own prereq). build_prereq_map passes course_id as gated_course; dnf_to_cnf
    # _norm's it before the self-ref comparison. A lower-cased / messy course_id
    # must still drop the self-ref (proves the normalization happens BEFORE the
    # comparison — a raw-cased 'chem 102' still cancels the 'CHEM 102' literal).
    records = [
        {"course_id": "chem 102", "dnf": [["CHEM 101"], ["CHEM 102"]]},
    ]
    prereqs, results = build_prereq_map(records)
    # Self-ref (CHEM 102) dropped; only the real alternative (CHEM 101) remains.
    flat = [lit for g in _groups(prereqs["CHEM 102"]) for lit in g]
    assert "CHEM 102" not in flat                 # self-ref gone (normalized + dropped)
    assert flat == ["CHEM 101"]                   # other branch survives
    assert prereqs["CHEM 102"] == "(CHEM 101)"


def test_build_prereq_map_self_only_prereq_drops_to_flagged_no_constraint():
    # When the ONLY listed prereq is the course itself (raw-cased), every branch
    # dies to the self-ref drop -> '' with the structured flagged reason. This
    # proves gated_course is _norm'd first (lower-case 'bio 200' cancels the
    # 'BIO 200' literal) AND that a self-prereq never reaches the solver (a
    # self-prereq makes solve_cohort false-INFEASIBLE).
    records = [{"course_id": "bio 200", "dnf": [["BIO 200"]]}]
    prereqs, results = build_prereq_map(records)
    assert prereqs["BIO 200"] == ""
    assert results["BIO 200"].exact is False
    assert results["BIO 200"].fallback_reason == "self_referential_prereq_dropped"
    assert _groups(prereqs["BIO 200"]) == []


def test_build_prereq_map_uses_no_socket(fixture_records, monkeypatch):
    # No client is constructed anywhere in the map build (pure conversion).
    import httpx

    def _boom(*a, **k):  # pragma: no cover - only fires on a regression
        raise AssertionError("build_prereq_map opened a network client")

    monkeypatch.setattr(httpx, "Client", _boom)
    prereqs, results = build_prereq_map(fixture_records)
    assert prereqs["PHYS 102"] == "(MATH 245 OR PHYS 185) AND (MATH 246 OR PHYS 185)"


def test_build_prereq_map_forwards_max_clauses():
    # The configurable guard is forwarded to dnf_to_cnf: a tiny budget forces the
    # flagged conservative under-approximation (single OR-union), still labeled.
    records = [{"course_id": "P 1", "dnf": [["A", "B"], ["C", "D"], ["E", "F"]]}]
    prereqs, results = build_prereq_map(records, max_clauses=2)
    assert results["P 1"].exact is False
    assert "clause_budget_exceeded" in results["P 1"].fallback_reason
    # Union retains all literals; round-trips as a single OR-clause.
    assert _groups(prereqs["P 1"]) == [["A", "B", "C", "D", "E", "F"]]
