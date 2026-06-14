"""Tests for the data chatbot: chat_assist (router + lookups + answer) and the
Api.chat bridge.

The local model is never contacted — llm_assist._chat is monkeypatched (the same
seam test_llm_assist.py uses). Live lookups run offline through the committed
FakeClient fixtures (lamc_routes / make_client, + the ASSIST fixtures), proving
the route -> lookup -> answer chain deterministically with no network.
"""
import json
import pathlib

import pytest

import app
import chat_assist
import llm_assist
from sources import schedule

FIX = pathlib.Path(__file__).parent / "fixtures"


def _patch_chat(monkeypatch, fn):
    monkeypatch.setattr(llm_assist, "_chat", fn)


def test_context_includes_buildability_block():
    """_context grounds the model with the program-buildability audit so it can
    answer 'can a student finish the required path?' — with the honest framing."""
    results = {
        "analysis": {
            "buildability": {
                "status": "active", "horizon_terms": [2268],
                "label": "Structural-feasibility PROXY, not a measured completion rate.",
                "programs": [{
                    "code": "BIOL-AS", "title": "Biology AS-T",
                    "required_total": 4, "available": 3, "missing": ["PHYSICS 6"],
                    "single_section_required": ["BIOLOGY 3"],
                    "time_conflict": {"feasible": False}, "score": 62,
                }],
            },
        },
    }
    ctx = chat_assist._context(results)
    assert "PROGRAM BUILDABILITY" in ctx
    assert "PROXY" in ctx                       # honest framing travels with it
    assert "Biology AS-T (score 62/100)" in ctx
    assert "missing PHYSICS 6" in ctx
    assert "has time conflicts" in ctx


def test_context_includes_ge_inclusive_buildability():
    """When GE folds into the denominator, the chat context carries the GE-inclusive
    score, the major-only number, the signed delta, and the DRAFT marker."""
    results = {"analysis": {"buildability": {
        "status": "active", "label": "Structural-feasibility PROXY ...",
        "programs": [{
            "code": "BIOL", "title": "Biology AS-T", "required_total": 4, "available": 3,
            "missing": ["PHYSICS 6"], "time_conflict": {"feasible": True},
            "single_section_required": [], "score": 47, "score_major_only": 55,
            "score_delta": -8,
            "ge": {"status": "active", "areas_in_denominator": 2, "areas_schedulable": 1,
                   "gaps": ["4"], "draft": True},
        }]}}}
    ctx = chat_assist._context(results)
    assert "GE-inclusive" in ctx
    assert "major-only 55" in ctx
    assert "GE 1/2 areas schedulable" in ctx
    assert "DRAFT GE" in ctx


def test_context_omits_buildability_when_inert():
    ctx = chat_assist._context({"analysis": {"buildability": {"status": "inert",
                                                              "reason": "no program"}}})
    assert "PROGRAM BUILDABILITY" not in ctx


def test_context_includes_bottleneck_block():
    """_context grounds the model with the cross-program bottleneck leaderboard so
    it can answer 'which course is the biggest bottleneck?' — with honest framing."""
    results = {
        "analysis": {
            "bottlenecks": {
                "status": "active",
                "label": "Cross-program bottleneck ranking — supply-vs-demand PROXY.",
                "leaderboard": [
                    {"course": "MATH 227", "n_programs": 15, "n_sections": 1,
                     "risk_score": 19.5,
                     "reasons": ["required by 15 programs", "single section"]},
                ],
                "gaps": [{"course": "PHYSICS 6", "n_programs": 4}],
                "unmatched_program_courses": 2,
            },
        },
    }
    ctx = chat_assist._context(results)
    assert "CROSS-PROGRAM BOTTLENECKS" in ctx
    assert "PROXY" in ctx                       # honest framing travels with it
    assert "MATH 227" in ctx and "15 programs" in ctx
    assert "PHYSICS 6" in ctx                    # the not-offered gap surfaces


def test_context_omits_bottlenecks_when_inert():
    ctx = chat_assist._context({"analysis": {"bottlenecks": {"status": "inert",
                                                             "reason": "no demand map"}}})
    assert "CROSS-PROGRAM BOTTLENECKS" not in ctx


def test_context_bottleneck_surfaces_truncation():
    """The chat grounding slices to the top 8 of each list; the courses it leaves
    out (its own [:8] slice + the engine's cap overflow) are surfaced as honest
    counts, never silently dropped."""
    board = [{"course": f"C {i}", "risk_score": 1, "n_programs": 1, "n_sections": 1}
             for i in range(10)]
    gaps = [{"course": f"G {i}", "n_programs": 1} for i in range(9)]
    ctx = chat_assist._context({"analysis": {"bottlenecks": {
        "status": "active", "label": "supply-vs-demand PROXY",
        "leaderboard": board, "gaps": gaps,
        "truncated": {"leaderboard": 5, "gaps": 2}}}})
    assert "+7 more ranked bottleneck course(s) not shown" in ctx   # (10-8)+5
    assert "+3 more required-but-not-offered course(s) not shown" in ctx  # (9-8)+2


def test_context_includes_grid_pressure_block():
    results = {"analysis": {"grid_pressure": {
        "status": "active",
        "label": "Grid-conformance & morning-compression — a structural time-block "
                 "PROXY, not a measured completion rate.",
        "conformance": {"on_grid_rate": 0.9},
        "morning_compression": {"prime_share": 0.7, "morning_locked_count": 3},
        "mutual_exclusions": [{"courses": ["MATH 2", "CHEM 1"], "reason": "x"}],
        "truncated": {"pairs": 0}, "not_assessed": {}}}}
    ctx = chat_assist._context(results)
    assert "GRID CONFORMANCE" in ctx
    assert "PROXY" in ctx
    assert "MATH 2" in ctx and "CHEM 1" in ctx


def test_context_omits_grid_pressure_when_inert():
    ctx = chat_assist._context({"analysis": {"grid_pressure": {"status": "inert",
                                                               "reason": "x"}}})
    assert "GRID CONFORMANCE" not in ctx


def test_context_grid_pressure_surfaces_truncation():
    pairs = [{"courses": [f"A{i}", f"B{i}"], "reason": "x"} for i in range(7)]
    ctx = chat_assist._context({"analysis": {"grid_pressure": {
        "status": "active", "label": "PROXY",
        "conformance": {"on_grid_rate": 0.5},
        "morning_compression": {"prime_share": 0.5, "morning_locked_count": 1},
        "mutual_exclusions": pairs, "truncated": {"pairs": 4}, "not_assessed": {}}}})
    assert "+5 more mutually-exclusive pair(s) not shown" in ctx   # (7-6)+4


def test_context_includes_demand_supply_proxy_framing():
    results = {"analysis": {"demand_supply": {
        "status": "active", "label": "L",
        "add_list": [{"course": "MATH 227", "action_score": 1.3, "demand_ratio": 1.55,
                      "wait_total": 22, "n_sections": 2}],
        "capacity_slack": [{"course": "ART 101", "fill": 0.14, "n_sections": 2,
                            "note": "review only"}],
        "program_weighted": True, "not_assessed": 0,
        "truncated": {"add_list": 5, "capacity_slack": 0}}}}
    ctx = chat_assist._context(results)
    blob = "\n".join(ctx) if isinstance(ctx, list) else str(ctx)
    assert "DEMAND-VS-SUPPLY" in blob
    assert "PROXY" in blob and "MATH 227" in blob
    assert "+5 more" in blob                 # honest truncation, no silent drop


def test_context_omits_demand_supply_when_inert():
    results = {"analysis": {"demand_supply": {"status": "inert", "label": "L",
                                              "reason": "no seat counts"}}}
    ctx = chat_assist._context(results)
    blob = "\n".join(ctx) if isinstance(ctx, list) else str(ctx)
    assert "DEMAND-VS-SUPPLY" not in blob


def test_context_includes_equity_exposure_proxy_framing():
    results = {"analysis": {"equity_exposure": {
        "status": "active", "label": "L", "by_design_count": 0,
        "truncated": {"newly_unavailable": 0},
        "archetypes": [
            {"key": "evening", "name": "Evening-only (start ≥ 5:00 PM)",
             "computable": True, "sections_kept": 1, "sections_total": 3,
             "programs": [{"code": "BIOL", "title": "Bio", "score": 48,
                           "baseline_score": 71, "score_delta": -23,
                           "collapsed": True, "newly_unavailable": ["CHEM 1"]}]},
            {"key": "online", "name": "Online-only", "computable": False,
             "reason": "section modality not present on import"},
        ]}}}
    ctx = chat_assist._context(results)
    blob = "\n".join(ctx) if isinstance(ctx, list) else str(ctx)
    assert "EQUITY" in blob and "PROXY" in blob
    assert "Evening-only" in blob and "Bio" in blob and "CHEM 1" in blob
    assert "-23" in blob                          # signed delta carried
    assert "not assessed" in blob.lower()         # online non-computable surfaced


def test_context_omits_equity_exposure_when_inert():
    results = {"analysis": {"equity_exposure": {
        "status": "inert", "label": "L", "reason": "baseline inert"}}}
    ctx = chat_assist._context(results)
    blob = "\n".join(ctx) if isinstance(ctx, list) else str(ctx)
    assert "EQUITY" not in blob


def test_context_equity_exposure_surfaces_truncation():
    results = {"analysis": {"equity_exposure": {
        "status": "active", "label": "L", "by_design_count": 0,
        "truncated": {"newly_unavailable": 7},
        "archetypes": [
            {"key": "evening", "name": "Evening-only", "computable": True,
             "sections_kept": 0, "sections_total": 3,
             "programs": [{"code": "B", "title": "B", "score": 0,
                           "baseline_score": 71, "score_delta": -71, "collapsed": True,
                           "newly_unavailable": ["A 1"]}]}]}}}
    ctx = chat_assist._context(results)
    blob = "\n".join(ctx) if isinstance(ctx, list) else str(ctx)
    assert "7 more" in blob


# ------------------------------------------------ F8 gateway momentum grounding
def test_context_includes_gateway_momentum_proxy_framing():
    results = {"analysis": {"gateway_momentum": {
        "status": "active", "label": "L", "first_year_terms": ["2248", "2252"],
        "english": {"identified": True, "course": "ENGL 101", "via": "ge_area_1A",
                    "transfer_level": "area-defined", "schedulable_year1": True,
                    "obstructions": []},
        "math": {"identified": True, "course": "MATH 227", "via": "major_subject",
                 "transfer_level": "unverified", "schedulable_year1": False,
                 "obstructions": ["not offered in the analyzed schedule"]},
        "both_gateways_year1": False, "not_assessed": []}}}
    blob = "\n".join(chat_assist._context(results).split("\n"))
    assert "GATEWAY" in blob and "PROXY" in blob
    assert "ENGL 101" in blob and "MATH 227" in blob
    assert "not offered in the analyzed schedule" in blob   # obstruction carried
    assert "unverified" in blob                             # transfer-level honesty


def test_context_omits_gateway_momentum_when_inert():
    results = {"analysis": {"gateway_momentum": {
        "status": "inert", "label": "L", "reason": "no gateway identifiable"}}}
    assert "GATEWAY" not in chat_assist._context(results)


# ------------------------------------------ F9 corequisite co-availability grounding
def test_context_includes_corequisite_proxy_and_ab1705_framing():
    results = {"analysis": {"corequisite_availability": {
        "status": "active", "label": "L", "first_year_terms": ["2248"],
        "english": {"identified": True, "course": "ENGL 101", "via": "ge_area_1A",
                    "transfer_level": "area-defined", "has_corequisite": True,
                    "corequisites": ["ENGL 101L"], "co_offered_year1": True,
                    "co_offered_terms": ["2248"], "obstructions": []},
        "math": {"identified": True, "course": "MATH 150", "via": "major_subject",
                 "transfer_level": "unverified", "has_corequisite": True,
                 "corequisites": ["MATH 150L"], "co_offered_year1": False,
                 "co_offered_terms": [],
                 "obstructions": ["corequisite MATH 150L is not offered in the analyzed schedule"]},
        "both_gateways_coreq_co_offered_year1": False, "not_assessed": []}}}
    blob = chat_assist._context(results)
    assert "COREQUISITE" in blob and ("PROXY" in blob or "STRUCTURE" in blob)
    assert "DIRECT PLACEMENT" in blob                       # AB1705 causal caveat
    assert "ENGL 101L" in blob and "MATH 150L" in blob
    assert "not offered in the analyzed schedule" in blob   # obstruction carried


def test_context_omits_corequisite_when_inert():
    results = {"analysis": {"corequisite_availability": {
        "status": "inert", "label": "L", "reason": "no corequisite linkage"}}}
    assert "COREQUISITE" not in chat_assist._context(results)


# ------------------------------------------------------- E11 infeasibility grounding
def test_context_includes_infeasibility_structural_framing():
    results = {"analysis": {"infeasibility": {"status": "active", "label": "L",
        "explained": [{"program": "Bio", "cohort": "Full-time", "horizon_terms": 4,
                       "reproduced": True,
                       "minimal_conflict_set": ["MATH 261", "CHEM 101"],
                       "background_only": False,
                       "summary": "these 2 required course(s) cannot all be scheduled "
                                  "within the 4-term full-time plan; relaxing any one "
                                  "restores feasibility"}],
        "not_assessed": []}}}
    blob = chat_assist._context(results)
    assert "INFEASIB" in blob.upper() and "STRUCTURAL" in blob.upper()
    assert "Bio" in blob and "Full-time" in blob and "MATH 261" in blob
    assert "relaxing any one restores feasibility" in blob


def test_context_omits_infeasibility_when_inert():
    results = {"analysis": {"infeasibility": {"status": "inert", "reason": "all feasible"}}}
    assert "INFEASIB" not in chat_assist._context(results).upper()


# ------------------------------------------------------ E9 course-success grounding
def test_context_includes_demand_success_measured_framing():
    results = {"analysis": {"demand_success": {"status": "active", "label": "L",
        "granularity": "Course",
        "with_outcome": [{"course": "CHEM 101", "success_rate": 0.40,
                          "retention_rate": 0.70, "supply_constrained": True}],
        "escalated": [{"course": "CHEM 101", "success_rate": 0.40,
                       "retention_rate": 0.70, "supply_constrained": True}],
        "matched": 1, "offered_without_outcome": 0, "not_assessed": []}}}
    blob = chat_assist._context(results)
    assert "SUCCESS" in blob.upper() and "MEASURED" in blob.upper()
    assert "CHEM 101" in blob and "40%" in blob
    assert "co-occurrence" in blob.lower() or "not causal" in blob.lower()


def test_context_omits_demand_success_when_inert():
    results = {"analysis": {"demand_success": {"status": "inert", "reason": "no export"}}}
    assert "COURSE SUCCESS" not in chat_assist._context(results).upper()


# ------------------------------------------------------------------ router
def test_route_parses_offering_and_fills_defaults(monkeypatch):
    _patch_chat(monkeypatch, lambda *a, **k: '{"lookup":"offering","courses":["BIOLOGY 6"]}')
    intent = chat_assist.route("is bio 6 offered?", {"campus": "LAMC", "terms": [2268]})
    assert intent["lookup"] == "offering"
    assert intent["courses"] == ["BIOLOGY 6"]
    assert intent["campus"] == "LAMC" and intent["terms"] == [2268]


def test_route_strips_code_fences(monkeypatch):
    _patch_chat(monkeypatch, lambda *a, **k: '```json\n{"lookup":"program","program":"Chemistry"}\n```')
    intent = chat_assist.route("what does chem require", {"campus": "LAMC", "terms": [2268]})
    assert intent["lookup"] == "program" and intent["program"] == "Chemistry"


def test_route_malformed_json_is_none(monkeypatch):
    _patch_chat(monkeypatch, lambda *a, **k: "not json at all")
    assert chat_assist.route("hi", {"campus": "LAMC", "terms": [2268]})["lookup"] == "none"


def test_route_unknown_lookup_is_none(monkeypatch):
    _patch_chat(monkeypatch, lambda *a, **k: '{"lookup":"rm -rf","path":"/"}')
    assert chat_assist.route("x", {"campus": "LAMC", "terms": [2268]})["lookup"] == "none"


def test_route_offering_without_courses_is_none(monkeypatch):
    _patch_chat(monkeypatch, lambda *a, **k: '{"lookup":"offering"}')
    assert chat_assist.route("x", {"campus": "LAMC", "terms": [2268]})["lookup"] == "none"


def test_route_chat_exception_is_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no model")
    _patch_chat(monkeypatch, boom)
    assert chat_assist.route("x", {"campus": "LAMC", "terms": [2268]})["lookup"] == "none"


# ------------------------------------------------------------------ lookups
def test_run_lookup_offering_real_course(lamc_routes, make_client):
    course = schedule.fetch_sections("LAMC", [2268], client=make_client(lamc_routes))[0]["course"]
    label, facts = chat_assist.run_lookup(
        {"lookup": "offering", "campus": "LAMC", "terms": [2268], "courses": [course]},
        client=make_client(lamc_routes))
    assert course in facts and "section" in facts
    assert label.startswith("offering")


def test_run_lookup_offering_absent_course(lamc_routes, make_client):
    _label, facts = chat_assist.run_lookup(
        {"lookup": "offering", "campus": "LAMC", "terms": [2268], "courses": ["ZZZZ 999"]},
        client=make_client(lamc_routes))
    assert "No sections" in facts


def test_run_lookup_program(lamc_routes, make_client):
    label, facts = chat_assist.run_lookup(
        {"lookup": "program", "campus": "LAMC", "program": "Biology"},
        client=make_client(lamc_routes))
    assert "Biology" in facts and "required courses" in facts
    assert "program pathway" in label


def test_run_lookup_program_no_match(lamc_routes, make_client):
    _label, facts = chat_assist.run_lookup(
        {"lookup": "program", "campus": "LAMC", "program": "Underwater Basket Weaving"},
        client=make_client(lamc_routes))
    assert "No program matched" in facts


def test_run_lookup_ge(lamc_routes, make_client):
    routes = dict(lamc_routes)
    routes["/api/AcademicYears"] = json.loads((FIX / "assist_academic_years.json").read_text())
    routes["/api/transferability/courses"] = json.loads(
        (FIX / "assist_transferability_igetc_LAMC.json").read_text())
    label, facts = chat_assist.run_lookup(
        {"lookup": "ge", "campus": "LAMC", "goal": "igetc", "area": ""},
        client=make_client(routes))
    assert "Area" in facts
    assert "IGETC" in label


def test_run_lookup_prereqs(monkeypatch):
    monkeypatch.setattr(chat_assist.elumen_client, "fetch_prereq_records",
                        lambda *a, **k: ([{"course_id": "MATH 261", "raw": "MATH 260"}], set(), {}))
    label, facts = chat_assist.run_lookup(
        {"lookup": "prereqs", "campus": "LAMC", "courses": ["MATH 261"]})
    assert "MATH 261" in facts and "MATH 260" in facts
    assert "prerequisites" in label


def test_run_lookup_source_error_degrades(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("eLumen down")
    monkeypatch.setattr(chat_assist.elumen_client, "fetch_prereq_records", boom)
    label, facts = chat_assist.run_lookup(
        {"lookup": "prereqs", "campus": "LAMC", "courses": ["MATH 261"]})
    assert "failed" in facts.lower()
    assert label == "prereqs"


# ------------------------------------------------------------------ chat() e2e
def test_chat_routes_then_answers_with_lookup(lamc_routes, make_client, monkeypatch):
    calls = {"n": 0}

    def fake_chat(prompt, model=None, system=""):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"lookup":"program","program":"Biology"}'
        return "Biology requires several courses."
    monkeypatch.setattr(llm_assist, "_chat", fake_chat)
    results = {"campus": "LAMC", "live_terms": [2268], "programs": {}, "analysis": {}}
    r = chat_assist.chat("what does biology require?", results,
                         client=make_client(lamc_routes))
    assert r["answer"] == "Biology requires several courses."
    assert r["lookup"] and "program" in r["lookup"]
    assert calls["n"] == 2          # one route call + one answer call


def test_chat_none_path_makes_no_lookup(monkeypatch):
    seq = ['{"lookup":"none"}', "Term 2 has CHEM 101."]
    monkeypatch.setattr(llm_assist, "_chat", lambda *a, **k: seq.pop(0))
    r = chat_assist.chat("what's in term 2?", {"programs": {}})
    assert r["lookup"] is None
    assert "Term 2" in r["answer"]


def test_chat_model_error_degrades(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no ollama")
    monkeypatch.setattr(llm_assist, "_chat", boom)
    r = chat_assist.chat("hi", {"programs": {}})
    assert "couldn't reach" in r["answer"].lower()
    assert r["lookup"] is None


def test_chat_empty_question():
    r = chat_assist.chat("   ", {"programs": {}})
    assert "Ask a question" in r["answer"] and r["lookup"] is None


# ------------------------------------------------------------------ Api.chat
def test_api_chat_before_analysis_returns_guidance():
    r = app.Api().chat("hi")
    assert "Run an analysis first" in r["answer"]
    assert r["needs_model"] is False


def test_api_chat_empty_question():
    api = app.Api()
    api._last_results = {"programs": {}}
    r = api.chat("   ")
    assert "Ask a question" in r["answer"]


def test_api_chat_needs_model_when_unavailable(monkeypatch):
    api = app.Api()
    api._last_results = {"programs": {}}
    monkeypatch.setattr(app.llm_assist, "available", lambda *a, **k: False)
    r = api.chat("what's in term 2?")
    assert r["needs_model"] is True


def test_api_chat_happy_path(monkeypatch):
    api = app.Api()
    api._last_results = {"programs": {}}
    monkeypatch.setattr(app.llm_assist, "available", lambda *a, **k: True)
    monkeypatch.setattr(app.chat_assist, "chat",
                        lambda *a, **k: {"answer": "hello", "lookup": "offering · LAMC"})
    r = api.chat("hi")
    assert r["answer"] == "hello" and r["lookup"] == "offering · LAMC"
    assert r["needs_model"] is False


def test_api_chat_guards_exception(monkeypatch):
    api = app.Api()
    api._last_results = {"programs": {}}
    monkeypatch.setattr(app.llm_assist, "available", lambda *a, **k: True)

    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(app.chat_assist, "chat", boom)
    r = api.chat("hi")
    assert "error" in r and "kaboom" in r["error"]
