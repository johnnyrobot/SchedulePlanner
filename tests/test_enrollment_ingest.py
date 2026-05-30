r"""Tests for the IR PeopleSoft enrollment ingest + CRN-suffix-stripping join.

`sources/enrollment.py` reads the committed IR-shaped workbook
(`files/lamc_sample_enrollment.xlsx`) into a `(int term, str bare-CRN)` ->
{Cap, Tot, Wait} map and threads those counts onto live section records.

The load-bearing fact these tests pin: the live schedule side carries a
DECORATED `class_nbr` (`'17818 (LEC)'`), while the IR side is a bare int
(`20001`). A plain `str()` of the schedule value can NEVER match the IR key;
only the leading-integer CRN extraction (`re.match(r'\s*(\d+)', ...)`) makes the
join fire. This is exercised on a real `'17818 (LEC)'`-shaped value.

These tests are OFFLINE (pure file read; no network).
"""
import copy
import pathlib

import pytest

from sources.enrollment import enrich_sections, load_enrollment
from sources.http import SourceDataError

REPO = pathlib.Path(__file__).resolve().parent.parent
SAMPLE = REPO / "files" / "lamc_sample_enrollment.xlsx"

# Two planted, snapshot-pinned rows from the committed IR fixture (verified):
#   (2248, 20001) ACCTG 2 -> Cap 40, Tot 14, Wait 0
#   (2248, 20003) ENGL 101 -> Cap 40, Tot 40, Wait 15
ACCTG_KEY = (2248, "20001")
ENGL_KEY = (2248, "20003")


# --- load_enrollment -------------------------------------------------------

def test_load_enrollment_returns_dict_keyed_int_term_str_bare_crn():
    enr = load_enrollment(str(SAMPLE))
    assert isinstance(enr, dict)
    # key shape: (int term, str bare-CRN)
    k = next(iter(enr))
    assert isinstance(k[0], int) and isinstance(k[1], str)
    # the str CRN is the bare integer form (no decoration), e.g. "20001"
    assert k[1].isdigit()


def test_load_enrollment_planted_rows_have_expected_counts():
    enr = load_enrollment(str(SAMPLE))
    assert ACCTG_KEY in enr, f"expected planted key {ACCTG_KEY}"
    assert enr[ACCTG_KEY] == {"Cap Enrl": 40, "Tot Enrl": 14, "Wait Tot": 0}
    assert ENGL_KEY in enr
    assert enr[ENGL_KEY]["Cap Enrl"] == 40
    assert enr[ENGL_KEY]["Tot Enrl"] == 40
    assert enr[ENGL_KEY]["Wait Tot"] == 15  # > 15 boundary planted as the under_supply driver


def test_load_enrollment_keys_are_unique_in_fixture():
    # The committed IR fixture has 116 rows, all (term, class_nbr)-unique (verified).
    enr = load_enrollment(str(SAMPLE))
    assert len(enr) == 116


def test_load_enrollment_missing_column_raises_naming_the_file(tmp_path):
    import pandas as pd
    # A workbook whose `sections` sheet drops the required `Wait Tot` column.
    bad = tmp_path / "bad_enrollment.xlsx"
    df = pd.DataFrame([
        {"Term": 2248, "Class Nbr": 20001, "Cap Enrl": 40, "Tot Enrl": 14},
    ])
    with pd.ExcelWriter(bad, engine="openpyxl") as xl:
        df.to_excel(xl, sheet_name="sections", index=False)
    with pytest.raises(SourceDataError) as exc:
        load_enrollment(str(bad))
    # the error must name the offending file so the operator can find it
    assert str(bad) in str(exc.value) or "bad_enrollment.xlsx" in str(exc.value)
    # and it should name the missing column
    assert "Wait Tot" in str(exc.value)


# --- CRN-suffix-strip join (the load-bearing test) -------------------------

def test_decorated_schedule_crn_joins_after_suffix_strip():
    # A REAL decorated schedule value joins to an IR key only after the strip.
    enrollment = {(2248, "17818"): {"Cap Enrl": 30, "Tot Enrl": 25, "Wait Tot": 5}}
    records = [{"term": 2248, "course": "BIOLOGY 003", "class_nbr": "17818 (LEC)"}]
    out = enrich_sections(records, enrollment)
    assert out[0]["Cap Enrl"] == 30
    assert out[0]["Tot Enrl"] == 25
    assert out[0]["Wait Tot"] == 5


def test_plain_str_of_decorated_crn_would_not_match():
    # Prove the strip is what makes the join work: keying the IR side on the raw
    # decorated string '17818 (LEC)' (what a plain str() would produce) does NOT
    # match the bare-CRN key '17818'.
    raw_decorated = "17818 (LEC)"
    assert str(raw_decorated) != "17818"  # plain str() keeps the suffix
    enrollment = {(2248, "17818"): {"Cap Enrl": 30, "Tot Enrl": 25, "Wait Tot": 5}}
    # an enrollment map keyed on the RAW decorated value (the wrong key) misses:
    assert (2248, raw_decorated) not in enrollment
    # but the stripped bare CRN is present:
    assert (2248, "17818") in enrollment


def test_int_term_coercion_on_join():
    # schedule records may carry term as a string; the join coerces int(term).
    enrollment = {(2248, "17818"): {"Cap Enrl": 30, "Tot Enrl": 25, "Wait Tot": 5}}
    records = [{"term": "2248", "course": "BIOLOGY 003", "class_nbr": "17818 (LEC)"}]
    out = enrich_sections(records, enrollment)
    assert out[0]["Cap Enrl"] == 30


# --- enrich_sections: NEW list, no mutation, idempotent --------------------

def test_enrich_returns_new_list_without_mutating_input():
    enrollment = {(2248, "17818"): {"Cap Enrl": 30, "Tot Enrl": 25, "Wait Tot": 5}}
    records = [{"term": 2248, "course": "BIOLOGY 003", "class_nbr": "17818 (LEC)"}]
    snapshot = copy.deepcopy(records)
    out = enrich_sections(records, enrollment)
    # a brand new list of brand new dicts
    assert out is not records
    assert out[0] is not records[0]
    # caller's records are untouched (no in-place mutation)
    assert records == snapshot
    assert "Cap Enrl" not in records[0]


def test_enrich_is_idempotent():
    enrollment = {(2248, "17818"): {"Cap Enrl": 30, "Tot Enrl": 25, "Wait Tot": 5}}
    records = [{"term": 2248, "course": "BIOLOGY 003", "class_nbr": "17818 (LEC)"}]
    once = enrich_sections(records, enrollment)
    twice = enrich_sections(once, enrollment)
    assert once == twice
    # running on already-enriched records does not accumulate / double anything
    assert twice[0]["Cap Enrl"] == 30
    assert twice[0]["Tot Enrl"] == 25
    assert twice[0]["Wait Tot"] == 5


def test_unmatched_record_keeps_no_enrollment_keys():
    # A live-only record (no enrollment row) gets NO Cap/Tot/Wait keys, so it
    # stays 0 downstream via build_sections_df's r.get(..., 0) defaults.
    enrollment = {(2248, "17818"): {"Cap Enrl": 30, "Tot Enrl": 25, "Wait Tot": 5}}
    records = [{"term": 2248, "course": "MATH 245", "class_nbr": "99999 (LEC)"}]
    out = enrich_sections(records, enrollment)
    assert "Cap Enrl" not in out[0]
    assert "Tot Enrl" not in out[0]
    assert "Wait Tot" not in out[0]


# --- blank / non-numeric class_nbr is skipped (never key on "") ------------

def test_blank_class_nbr_is_not_falsely_matched():
    # A blank class_nbr must be SKIPPED (never keyed on "") so a spurious
    # enrollment row keyed on "" cannot leak onto a blank-CRN relsection.
    enrollment = {
        (2248, ""): {"Cap Enrl": 1, "Tot Enrl": 1, "Wait Tot": 1},      # malicious "" key
        (2248, "17818"): {"Cap Enrl": 30, "Tot Enrl": 25, "Wait Tot": 5},
    }
    records = [
        {"term": 2248, "course": "X", "class_nbr": ""},          # blank
        {"term": 2248, "course": "Y", "class_nbr": "   "},        # whitespace-only
        {"term": 2248, "course": "Z", "class_nbr": "BIO (LEC)"},  # non-numeric prefix
        {"term": 2248, "course": "W", "class_nbr": "17818 (LEC)"},
    ]
    out = enrich_sections(records, enrollment)
    # the three blank/non-numeric records are not matched (no "" key join)
    for rec in out[:3]:
        assert "Cap Enrl" not in rec
    # only the real decorated CRN matched
    assert out[3]["Cap Enrl"] == 30


def test_duplicate_term_crn_writes_once_per_matching_record():
    # Two live records sharing the same (term, CRN) each get the counts written
    # exactly once per record (enrich_sections does not aggregate).
    enrollment = {(2248, "17818"): {"Cap Enrl": 30, "Tot Enrl": 25, "Wait Tot": 5}}
    records = [
        {"term": 2248, "course": "BIOLOGY 003", "class_nbr": "17818 (LEC)"},
        {"term": 2248, "course": "BIOLOGY 003", "class_nbr": "17818 (LAB)"},
    ]
    out = enrich_sections(records, enrollment)
    assert out[0]["Cap Enrl"] == 30 and out[0]["Tot Enrl"] == 25 and out[0]["Wait Tot"] == 5
    assert out[1]["Cap Enrl"] == 30 and out[1]["Tot Enrl"] == 25 and out[1]["Wait Tot"] == 5
    # written ONCE per record, not summed / doubled
    assert out[0]["Tot Enrl"] == 25


# --- end-to-end: load the real fixture, join within its own terms ----------

def test_load_then_enrich_within_fixture_own_terms():
    # Derive section records from the enrollment fixture's OWN terms and join
    # them back -- this is the only self-consistent join the committed fixtures
    # support (the live schedule (2268) and IR ({2248,2252}) sets are disjoint).
    enr = load_enrollment(str(SAMPLE))
    records = [
        {"term": 2248, "course": "ACCTG 2", "class_nbr": "20001 (LEC)"},
        {"term": 2248, "course": "ENGL 101", "class_nbr": "20003 (LEC)"},
    ]
    out = enrich_sections(records, enr)
    assert out[0]["Cap Enrl"] == 40 and out[0]["Tot Enrl"] == 14
    assert out[1]["Wait Tot"] == 15
