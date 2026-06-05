"""Full-output golden guardrail for chat_assist._context.

The rest of the chat_assist test suite asserts only substrings. That silently
tolerates a whitespace / ordering / separator shift in the `extra += [...]` chain
that an upcoming "fold the extra chain into an append-only registry" refactor
could introduce. These tests pin the ENTIRE grounding text, byte for byte, against
a committed golden captured from the CURRENT (pre-refactor) code.

Two fixtures:
  * maximal — activates BUILD meta, TERM-BY-TERM PLANS, GENERAL EDUCATION, LIVE
              RECONCILIATION, TIME CONFLICTS, PROGRAM BUILDABILITY (with F4
              GE-inclusive score_line), CROSS-PROGRAM BOTTLENECKS (with
              truncation), DEMAND-VS-SUPPLY (with truncation) and GRID
              CONFORMANCE (with truncation), all in one document — pins ordering,
              the blank-line separators between blocks, and the truncation lines.
  * inert   — programs present but nothing that activates any `extra` block and no
              BUILD meta. CRITICAL: _context returns the bare `base` with NO
              trailing newline in this case
              (`return base + ("\\n" + "\\n".join(extra) if extra else "")`).
              The golden captures exactly that, pinning the empty-extra
              short-circuit.

Regenerate intentionally with:  UPDATE_GOLDENS=1 python3 -m pytest \
    tests/test_chat_assist_golden.py
The default run asserts equality.
"""
import os
import pathlib

import chat_assist

FIX = pathlib.Path(__file__).parent / "fixtures"


def _assert_golden(name: str, rendered: str):
    path = FIX / name
    if os.environ.get("UPDATE_GOLDENS"):
        path.write_text(rendered, encoding="utf-8")
    expected = path.read_text(encoding="utf-8")
    assert rendered == expected, (
        f"{name} drifted from the committed golden. If this change is "
        f"intentional, regenerate with UPDATE_GOLDENS=1.")


# --------------------------------------------------------------------- fixtures
def _maximal_results() -> dict:
    """Every `extra` block of _context active in one document. Dict shapes are
    reused from the existing chat_assist / live-pipeline / feature tests — no
    invented field names. Lists are sized to also exercise the [:8]/[:6] slice +
    engine-cap truncation lines."""
    board = [{"course": f"C {i}", "risk_score": 1, "n_programs": 2, "n_sections": 1}
             for i in range(10)]
    gaps = [{"course": f"G {i}", "n_programs": 1} for i in range(9)]
    pairs = [{"courses": [f"A{i}", f"B{i}"], "reason": "both morning-locked"}
             for i in range(7)]
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
                        "plan": {1: ["BIOLOGY 3"], 2: ["CHEM 101"]},
                        "fixes": [],
                    },
                },
            },
        },
        "analysis": {
            "rotation_gaps": [{"course": "PHYSICS 6", "offered": 1, "of": 4}],
            "single_section": [{"course": "BIOLOGY 3"}],
            "modality_mismatch": [],
            "under_supply": [{"course": "MATH 227", "waitlisted": 0,
                              "sections_waitlisted": 2, "sections_total": 3}],
            "time_block_collisions": [
                {"kind": "pair", "courses": ["CHEM 101", "MATH 245"],
                 "summary": "CHEM 101 & MATH 245 — every offered section overlaps"}],
            "buildability": {
                "status": "active", "horizon_terms": [2268, 2272],
                "label": "Structural-feasibility PROXY, not a measured completion rate.",
                "programs": [{
                    "code": "BIOL", "title": "Biology AS-T", "required_total": 4,
                    "available": 3, "missing": ["PHYSICS 6"],
                    "time_conflict": {"feasible": True},
                    "single_section_required": ["BIOLOGY 3"],
                    "score": 47, "score_major_only": 55, "score_delta": -8,
                    "ge": {"status": "active", "areas_in_denominator": 2,
                           "areas_schedulable": 1, "gaps": ["4"], "draft": True},
                }],
            },
            "bottlenecks": {
                "status": "active",
                "label": "Cross-program bottleneck ranking — supply-vs-demand PROXY.",
                "leaderboard": board, "gaps": gaps,
                "unmatched_program_courses": 2,
                "truncated": {"leaderboard": 5, "gaps": 2},
            },
            "demand_supply": {
                "status": "active", "label": "Demand-vs-supply PROXY label",
                "add_list": [{"course": "MATH 227", "action_score": 1.3,
                              "demand_ratio": 1.55, "wait_total": 22, "n_sections": 2}],
                "capacity_slack": [{"course": "ART 101", "fill": 0.14,
                                    "n_sections": 2, "note": "review only"}],
                "program_weighted": True, "not_assessed": 1,
                "truncated": {"add_list": 5, "capacity_slack": 0},
            },
            "grid_pressure": {
                "status": "active",
                "label": "Grid-conformance & morning-compression — a structural "
                         "time-block PROXY, not a measured completion rate.",
                "conformance": {"on_grid_rate": 0.9},
                "morning_compression": {"prime_share": 0.7, "morning_locked_count": 2},
                "mutual_exclusions": pairs,
                "truncated": {"pairs": 4}, "not_assessed": {},
            },
            "equity_exposure": {
                "status": "active", "label": "Archetype exposure PROXY label",
                "by_design_count": 0, "truncated": {"newly_unavailable": 2},
                "archetypes": [
                    {"key": "evening", "name": "Evening-only (start ≥ 5:00 PM)",
                     "computable": True, "sections_kept": 1, "sections_total": 3,
                     "programs": [{"code": "BIOL", "title": "Biology AS-T",
                                   "score": 48, "baseline_score": 71,
                                   "score_delta": -23, "collapsed": True,
                                   "newly_unavailable": ["CHEM 101", "MATH 261"]}]},
                    {"key": "online", "name": "Online-only", "computable": False,
                     "reason": "section modality (classType) is not present on the "
                               "imported records"},
                    {"key": "two_day", "name": "Two days a week (≤ 2 meeting days)",
                     "computable": True, "sections_kept": 2, "sections_total": 3,
                     "programs": [{"code": "BIOL", "title": "Biology AS-T",
                                   "score": 71, "baseline_score": 71,
                                   "score_delta": 0, "collapsed": False,
                                   "newly_unavailable": []}]},
                ],
            },
        },
        "reconciliation": {
            "matched_count": 6, "unmatched_count": 2,
            "unmatched": ["PHYSICS 6", "GEOG 15"],
        },
        "ge_coverage": {
            "requested": True, "pattern": "igetc", "assist_status": "ok",
            "areas": [
                {"area": "1A", "title": "English Composition", "required": 1,
                 "resolution": "concrete"},
                {"area": "2A", "title": "Mathematical Concepts", "required": 1,
                 "resolution": "shared"},
            ],
            "shared_with_major": [
                {"area": "2A", "course": "MATH 261"},
                {"area": "5A", "course": "BIOLOGY 3"},
            ],
        },
    }


def _inert_results() -> dict:
    """Programs present (so `base` is the rich template summary) but nothing that
    activates any `extra` block and NO BUILD meta (no campus / live_terms /
    program_info). Pins the empty-extra short-circuit: _context returns bare
    `base` with no trailing newline.

    The cohorts carry `terms_used` (so the base summary still describes timing)
    but an EMPTY `plan`, so the TERM-BY-TERM PLANS extra block — which only fires
    on a truthy `plan` — stays silent and `extra` ends up empty."""
    return {
        "terms_in_data": 2,
        "programs": {
            "BIOLOGY": {
                "title": "Biology AS-T",
                "official_map_issues": [],
                "cohorts": {
                    "full_time": {"terms_used": 4, "needs_fix": False,
                                  "plan": {}, "fixes": []},
                    "part_time": None,
                },
            },
        },
        "analysis": {
            "rotation_gaps": [], "single_section": [],
            "modality_mismatch": [], "under_supply": [],
            # inert blocks must NOT activate any extra:
            "buildability": {"status": "inert", "reason": "no program"},
            "bottlenecks": {"status": "inert", "reason": "no demand map"},
            "grid_pressure": {"status": "inert", "reason": "no timed sections"},
            "demand_supply": {"status": "inert", "reason": "no seat counts"},
        },
    }


# ------------------------------------------------------------------------ tests
def test_context_maximal_golden():
    rendered = chat_assist._context(_maximal_results())
    _assert_golden("golden_context_maximal.txt", rendered)


def test_context_inert_golden():
    rendered = chat_assist._context(_inert_results())
    _assert_golden("golden_context_inert.txt", rendered)


def test_context_inert_has_no_trailing_newline_beyond_base():
    """The empty-extra short-circuit returns bare `base`: no trailing newline is
    appended. Pin it explicitly (the golden file would otherwise be ambiguous
    about a single trailing byte)."""
    rendered = chat_assist._context(_inert_results())
    # base is llm_assist._template_summary, which joins lines with "\n" and does
    # not end in a newline; _context must not add one when extra is empty.
    assert not rendered.endswith("\n")
