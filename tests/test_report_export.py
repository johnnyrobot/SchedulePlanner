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


def test_report_renders_ge_inclusive_buildability():
    """An active GE block renders the GE-inclusive framing: major-only score, signed
    delta, GE areas schedulable, the gap, and the DRAFT caveat — all escaped."""
    results = {"analysis": {"buildability": {
        "status": "active", "label": "Structural-feasibility PROXY ...",
        "ge_label": "GE-inclusive buildability — a structural-coverage PROXY ...",
        "horizon_terms": [2268],
        "programs": [{
            "code": "BIOL", "title": "Biology AS-T", "required_total": 4, "available": 3,
            "missing": ["PHYSICS 6"], "dead_requirements": [], "single_section_required": [],
            "choice_groups": [], "season_mismatches": [], "seat_pressure": [],
            "by_design_excluded": [],
            "time_conflict": {"feasible": True, "pairwise_hard": [], "term_clashes": []},
            "score": 47, "score_major_only": 55, "score_delta": -8,
            "ge": {"status": "active", "areas_in_denominator": 2, "areas_schedulable": 1,
                   "gaps": ["4"], "draft": True},
        }]}}}
    doc = report_export.render_report(results)
    assert "GE-inclusive" in doc
    assert "major-only 55/100" in doc
    assert "1/2 GE areas schedulable" in doc
    assert "-8" in doc                  # the signed delta
    assert "DRAFT" in doc


def test_report_renders_bottlenecks_section():
    """An active bottlenecks block renders the Cross-program bottlenecks card:
    ranked rows, the gaps, the unmatched count, the PROXY label; data escaped."""
    results = {
        "terms_in_data": 1,
        "analysis": {
            "rotation_gaps": [], "single_section": [],
            "modality_mismatch": [], "under_supply": [],
            "bottlenecks": {
                "status": "active",
                "label": "Cross-program bottleneck ranking — a structural "
                         "supply-vs-demand PROXY, NOT a measured completion rate.",
                "leaderboard": [{
                    "course": "MATH <227>", "n_programs": 15, "n_listed": 18,
                    "programs": ["Biology AS-T", "Chemistry AS-T"],
                    "n_sections": 1, "min_sections_per_term": 1, "fill_pct": 96,
                    "closed": False, "is_lab": False, "risk_score": 19.5,
                    "reasons": ["required by 15 programs",
                                "single section in at least one offered term",
                                "at 96% fill"],
                }],
                "gaps": [{"course": "PHYSICS 6", "n_programs": 4,
                          "programs": ["Biology AS-T"]}],
                "unmatched_program_courses": 2,
                "truncated": {"leaderboard": 0, "gaps": 0},
            },
        },
        "programs": {},
    }
    doc = report_export.render_report(results)
    assert "Cross-program bottlenecks" in doc
    assert "MATH &lt;227&gt;" in doc                 # course id HTML-escaped
    assert "15" in doc and "19.5" in doc             # n_programs + risk score
    assert "required by 15 programs" in doc
    assert "PHYSICS 6" in doc                          # the gaps list
    assert "PROXY" in doc
    assert "2" in doc                                  # unmatched count surfaced


def test_report_omits_bottlenecks_when_absent():
    results = {"terms_in_data": 4,
               "analysis": {"rotation_gaps": [], "single_section": [],
                            "modality_mismatch": [], "under_supply": []},
               "programs": {}}
    doc = report_export.render_report(results)
    assert "Cross-program bottlenecks" not in doc


def test_report_bottlenecks_notes_both_truncations():
    """Neither the leaderboard NOR the gaps overflow is silently dropped — both
    'N more …' notes render (honesty doctrine: no silent truncation)."""
    results = {
        "terms_in_data": 1,
        "analysis": {
            "rotation_gaps": [], "single_section": [],
            "modality_mismatch": [], "under_supply": [],
            "bottlenecks": {
                "status": "active", "label": "… PROXY …",
                "leaderboard": [{"course": "MATH 227", "n_programs": 9,
                                 "n_sections": 1, "risk_score": 9.0,
                                 "reasons": ["required by 9 programs"]}],
                "gaps": [{"course": "PHYSICS 6", "n_programs": 4, "programs": []}],
                "unmatched_program_courses": 0,
                "truncated": {"leaderboard": 3, "gaps": 7},
            },
        },
        "programs": {},
    }
    doc = report_export.render_report(results)
    assert "3 more ranked course(s) beyond the top shown" in doc
    assert "7 more required-but-not-offered course(s) beyond those shown" in doc


def test_report_bottlenecks_inert_shows_reason():
    results = {"terms_in_data": 1,
               "analysis": {"rotation_gaps": [], "single_section": [],
                            "modality_mismatch": [], "under_supply": [],
                            "bottlenecks": {"status": "inert",
                                            "label": "... PROXY ...",
                                            "reason": "no demand map supplied"}},
               "programs": {}}
    doc = report_export.render_report(results)
    assert "Cross-program bottlenecks" in doc
    assert "no demand map supplied" in doc


def test_report_renders_grid_pressure_section():
    results = {
        "terms_in_data": 1,
        "analysis": {
            "rotation_gaps": [], "single_section": [],
            "modality_mismatch": [], "under_supply": [],
            "grid_pressure": {
                "status": "active",
                "label": "Grid-conformance & morning-compression — a structural "
                         "time-block PROXY, not a measured completion rate.",
                "conformance": {"on_grid_rate": 0.9, "off_grid_sample": [],
                                "off_grid_truncated": 0, "evaluated": 10,
                                "on_grid": 9, "off_grid": 1, "skipped": 0},
                "morning_compression": {"buckets": {"early": 1, "prime": 7,
                                        "afternoon": 1, "evening": 1},
                                        "total_timed": 10, "prime_share": 0.7,
                                        "morning_locked_count": 2},
                "mutual_exclusions": [{"courses": ["MATH <2>", "CHEM 1"],
                                       "reason": "both 9-1; overlap"}],
                "what_if_caveat": "feasibility is not verified",
                "not_assessed": {
                    "end_time_duration": {"status": "inert",
                                          "reason": "no contact category"},
                    "holidays_session_dates": {"status": "inert",
                                               "reason": "no calendar"}},
                "truncated": {"pairs": 0, "off_grid": 0},
            },
        },
        "programs": {},
    }
    doc = report_export.render_report(results)
    assert "Grid conformance" in doc
    assert "90%" in doc                          # on-grid rate
    assert "MATH &lt;2&gt;" in doc               # course id HTML-escaped
    assert "PROXY" in doc
    assert "no contact category" in doc          # not_assessed reason surfaced


def test_report_omits_grid_pressure_when_absent():
    results = {"terms_in_data": 4,
               "analysis": {"rotation_gaps": [], "single_section": [],
                            "modality_mismatch": [], "under_supply": []},
               "programs": {}}
    assert "Grid conformance" not in report_export.render_report(results)


def test_demand_supply_section_renders_and_escapes():
    results = {"analysis": {"demand_supply": {
        "status": "active", "label": "Demand-vs-supply PROXY label",
        "add_list": [{"course": "MATH 227", "action_score": 1.3, "demand_ratio": 1.55,
                      "wait_total": 22, "n_sections": 2,
                      "reasons": ["fill 1.00", "22 waitlisted", "<b>x</b>"]}],
        "capacity_slack": [{"course": "ART 101", "fill": 0.14, "n_sections": 2,
                            "note": "review only — not a cut recommendation"}],
        "sections_with_counts": 4, "program_weighted": True, "not_assessed": 1,
        "truncated": {"add_list": 3, "capacity_slack": 0}}}}
    html = report_export._demand_supply(results)
    assert "Demand-vs-supply action list" in html
    assert "MATH 227" in html and "ART 101" in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html   # escaped
    assert "not a cut" in html
    assert "1 required course" in html   # not_assessed footnote present
    assert "3 more add-list" in html


def test_demand_supply_section_empty_when_absent():
    assert report_export._demand_supply({"analysis": {}}) == ""


def test_demand_supply_section_inert_note():
    results = {"analysis": {"demand_supply": {
        "status": "inert", "label": "L", "reason": "no seat counts to assess"}}}
    html = report_export._demand_supply(results)
    assert "Not computed" in html and "no seat counts" in html


def _equity_block():
    return {"status": "active", "label": "Archetype exposure PROXY label",
            "horizon_terms": [2268], "by_design_count": 0,
            "truncated": {"newly_unavailable": 0},
            "archetypes": [
                {"key": "evening", "name": "Evening-only (start ≥ 5:00 PM)",
                 "computable": True, "sections_kept": 1, "sections_total": 3,
                 "programs": [{"code": "BIOL", "title": "Bio <AS>", "score": 48,
                               "baseline_score": 71, "score_delta": -23,
                               "collapsed": True,
                               "newly_unavailable": ["CHEM <1>", "MATH 261"],
                               "still_available": 1, "required_total": 3}]},
                {"key": "online", "name": "Online-only", "computable": False,
                 "reason": "section modality (classType) is not present"},
            ]}


def test_equity_exposure_section_renders_and_escapes():
    results = {"analysis": {"equity_exposure": _equity_block()}}
    html = report_export._equity_exposure(results)
    assert "Equity / archetype exposure" in html
    assert "Evening-only" in html
    assert "Bio &lt;AS&gt;" in html and "Bio <AS>" not in html       # escaped
    assert "CHEM &lt;1&gt;" in html                                   # course escaped
    assert "PROXY" in html
    assert "Not assessed" in html and "modality" in html             # online inert-archetype
    assert "-23" in html or "−23" in html or "23" in html            # signed delta


def test_equity_exposure_section_empty_when_absent():
    assert report_export._equity_exposure({"analysis": {}}) == ""


def test_equity_exposure_section_inert_note():
    results = {"analysis": {"equity_exposure": {
        "status": "inert", "label": "L",
        "reason": "the baseline buildability audit is itself inert"}}}
    html = report_export._equity_exposure(results)
    assert "Not computed" in html and "baseline" in html


def test_equity_exposure_truncation_footnote():
    blk = _equity_block()
    blk["truncated"]["newly_unavailable"] = 5
    html = report_export._equity_exposure({"analysis": {"equity_exposure": blk}})
    assert "5 more" in html


# ---------------------------------------------------- F8 gateway momentum render
def _gateway_block():
    """An active F8 block: English schedulable, Math obstructed (matches the real
    gateway_momentum_report shape)."""
    return {
        "status": "active",
        "label": "First-Year Gateway-Momentum: an OFFERING PROXY ... NOT a measured "
                 "completion rate.",
        "first_year_terms": ["2248", "2252"],
        "english": {"identified": True, "course": "ENGL <1>", "via": "ge_area_1A",
                    "transfer_level": "area-defined", "recommended_semester": 1,
                    "schedulable_year1": True, "sections_in_window": 2,
                    "obstructions": []},
        "math": {"identified": True, "course": "MATH 227", "via": "major_subject",
                 "transfer_level": "unverified", "recommended_semester": 1,
                 "schedulable_year1": False, "sections_in_window": 0,
                 "obstructions": ["not offered in the analyzed schedule"]},
        "both_gateways_year1": False,
        "not_assessed": [
            {"check": "placement_prerequisite_blocking", "status": "inert",
             "reason": "no placement data exists"},
            {"check": "student_completion", "status": "inert",
             "reason": "no student-level outcome exists"}],
    }


def test_gateway_momentum_section_renders_and_escapes():
    html = report_export._gateway_momentum({"analysis": {"gateway_momentum": _gateway_block()}})
    assert "gateway" in html.lower() and "momentum" in html.lower()
    assert "ENGL &lt;1&gt;" in html and "ENGL <1>" not in html          # escaped
    assert "MATH 227" in html
    assert "PROXY" in html                                              # caveat carried
    assert "not offered in the analyzed schedule" in html              # obstruction
    assert "placement" in html and "student completion" in html.replace("_", " ")  # not_assessed
    assert "unverified" in html                                        # transfer-level honesty


def test_gateway_momentum_section_empty_when_absent():
    assert report_export._gateway_momentum({"analysis": {}}) == ""


def test_gateway_momentum_section_inert_note():
    html = report_export._gateway_momentum({"analysis": {"gateway_momentum": {
        "status": "inert", "label": "L",
        "reason": "neither a transfer-level English nor Math gateway could be identified"}}})
    assert "Not computed" in html and "gateway" in html.lower()


# ------------------------------------------------ F9 corequisite co-availability
def _coreq_block():
    """An active F9 block: English coreq co-offered, Math coreq not offered."""
    return {
        "status": "active",
        "label": "Corequisite Co-Availability (AB1705): a co-OFFERING STRUCTURE proxy "
                 "... DIRECT PLACEMENT was the dominant lever ... NOT a measured "
                 "completion rate.",
        "first_year_terms": ["2248"],
        "english": {"identified": True, "course": "ENGL 101", "via": "ge_area_1A",
                    "transfer_level": "area-defined", "has_corequisite": True,
                    "corequisites": ["ENGL <101L>"],
                    "corequisite_detail": [{"course": "ENGL <101L>", "offered": True,
                                            "co_offered_terms": ["2248"],
                                            "co_offered_year1": True}],
                    "co_offered_year1": True, "all_corequisites_co_offered_year1": True,
                    "co_offered_terms": ["2248"], "obstructions": []},
        "math": {"identified": True, "course": "MATH 150", "via": "major_subject",
                 "transfer_level": "unverified", "has_corequisite": True,
                 "corequisites": ["MATH 150L"],
                 "corequisite_detail": [{"course": "MATH 150L", "offered": False,
                                         "co_offered_terms": [], "co_offered_year1": False}],
                 "co_offered_year1": False, "all_corequisites_co_offered_year1": False,
                 "co_offered_terms": [],
                 "obstructions": ["corequisite MATH 150L is not offered in the analyzed schedule"]},
        "both_gateways_coreq_co_offered_year1": False,
        "not_assessed": [
            {"check": "placement_prerequisite_blocking", "status": "inert",
             "reason": "no placement data exists"},
            {"check": "corequisite_enrollment_linkage", "status": "inert",
             "reason": "catalog co-offering is not registration linkage"},
            {"check": "student_completion_or_corequisite_effectiveness", "status": "inert",
             "reason": "no student-level outcome exists"}],
    }


def test_corequisite_availability_section_renders_and_escapes():
    html = report_export._corequisite_availability(
        {"analysis": {"corequisite_availability": _coreq_block()}})
    assert "corequisite" in html.lower()
    assert "ENGL &lt;101L&gt;" in html and "ENGL <101L>" not in html    # escaped coreq
    assert "MATH 150L" in html
    assert "STRUCTURE proxy" in html or "PROXY" in html                # caveat
    assert "DIRECT PLACEMENT" in html                                  # AB1705 honesty
    assert "not offered in the analyzed schedule" in html              # obstruction
    assert "linkage" in html                                           # not_assessed disclosed


def test_corequisite_availability_section_empty_when_absent():
    assert report_export._corequisite_availability({"analysis": {}}) == ""


def test_corequisite_availability_section_inert_note():
    html = report_export._corequisite_availability(
        {"analysis": {"corequisite_availability": {
            "status": "inert", "label": "L",
            "reason": "no corequisite linkage available",
            "remedy": "run with --elumen-live"}}})
    assert "Not computed" in html and "corequisite" in html.lower()


# ------------------------------------------------- E11 infeasibility render
def _infeasibility_block():
    return {
        "status": "active",
        "label": "Infeasibility Explainer: a deterministic STRUCTURAL re-solve ... "
                 "NOT a student outcome or a prediction.",
        "explained": [
            {"program": "Bio <AS>", "cohort": "Full-time", "horizon_terms": 4,
             "reproduced": True, "minimal_conflict_set": ["MATH <261>", "CHEM 101"],
             "background_only": False,
             "summary": "these 2 required course(s) cannot all be scheduled within "
                        "the 4-term full-time plan; relaxing any one restores feasibility"},
            {"program": "Bio <AS>", "cohort": "Part-time", "horizon_terms": 8,
             "reproduced": False,
             "note": "the planner found no feasible plan, but the structural explainer "
                     "could not reproduce it, so a minimal conflicting set is unavailable"},
        ],
        "not_assessed": [
            {"check": "season_mismatch_as_cause", "status": "inert",
             "reason": "season mismatches are treated as fixable"},
            {"check": "student_completion", "status": "inert",
             "reason": "no student-level outcome exists"}],
    }


def test_infeasibility_section_renders_and_escapes():
    html = report_export._infeasibility({"analysis": {"infeasibility": _infeasibility_block()}})
    assert "infeasib" in html.lower() or "unbuildable" in html.lower()
    assert "MATH &lt;261&gt;" in html and "MATH <261>" not in html   # escaped course
    assert "Full-time" in html and "Part-time" in html
    assert "relaxing any one restores feasibility" in html
    assert "could not reproduce" in html                              # not-reproduced note
    assert "season" in html.lower()                                   # not_assessed surfaced
    assert "STRUCTURAL" in html                                       # honesty label


def test_infeasibility_section_empty_when_absent():
    assert report_export._infeasibility({"analysis": {}}) == ""


def test_infeasibility_section_inert_note():
    html = report_export._infeasibility({"analysis": {"infeasibility": {
        "status": "inert", "label": "L",
        "reason": "every program cohort has a feasible plan to explain"}}})
    assert "Not computed" in html and "feasible" in html
