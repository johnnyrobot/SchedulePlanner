"""Tests for the self-contained HTML report exporter (report_export.py) and the
Api.export_report bridge method (app.py).

render_report is pure (results dict -> HTML string), so most assertions check the
emitted document directly. The live-panel case is driven through the same offline
FakeClient fixtures the rest of the live pipeline uses (no network). The WCAG
posture is verified structurally here (single h1, landmarks, skip link, table
scope, accessible controls, self-containment); full WCAG validation is done with
the browser/validator at review time.
"""
import json
import pathlib

import pytest

import app
import report_export

FIX = pathlib.Path(__file__).parent / "fixtures"


def _demo_results():
    """A real non-live results dict (engine.run over the bundled demo workbook)."""
    return app.Api().load_demo()


# --------------------------------------------------------------- structure / a11y
def test_render_report_demo_result_is_standalone_html():
    doc = report_export.render_report(_demo_results())
    assert doc.startswith("<!DOCTYPE html>")
    assert '<html lang="en"' in doc
    assert "<main" in doc and 'id="main"' in doc
    assert '<a class="skip" href="#main">' in doc
    # exactly one h1 (heading order: one page title, programs/sections are h2)
    assert doc.count("<h1") == 1
    # reader controls present and labelled
    assert 'id="decBtn"' in doc and 'id="incBtn"' in doc and 'id="resetBtn"' in doc
    assert 'aria-label="Decrease text size"' in doc
    assert 'id="themeBtn"' in doc and 'aria-pressed=' in doc
    # truly self-contained: no external resources of any kind
    assert "http://" not in doc and "https://" not in doc
    assert "<link " not in doc
    assert "src=" not in doc


def test_render_report_empty_programs_shows_no_programs():
    results = {"terms_in_data": 1,
               "analysis": {"rotation_gaps": [], "single_section": [],
                            "modality_mismatch": [], "under_supply": []},
               "programs": {}}
    doc = report_export.render_report(results)
    assert "No programs found" in doc


def test_render_report_escapes_untrusted_data():
    results = {"terms_in_data": 2,
               "analysis": {"rotation_gaps": [], "single_section": [],
                            "modality_mismatch": [], "under_supply": []},
               "programs": {"X": {"title": '<script>alert(1)</script>"&',
                                  "official_map_issues": [],
                                  "cohorts": {"full_time": None, "part_time": None}}}}
    doc = report_export.render_report(results)
    # the DATA-supplied script tag must never appear raw...
    assert "<script>alert(1)</script>" not in doc
    # ...it must be escaped instead
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in doc


# ------------------------------------------------------------------- live panels
def test_render_report_live_result_includes_panels(lamc_routes, make_client, tmp_path):
    routes = dict(lamc_routes)
    routes["/api/AcademicYears"] = json.loads((FIX / "assist_academic_years.json").read_text())
    routes["/api/transferability/courses"] = json.loads(
        (FIX / "assist_transferability_igetc_LAMC.json").read_text())
    client = make_client(routes)
    out = app.Api().fetch_live("LAMC", "2268", "Biology", None, False, "igetc",
                               client=client)
    assert "error" not in out
    assert out.get("ge_coverage")

    doc = report_export.render_report(out, briefing="Briefing test line.",
                                      generated_at="2026-06-02 10:00")
    assert "Biology" in doc
    assert "Live data reconciliation" in doc
    # GE coverage table is a real <table> with caption + column scope (1.3.1)
    assert "General Education" in doc
    assert "<caption" in doc
    assert 'scope="col"' in doc
    # briefing embedded
    assert "Admin briefing" in doc
    assert "Briefing test line." in doc
    # live metadata line
    assert "Live LACCD data" in doc and "LAMC" in doc


# --------------------------------------------------------------- export_report API
def test_export_report_writes_file(tmp_path):
    api = app.Api()
    api.load_demo()                      # populates _last_results
    out = tmp_path / "schedule-report.html"
    r = api.export_report(str(out))
    assert r.get("path") == str(out)
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert text.startswith("<!DOCTYPE html>")
    assert '<html lang="en"' in text


def test_export_report_before_analysis_returns_error(tmp_path):
    r = app.Api().export_report(str(tmp_path / "never.html"))
    assert "error" in r
    assert not (tmp_path / "never.html").exists()


def test_report_renders_time_conflicts_block():
    """A time_block_collisions finding in analysis renders in the Supply-diagnostics card."""
    results = {
        "terms_in_data": 4,
        "analysis": {
            "rotation_gaps": [], "single_section": [],
            "modality_mismatch": [], "under_supply": [],
            "time_block_collisions": [
                {"kind": "pair", "courses": ["CHEM 101", "MATH 245"],
                 "summary": "CHEM 101 & MATH 245 — every offered section overlaps"}],
        },
        "programs": {},
    }
    doc = report_export.render_report(results)
    assert "Time conflicts" in doc
    assert "every offered section overlaps" in doc
    assert "CHEM 101 &amp; MATH 245" in doc   # & is HTML-escaped


def test_report_renders_buildability_section():
    """An active buildability block renders a Program-buildability card with the
    score, summary, blocking reasons, and the honest PROXY label; data escaped."""
    results = {
        "terms_in_data": 1,
        "analysis": {
            "rotation_gaps": [], "single_section": [],
            "modality_mismatch": [], "under_supply": [],
            "buildability": {
                "status": "active",
                "label": "Structural-feasibility PROXY, not a measured completion rate.",
                "horizon_terms": [2268],
                "programs": [{
                    "code": "BIOL-AS", "title": "Biology <AS>", "required_total": 4,
                    "available": 3, "missing": ["PHYSICS 6"], "dead_requirements": [],
                    "single_section_required": ["BIOLOGY 3"],
                    "choice_groups": [], "season_mismatches": [], "seat_pressure": [],
                    "time_conflict": {"feasible": False,
                                      "pairwise_hard": [["BIOLOGY 3", "CHEM 101"]],
                                      "term_clashes": []},
                    "by_design_excluded": [], "score": 62,
                    "summary": "3/4 required courses offered; 1 missing; has time conflicts.",
                }],
            },
        },
        "programs": {},
    }
    doc = report_export.render_report(results)
    assert "Program buildability" in doc
    assert "score 62/100" in doc
    assert "Not offered: PHYSICS 6" in doc
    assert "Time conflict: BIOLOGY 3 &amp; CHEM 101" in doc
    assert "PROXY" in doc
    assert "Biology &lt;AS&gt;" in doc        # title is HTML-escaped


def test_report_omits_buildability_when_absent():
    """Demo / non-live results (no buildability key) render without the section."""
    results = {"terms_in_data": 4,
               "analysis": {"rotation_gaps": [], "single_section": [],
                            "modality_mismatch": [], "under_supply": []},
               "programs": {}}
    doc = report_export.render_report(results)
    assert "Program buildability" not in doc
