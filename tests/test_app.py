"""Headless tests for the pywebview desktop shell (app.py).

The pywebview window is only created inside main() under
`if __name__ == "__main__"`, so importing `app` and exercising `Api`
directly never opens a window — safe for CI and offline runs.

Covers the m2 "one-click demo" path: Api._demo_path() resolves the bundled
synthetic workbook, Api.load_demo() runs the same code path as a normal
analyze, and a non-workbook path surfaces a readable error dict instead of
raising.

Also covers the m4 "live LACCD data inside the UI" path: Api.fetch_live()
runs the full live pipeline through an injected FakeClient (replaying the
committed fixtures, no network) and returns the engine results merged with the
reconciliation + inert-detector fields the UI renders; a no-match program
surfaces a readable error dict instead of raising.
"""
import json
import os

import app
# json_response is a module-level helper in conftest (builds a FakeResponse with
# a chosen body), not a fixture — import it directly to forge a non-JSON body.
from conftest import json_response
# Reuse the self-contained offline eLumen route map (schedule + Program Mapper +
# eLumen, overlapping courses) from the live-eLumen pipeline test, so the
# app-level elumen_live flag test runs with no network and no duplicated fixtures.
from test_elumen_live_pipeline import _routes as _elumen_live_routes

# The committed IR enrollment export fixture (terms {2248, 2252}); term-disjoint
# from the schedule fixture (2268), so a real join matches 0 sections — the
# honest baseline that proves the upload path is wired without overclaiming.
_ENROLL_FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "files", "lamc_sample_enrollment.xlsx")

# The `lamc_routes` fixture (the shared live-fixture route map replaying the
# committed tests/fixtures/) lives in tests/conftest.py, shared with the
# live-pipeline tests so the route map and its identifiers live in one place.


def test_demo_path_points_at_bundled_workbook():
    p = app.Api()._demo_path()
    assert p.replace(os.sep, "/").endswith("files/lamc_data.xlsx")
    assert os.path.exists(p), f"bundled demo workbook missing at {p}"


def test_load_demo_returns_full_analysis():
    res = app.Api().load_demo()
    assert "error" not in res, res.get("error")
    assert res["terms_in_data"] == 8
    # all four supply-diagnostic detectors present
    assert set(res["analysis"]) == {
        "rotation_gaps", "single_section", "modality_mismatch", "under_supply",
    }
    # the four bundled AS-T programs
    assert set(res["programs"]) == {
        "AS-T-CSCI", "AS-T-BUS", "AS-T-BIOL", "AS-T-ENGR",
    }


def test_load_demo_uses_same_path_as_analyze():
    api = app.Api()
    via_analyze = api.analyze(api._demo_path())
    via_demo = api.load_demo()
    assert via_demo["terms_in_data"] == via_analyze["terms_in_data"]
    assert set(via_demo["programs"]) == set(via_analyze["programs"])


def test_analyze_non_workbook_returns_error_not_exception(tmp_path):
    bad = tmp_path / "not_a_workbook.txt"
    bad.write_text("this is plainly not an xlsx workbook")
    res = app.Api().analyze(str(bad))
    assert isinstance(res, dict)
    assert "error" in res
    assert isinstance(res["error"], str) and res["error"]


def test_analyze_missing_file_returns_error():
    res = app.Api().analyze("/no/such/file/anywhere.xlsx")
    assert res == {"error": "File not found."}


# ---- m4: live LACCD data inside the UI ------------------------------------

def test_fetch_live_returns_results_plus_reconciliation_and_inert(
        lamc_routes, make_client):
    """Api.fetch_live drives the full live pipeline through an injected
    FakeClient (no network) and returns the engine results merged with the
    reconciliation + inert-detector fields the UI renders."""
    client = make_client(lamc_routes)
    res = app.Api().fetch_live("LAMC", "2268", "Biology", client=client)

    assert "error" not in res, res.get("error")
    # whole payload is JSON-serializable (it is marshalled to JS)
    json.dumps(res)

    # engine results are present at the top level (so showResult() renders)
    assert res["terms_in_data"] >= 1
    assert set(res["analysis"]) == {
        "rotation_gaps", "single_section", "modality_mismatch", "under_supply"}
    assert "BIOLOGY" in res["programs"]
    # modality_mismatch stays inert (needs IR fill %); under_supply now fires
    # from the live schedule Waitlist status (breadth, no IR headcount).
    assert res["analysis"]["modality_mismatch"] == []
    assert res["analysis"]["under_supply"]
    assert all(r["waitlisted"] == 0 for r in res["analysis"]["under_supply"])

    # reconciliation surfaced for the live panel
    rec = res["reconciliation"]
    assert isinstance(rec["matched"], list) and rec["matched"]
    assert isinstance(rec["unmatched"], list)
    assert rec["matched_count"] == len(rec["matched"])
    assert rec["unmatched_count"] == len(rec["unmatched"])

    # inert-detector notes surfaced for the live panel
    inert = res["inert_detectors"]
    # under_supply is live-active now; ge_scheduling is always present (inert when
    # no transfer_goal is given).
    assert {d["detector"] for d in inert} == {
        "modality_mismatch", "prerequisite_ordering", "ge_scheduling"}
    for d in inert:
        if d["detector"] == "ge_scheduling":
            continue  # ge_scheduling carries "reason" but no "remedy"
        assert d["reason"]
        assert d["remedy"]


def test_fetch_live_parses_comma_terms(lamc_routes, make_client):
    """A comma-separated terms string is parsed into ints; with a single
    fixture term it still resolves the live chain."""
    client = make_client(lamc_routes)
    res = app.Api().fetch_live("LAMC", " 2268 ", "Biology", client=client)
    assert "error" not in res, res.get("error")
    assert res["terms_in_data"] >= 1


def test_fetch_live_no_match_returns_error(lamc_routes, make_client):
    client = make_client(lamc_routes)
    res = app.Api().fetch_live(
        "LAMC", "2268", "Underwater Basket Weaving", client=client)
    assert isinstance(res, dict)
    assert "error" in res
    assert isinstance(res["error"], str) and res["error"]
    assert "no program matched" in res["error"].lower()


def test_fetch_live_source_error_returns_error_not_exception(
        lamc_routes, make_client, error_resp):
    """A real source error surfaces a readable error dict rather than raising
    into the UI. We make the schedule listing endpoint (the first call in the
    chain) return HTTP 403, which drives the genuine
    get_json -> SourceHTTPError -> analyze_live -> fetch_live except path."""
    lamc_routes["/listing/LAMC/2268"] = error_resp(403)
    client = make_client(lamc_routes)
    res = app.Api().fetch_live("LAMC", "2268", "Biology", client=client)
    assert isinstance(res, dict)
    assert "error" in res
    assert isinstance(res["error"], str) and res["error"]
    assert "live" in res["error"].lower()  # the fetch_live wrapper message


def test_fetch_live_blank_or_nonpositive_terms_returns_error():
    """Blank, negative and zero term codes are all invalid (term codes are
    always positive) and must surface a readable error, not slip through into
    a live fetch."""
    for bad in ("   ", "-2268", "0", "-2268,0", "abc"):
        res = app.Api().fetch_live("LAMC", bad, "Biology")
        assert isinstance(res, dict)
        assert "error" in res, f"{bad!r} should be rejected"
        assert isinstance(res["error"], str) and res["error"]


# ---- m8-C t7: app.py error hardening --------------------------------------

def test_analyze_corrupt_xlsx_returns_error_not_exception(tmp_path):
    """A file that LOOKS like a workbook (.xlsx) but is corrupt/zero-byte must
    surface a readable error dict, not a traceback into the JS bridge."""
    bad = tmp_path / "corrupt.xlsx"
    bad.write_bytes(b"")  # zero-byte: not a valid zip/xlsx container
    res = app.Api().analyze(str(bad))
    assert isinstance(res, dict)
    assert "error" in res
    assert isinstance(res["error"], str) and res["error"]


def test_analyze_garbage_bytes_xlsx_returns_error(tmp_path):
    """A .xlsx with garbage (non-zip) bytes is corrupt the same way a truncated
    download would be: readable error, never an exception."""
    bad = tmp_path / "garbage.xlsx"
    bad.write_bytes(b"PK\x03\x04 not really a real zip central directory at all")
    res = app.Api().analyze(str(bad))
    assert isinstance(res, dict)
    assert "error" in res
    assert isinstance(res["error"], str) and res["error"]


def test_fetch_live_http_500_returns_error_not_exception(
        lamc_routes, make_client, error_resp):
    """A 5xx from a live endpoint (here the schedule listing, first call in the
    chain) drives get_json -> SourceHTTPError -> analyze_live -> fetch_live's
    except path and renders as a readable {error} dict, not an exception."""
    lamc_routes["/listing/LAMC/2268"] = error_resp(500)
    client = make_client(lamc_routes)
    res = app.Api().fetch_live("LAMC", "2268", "Biology", client=client)
    assert isinstance(res, dict)
    assert "error" in res
    assert isinstance(res["error"], str) and res["error"]
    assert "live" in res["error"].lower()


def test_fetch_live_non_json_body_returns_error_not_exception(
        lamc_routes, make_client):
    """A 200-OK endpoint that returns a non-JSON body (an HTML error page /
    truncated response) drives get_json -> SourceDataError and must render as a
    readable {error} dict through fetch_live's except path, not an exception."""
    lamc_routes["/listing/LAMC/2268"] = json_response(
        None, text="<html><body>502 Bad Gateway</body></html>")
    client = make_client(lamc_routes)
    res = app.Api().fetch_live("LAMC", "2268", "Biology", client=client)
    assert isinstance(res, dict)
    assert "error" in res
    assert isinstance(res["error"], str) and res["error"]
    assert "live" in res["error"].lower()


def test_explain_before_any_analyze_returns_readable_message():
    res = app.Api().explain()
    assert res == {"text": "Run an analysis first."}


def test_explain_partial_results_returns_readable_message_not_traceback():
    """A partial / None-cohort results dict (a program missing 'cohorts', a
    cohort missing 'terms_used', etc.) must yield readable text, not a
    traceback marshalled across the JS bridge."""
    api = app.Api()
    # Program present but cohorts is None and the keys the summary reads are
    # absent — exercises the AttributeError/KeyError path inside the templated
    # summary that the explain() guard must absorb.
    api._last_results = {
        "programs": {
            "AS-T-BROKEN": {
                "title": "Broken Program",
                "cohorts": None,                 # None-cohort
                "official_map_issues": [],
            },
            "AS-T-PARTIAL": {
                # 'title' missing entirely, cohort missing 'terms_used'
                "cohorts": {"full_time": {"needs_fix": True}},
                "official_map_issues": [],
            },
        }
    }
    res = api.explain()
    assert isinstance(res, dict)
    assert isinstance(res.get("text"), str) and res["text"]


def test_explain_empty_results_dict_returns_message():
    """An empty (but truthy-after-analyze) results dict must not raise; the
    templated summary just produces an empty body, which is fine."""
    api = app.Api()
    api._last_results = {"terms_in_data": 0}  # no 'programs' key
    res = api.explain()
    assert isinstance(res, dict)
    assert isinstance(res.get("text"), str)


def test_ai_status_error_path_returns_safe_dict(monkeypatch):
    """If a probe blows up (broken/absent Ollama), ai_status returns a safe
    'AI unavailable' dict with an error note, never an exception."""
    def boom():
        raise RuntimeError("ollama probe exploded")
    monkeypatch.setattr(app.llm_assist, "ollama_installed", boom)
    res = app.Api().ai_status()
    assert isinstance(res, dict)
    assert res["installed"] is False
    assert res["running"] is False
    assert res["model"] is False
    assert "error" in res and "ollama probe exploded" in res["error"]


def test_setup_ai_error_path_returns_ok_false_dict(monkeypatch):
    """A failed model pull (Ollama raising) returns {ok: False, error: ...},
    not an exception."""
    def boom(*a, **k):
        raise RuntimeError("pull exploded")
    monkeypatch.setattr(app.llm_assist, "ensure_model", boom)
    res = app.Api().setup_ai()
    assert isinstance(res, dict)
    assert res["ok"] is False
    assert "error" in res and "pull exploded" in res["error"]


def test_ai_status_happy_path_still_a_dict(monkeypatch):
    """The non-error path is unchanged: a plain status dict with no error key."""
    monkeypatch.setattr(app.llm_assist, "ollama_installed", lambda: False)
    monkeypatch.setattr(app.llm_assist, "ollama_running", lambda: False)
    monkeypatch.setattr(app.llm_assist, "model_present", lambda: False)
    res = app.Api().ai_status()
    assert res == {
        "installed": False, "running": False, "model": False,
        "model_name": app.llm_assist.MODEL,
    }


# ---- live-form optional enrichments: enrollment upload + eLumen toggle ------

def test_fetch_live_elumen_live_flag_threads_prereqs(make_client):
    """The new elumen_live=True bridge param flows fetch_live -> analyze_live and
    flips prerequisite_ordering ACTIVE (REAL eLumen), with the live prereq landing
    in the catalog. Driven by the offline eLumen route map (no network/socket)."""
    client = make_client(_elumen_live_routes())
    res = app.Api().fetch_live("LAMC", "2268", "Biology",
                               elumen_live=True, client=client)
    assert "error" not in res, res.get("error")
    det = next(d for d in res["inert_detectors"]
               if d["detector"] == "prerequisite_ordering")
    assert det["status"] == "active", det
    assert det.get("live") is True, det
    assert "REAL eLumen" in det.get("label", ""), det


def test_fetch_live_enrollment_path_threads_and_reports_join(
        lamc_routes, make_client):
    """A committed enrollment export passed via enrollment_path flows fetch_live
    -> analyze_live -> enrich_sections. The committed fixtures are term-disjoint
    (schedule 2268 vs enrollment {2248, 2252}), so the honest outcome is a
    0-section join that keeps capacity inert with an explicit matched/total count
    and a recorded source — proving the upload path is wired and reports the join
    rather than silently doing nothing."""
    client = make_client(lamc_routes)
    res = app.Api().fetch_live("LAMC", "2268", "Biology",
                               enrollment_path=_ENROLL_FIXTURE, client=client)
    assert "error" not in res, res.get("error")
    det = next(d for d in res["inert_detectors"]
               if d["detector"] == "modality_mismatch")
    assert det["status"] == "inert", det
    assert det.get("matched_sections") == 0, det
    assert det.get("total_sections", 0) >= 1, det
    assert det.get("source"), det  # the enrollment source is recorded honestly


def test_fetch_live_nonexistent_enrollment_returns_error():
    """A non-empty enrollment_path that does not exist is a readable error dict
    (caught before any network), not a load_enrollment traceback."""
    res = app.Api().fetch_live("LAMC", "2268", "Biology",
                               enrollment_path="/no/such/enrollment.xlsx")
    assert isinstance(res, dict)
    assert "error" in res
    assert "enrollment export not found" in res["error"].lower()


def test_fetch_live_blank_enrollment_path_is_ignored(lamc_routes, make_client):
    """An empty/whitespace enrollment_path (the UI's 'no file chosen' state) is
    treated as no enrollment — the bare live fetch still succeeds with the two
    baseline inert detectors, unchanged from the no-arg call."""
    client = make_client(lamc_routes)
    res = app.Api().fetch_live("LAMC", "2268", "Biology",
                               enrollment_path="   ", client=client)
    assert "error" not in res, res.get("error")
    assert {d["detector"] for d in res["inert_detectors"]} == {
        "modality_mismatch", "prerequisite_ordering", "ge_scheduling"}


# ---- Task 8: transfer_goal param + ge_coverage flattening ------------------

import json, pathlib
FIXX = pathlib.Path(__file__).parent / "fixtures"


def test_fetch_live_passes_transfer_goal_and_flattens_ge(lamc_routes, make_client):
    routes = dict(lamc_routes)
    routes["/api/AcademicYears"] = json.loads((FIXX / "assist_academic_years.json").read_text())
    routes["/api/transferability/courses"] = json.loads(
        (FIXX / "assist_transferability_igetc_LAMC.json").read_text())
    api = app.Api()
    out = api.fetch_live("LAMC", "2268", "Biology",
                         transfer_goal="igetc", client=make_client(routes))
    assert "error" not in out
    assert out["ge_coverage"]["requested"] is True


def test_fetch_live_none_goal_has_no_ge(lamc_routes, make_client):
    api = app.Api()
    out = api.fetch_live("LAMC", "2268", "Biology",
                         transfer_goal="none", client=make_client(lamc_routes))
    assert out.get("ge_coverage") is None


# --- Spec 2: local AA/AS GE from a catalog PDF (OpenDataLoader) -------------
_CATALOG_ODL = os.path.join(os.path.dirname(__file__), "fixtures",
                            "catalog_odl_sample.json")


def _load_catalog_odl():
    with open(_CATALOG_ODL, encoding="utf-8") as fh:
        return json.load(fh)


def test_preview_local_ge_no_file_returns_error():
    r = app.Api().preview_local_ge("")
    assert r["ok"] is False and "catalog PDF" in r["error"]


def test_preview_local_ge_needs_java(monkeypatch, tmp_path):
    f = tmp_path / "c.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(app.pdf_loader, "available", lambda: False)
    r = app.Api().preview_local_ge(str(f))
    assert r["needs_java"] is True and r["ok"] is False


def test_preview_local_ge_happy(monkeypatch, tmp_path):
    odl = _load_catalog_odl()
    f = tmp_path / "c.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(app.pdf_loader, "available", lambda: True)
    monkeypatch.setattr(app.pdf_loader, "extract", lambda _p: odl)
    r = app.Api().preview_local_ge(str(f))
    assert r["ok"] is True and r["section_found"] is True
    assert any(a["code"] == "A" for a in r["areas"])


def test_fetch_live_local_ge_uses_catalog(lamc_routes, make_client, monkeypatch):
    odl = _load_catalog_odl()
    # Patch the shared module's extract so no JVM/PDF is needed in CI.
    monkeypatch.setattr(app.pdf_loader, "extract", lambda _p: odl)
    out = app.Api().fetch_live("LAMC", "2268", "Biology", None, False, "local",
                               "/some/catalog.pdf", client=make_client(lamc_routes))
    assert "error" not in out
    assert out["ge_coverage"]["source"] == "catalog"
    assert out["ge_coverage"]["areas"]
    assert out["ge_coverage"]["draft_warning"]
