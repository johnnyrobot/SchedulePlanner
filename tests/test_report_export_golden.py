"""Full-output golden guardrail for report_export.render_report.

The rest of the report_export test suite asserts only substrings (`in` / `not in`).
That silently tolerates a whitespace / ordering / separator shift — exactly the
kind of change an upcoming "fold the <main> f-string into an append-only registry"
refactor could introduce. These tests pin the ENTIRE rendered document, byte for
byte, against a committed golden captured from the CURRENT (pre-refactor) code, so
the refactor can be proven byte-identical on every rendered surface.

Three fixtures collectively cover ordering, separators, skip, truncation, inert
and absent:
  * maximal  — every section active in one document (ordering + separators +
               truncation footnotes + active/inert detector mix);
  * absent   — programs only, no analysis/recon/detectors/ge/briefing (pins that
               absent sections contribute exactly "" and the seams are zero-width);
  * mixed    — some sections active, some inert, some absent (pins interleaving).

Regenerate intentionally with:  UPDATE_GOLDENS=1 python3 -m pytest \
    tests/test_report_export_golden.py
The default run asserts equality.
"""
import os
import pathlib

import evidence
import report_export

FIX = pathlib.Path(__file__).parent / "fixtures"

GEN = "2026-06-02 10:00"
BRIEFING = ("Two programs analyzed. Biology AS-T finishes full-time in 4 terms.\n"
            "MATH 227 is the top cross-program bottleneck — add a section.")


def _assert_golden(name: str, rendered: str):
    """Write-or-compare against a committed golden, byte for byte."""
    path = FIX / name
    if os.environ.get("UPDATE_GOLDENS"):
        path.write_text(rendered, encoding="utf-8")
    expected = path.read_text(encoding="utf-8")
    assert rendered == expected, (
        f"{name} drifted from the committed golden. If this change is "
        f"intentional, regenerate with UPDATE_GOLDENS=1.")


# --------------------------------------------------------------------- fixtures
def _maximal_results() -> dict:
    """Activates EVERY section of render_report in a single document: programs,
    supply diagnostics (with time-block collisions + off-grid), active
    buildability with an active F4 GE sub-block, active bottlenecks with both
    truncation footnotes, active grid_pressure with mutual exclusions +
    not_assessed + truncation, active demand_supply with add_list + capacity_slack
    + not_assessed + truncation, active equity_exposure (F6) with a collapsing
    archetype + a non-computable (online) archetype + truncation, reconciliation
    (with unmatched), inert_detectors (active + inert mix), and ge_coverage
    (requested, with draft + areas + shared).

    Every dict shape is reused from the existing per-feature tests — no invented
    field names."""
    return {
        "campus": "LAMC",
        "live_terms": [2268, 2272],
        "terms_in_data": 4,
        "program_info": {"title": "Biology AS-T", "award": "AS-T"},
        "programs": {
            "BIOLOGY": {
                "title": "Biology AS-T",
                "official_map_issues": ["BIOLOGY 3 mapped to term 1 but only offered Fall"],
                "cohorts": {
                    "full_time": {
                        "terms_used": 4, "needs_fix": True,
                        "plan": {1: ["BIOLOGY 3", "CHEM 101"], 2: ["MATH 261"]},
                        "fixes": [{"course": "PHYSICS 6", "season": "Spring"}],
                    },
                    "part_time": {
                        "terms_used": 6, "needs_fix": False,
                        "plan": {1: ["BIOLOGY 3"], 2: ["CHEM 101"], 3: ["MATH 261"]},
                        "fixes": [],
                    },
                },
            },
            "CHEM": {
                "title": "Chemistry AS-T",
                "official_map_issues": [],
                "cohorts": {
                    "full_time": {"terms_used": 4, "needs_fix": False,
                                  "plan": {1: ["CHEM 101"], 2: ["CHEM 102"]},
                                  "fixes": []},
                    "part_time": None,
                },
            },
        },
        "analysis": {
            "rotation_gaps": [{"course": "PHYSICS 6", "offered": 1, "of": 4}],
            "single_section": [{"course": "BIOLOGY 3"}],
            "modality_mismatch": [{"course": "ART 101", "fill_pct": 18}],
            "under_supply": [
                {"course": "MATH 227", "waitlisted": 22},
                {"course": "CHEM 101", "waitlisted": 0,
                 "sections_waitlisted": 2, "sections_total": 3},
            ],
            "time_block_collisions": [
                {"kind": "pair", "courses": ["CHEM 101", "MATH 245"],
                 "summary": "CHEM 101 & MATH 245 — every offered section overlaps"}],
            "off_grid_sections": [
                {"course": "MATH 245", "term": 2268,
                 "summary": "MATH 245 — starts 9:05 AM (off the 16-week grid)"}],
            "buildability": {
                "status": "active",
                "label": "Structural-feasibility PROXY, not a measured completion rate.",
                "ge_label": "GE-inclusive buildability — a structural-coverage PROXY ...",
                "horizon_terms": [2268, 2272],
                "programs": [{
                    "code": "BIOL-AS", "title": "Biology <AS-T>", "required_total": 4,
                    "available": 3, "missing": ["PHYSICS 6"],
                    "dead_requirements": ["GEOG 15"],
                    "single_section_required": ["BIOLOGY 3"],
                    "choice_groups": [{"options": ["ANTHRO 101", "ANTHRO 102"], "slack": -1}],
                    "season_mismatches": [{"course": "CHEM 101",
                                           "recommended_season": "Fall",
                                           "offered_seasons": ["Spring"]}],
                    "seat_pressure": [{"course": "MATH 261", "fill_pct": 98}],
                    "time_conflict": {"feasible": False,
                                      "pairwise_hard": [["BIOLOGY 3", "CHEM 101"]],
                                      "term_clashes": [{"recommended_semester": 2,
                                                        "courses": ["MATH 261", "CHEM 102"]}]},
                    "by_design_excluded": ["PE 100"],
                    "score": 47, "score_major_only": 55, "score_delta": -8,
                    "summary": "3/4 required courses offered; 1 missing; has time conflicts.",
                    "ge": {"status": "active", "areas_in_denominator": 2,
                           "areas_schedulable": 1, "gaps": ["4"], "draft": True},
                }],
            },
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
                "truncated": {"leaderboard": 3, "gaps": 7},
            },
            "grid_pressure": {
                "status": "active",
                "label": "Grid-conformance & morning-compression — a structural "
                         "time-block PROXY, not a measured completion rate.",
                "conformance": {"on_grid_rate": 0.9, "off_grid_sample": [],
                                "off_grid_truncated": 5, "evaluated": 10,
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
                "truncated": {"pairs": 4, "off_grid": 0},
            },
            "demand_supply": {
                "status": "active", "label": "Demand-vs-supply PROXY label",
                "add_list": [{"course": "MATH 227", "action_score": 1.3,
                              "demand_ratio": 1.55, "wait_total": 22, "n_sections": 2,
                              "reasons": ["fill 1.00", "22 waitlisted", "<b>x</b>"]}],
                "capacity_slack": [{"course": "ART 101", "fill": 0.14, "n_sections": 2,
                                    "note": "review only — not a cut recommendation"}],
                "sections_with_counts": 4, "program_weighted": True,
                "not_assessed": 1, "truncated": {"add_list": 3, "capacity_slack": 0},
            },
            "equity_exposure": {
                "status": "active", "label": "Archetype exposure PROXY label",
                "horizon_terms": [2268], "by_design_count": 0,
                "truncated": {"newly_unavailable": 2},
                "archetypes": [
                    {"key": "evening", "name": "Evening-only (start ≥ 5:00 PM)",
                     "computable": True, "sections_kept": 1, "sections_total": 3,
                     "programs": [{"code": "BIOL", "title": "Bio <AS>", "score": 48,
                                   "baseline_score": 71, "score_delta": -23,
                                   "collapsed": True,
                                   "newly_unavailable": ["CHEM <1>", "MATH 261"],
                                   "still_available": 1, "required_total": 3}]},
                    {"key": "online", "name": "Online-only", "computable": False,
                     "reason": "section modality (classType) is not present on the "
                               "imported records"},
                    {"key": "two_day", "name": "Two days a week (≤ 2 meeting days)",
                     "computable": True, "sections_kept": 2, "sections_total": 3,
                     "programs": [{"code": "BIOL", "title": "Bio <AS>", "score": 71,
                                   "baseline_score": 71, "score_delta": 0,
                                   "collapsed": False, "newly_unavailable": [],
                                   "still_available": 3, "required_total": 3}]},
                ],
            },
        },
        "reconciliation": {
            "matched_count": 6, "unmatched_count": 2,
            "unmatched": ["PHYSICS 6", "GEOG 15"],
        },
        "inert_detectors": [
            {"detector": "modality_mismatch", "status": "active",
             "label": "Capacity / fill-rate analysis from your enrollment export",
             "metric": "fill-rate computed on 4 of 6 sections",
             "matched_sections": 4, "total_sections": 6},
            {"detector": "prerequisite_ordering", "status": "active",
             "label": "Prerequisite ordering from eLumen",
             "metric": "applied on the program path",
             "prereq_summary": {"with_hard_prereq_count": 3, "fallback_count": 1}},
            {"detector": "time_block_conflict", "status": "inert",
             "reason": "some sections have no posted meeting time",
             "remedy": "supply day/time on every section"},
        ],
        "ge_coverage": {
            "requested": True, "pattern": "igetc", "assist_status": "ok",
            "assist_caveat": "GE areas are live from ASSIST; confirm with a counselor.",
            "draft_warning": "Draft — unverified: area mapping is a placeholder.",
            "areas": [
                {"area": "1A", "title": "English Composition", "required": 1,
                 "resolution": "concrete", "flags": []},
                {"area": "2A", "title": "Mathematical Concepts", "required": 1,
                 "resolution": "shared", "flags": []},
                {"area": "4", "title": "Social & Behavioral Sciences", "required": 2,
                 "resolution": "reserve", "flags": ["no_offering", "unknown_area"]},
            ],
            "shared_with_major": [
                {"area": "2A", "course": "MATH 261"},
                {"area": "5A", "course": "BIOLOGY 3"},
            ],
        },
    }


def _absent_results() -> dict:
    """Programs present, but NO analysis / reconciliation / inert_detectors /
    ge_coverage and no briefing. Pins that every absent section contributes
    exactly the empty string and the inter-section seams are zero-width."""
    return {
        "programs": {
            "BIOLOGY": {
                "title": "Biology AS-T",
                "official_map_issues": [],
                "cohorts": {
                    "full_time": {"terms_used": 4, "needs_fix": False,
                                  "plan": {1: ["BIOLOGY 3"], 2: ["CHEM 101"]},
                                  "fixes": []},
                    "part_time": None,
                },
            },
        },
    }


def _mixed_results() -> dict:
    """Some sections active, some inert, some absent — pins interleaving:
    programs (active), supply diagnostics (present but empty categories),
    buildability INERT, bottlenecks INERT, grid_pressure ABSENT,
    demand_supply ABSENT, reconciliation present (no unmatched), no ge_coverage."""
    return {
        "campus": "LAMC",
        "live_terms": [2268],
        "terms_in_data": 2,
        "program_info": {"title": "Biology", "award": ""},
        "programs": {
            "BIOLOGY": {
                "title": "Biology",
                "official_map_issues": [],
                "cohorts": {
                    "full_time": {"terms_used": 4, "needs_fix": False,
                                  "plan": {1: ["BIOLOGY 3"]}, "fixes": []},
                    "part_time": None,
                },
            },
        },
        "analysis": {
            "rotation_gaps": [], "single_section": [],
            "modality_mismatch": [], "under_supply": [],
            "buildability": {"status": "inert",
                             "label": "Structural-feasibility PROXY ...",
                             "reason": "no program / sections to audit"},
            "bottlenecks": {"status": "inert", "label": "... PROXY ...",
                            "reason": "no demand map supplied"},
        },
        "reconciliation": {"matched_count": 5, "unmatched_count": 0,
                           "unmatched": []},
        "inert_detectors": [
            {"detector": "time_block_conflict", "status": "inert",
             "reason": "some sections have no posted meeting time",
             "remedy": "supply day/time on every section"},
        ],
    }


def _inert_each_section_results() -> dict:
    """Every FEATURE block present in its inert (status != "active") branch in ONE
    document, so all four "Not computed: …" renderers fire together:
    buildability-inert, bottlenecks-inert, grid_pressure-inert AND
    demand_supply-inert. The mixed fixture only byte-pins the first two inert
    branches; grid_pressure-inert + demand_supply-inert are otherwise
    substring-only, and the registry refactor moves those inert renderers — this
    pins them."""
    return {
        "campus": "LAMC",
        "live_terms": [2268],
        "terms_in_data": 2,
        "program_info": {"title": "Biology", "award": ""},
        "programs": {
            "BIOLOGY": {
                "title": "Biology",
                "official_map_issues": [],
                "cohorts": {
                    "full_time": {"terms_used": 4, "needs_fix": False,
                                  "plan": {1: ["BIOLOGY 3"]}, "fixes": []},
                    "part_time": None,
                },
            },
        },
        "analysis": {
            "rotation_gaps": [], "single_section": [],
            "modality_mismatch": [], "under_supply": [],
            "buildability": {"status": "inert",
                             "label": "Structural-feasibility PROXY ...",
                             "reason": "no program / sections to audit"},
            "bottlenecks": {"status": "inert", "label": "... bottleneck PROXY ...",
                            "reason": "no program-lists demand map supplied"},
            "grid_pressure": {"status": "inert", "label": "... grid PROXY ...",
                              "reason": "no timed sections"},
            "demand_supply": {"status": "inert", "label": "... demand PROXY ...",
                              "reason": "no seat counts available"},
        },
    }


def _demand_empty_results() -> dict:
    """An ACTIVE demand_supply block whose `add_list` is empty, so the fallback
    string "No course currently shows add-a-section pressure." renders. That
    branch (report_export.py:522-523) had no test at all (only the `== ""` absent
    case) — this byte-pins it. capacity_slack is non-empty so the active envelope
    still has content to show."""
    return {
        "campus": "LAMC",
        "live_terms": [2268],
        "terms_in_data": 2,
        "program_info": {"title": "Biology", "award": ""},
        "programs": {
            "BIOLOGY": {
                "title": "Biology",
                "official_map_issues": [],
                "cohorts": {
                    "full_time": {"terms_used": 4, "needs_fix": False,
                                  "plan": {1: ["BIOLOGY 3"]}, "fixes": []},
                    "part_time": None,
                },
            },
        },
        "analysis": {
            "rotation_gaps": [], "single_section": [],
            "modality_mismatch": [], "under_supply": [],
            "demand_supply": {
                "status": "active", "label": "Demand-vs-supply PROXY label",
                "add_list": [],
                "capacity_slack": [{"course": "ART 101", "fill": 0.14,
                                    "n_sections": 2,
                                    "note": "review only — not a cut recommendation"}],
                "sections_with_counts": 2, "program_weighted": False,
                "not_assessed": 0, "truncated": {"add_list": 0, "capacity_slack": 0},
            },
        },
    }


# ------------------------------------------------------------------------ tests
def _with_evidence(results: dict) -> dict:
    """Attach the F7 evidence appendix exactly as the live post-pass does, so the
    golden pins the rendered evidence section the same way production builds it."""
    results.setdefault("analysis", {})["evidence"] = evidence.evidence_appendix(results)
    return results


def test_report_maximal_golden():
    # The maximal fixture fires every F7 condition → an ACTIVE evidence appendix.
    rendered = report_export.render_report(
        _with_evidence(_maximal_results()), briefing=BRIEFING, generated_at=GEN)
    _assert_golden("golden_report_maximal.html", rendered)


def test_report_absent_golden():
    rendered = report_export.render_report(_absent_results())
    _assert_golden("golden_report_absent.html", rendered)


def test_report_mixed_golden():
    rendered = report_export.render_report(_mixed_results(), generated_at=GEN)
    _assert_golden("golden_report_mixed.html", rendered)


def test_report_inert_each_section_golden():
    # No feature flag fires → an INERT evidence appendix (positive guided-pathways
    # context only), pinning the no-flags default rendering.
    rendered = report_export.render_report(_with_evidence(_inert_each_section_results()),
                                           generated_at=GEN)
    _assert_golden("golden_report_inert.html", rendered)


def test_report_demand_empty_golden():
    rendered = report_export.render_report(_demand_empty_results(),
                                           generated_at=GEN)
    _assert_golden("golden_report_demand_empty.html", rendered)
