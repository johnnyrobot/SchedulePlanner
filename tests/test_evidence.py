"""Tests for evidence.py (F7 — Evidence-Cited Reporting & Chat Grounding).

The module holds ONLY the curated ✅ well-grounded research claims and maps this
build's already-computed structural flags (F1-F4 analysis keys + the engine's
time-block collisions) to the relevant citations. It is a PURE CONSUMER: it reads
results["analysis"][...] / results["ge_coverage"] and writes nothing.

The load-bearing test is ``test_anti_citation_guard`` — the honesty gate. The
HARD-EXCLUDED ❌/⚠️/❓ claims (CCD +40%, Austin Peay / Degree Maps +23%, UCF 45%,
Civitas 5-7%) must be IMPOSSIBLE to emit anywhere.
"""
import re

import chat_assist
import evidence
import report_export


# The ❌/⚠️/❓ claims that must NEVER appear in CLAIMS or any rendered F7 surface.
BANNED_TOKENS = [
    "+40%", "40%", "Denver",
    "Austin Peay", "APSU", "Degree Maps", "+23%", "23%",
    "UCF", "45%",
    "Civitas", "5-7%", "5–7%",
]

# Verbatim figures from the seven KEPT ✅ claims (roadmap lines 26-30).
KEPT_FIGURES = [
    "30%→43%", "9%→~18%", "23.7%→34.8%",     # Guided Pathways
    "+12", "13%", "26%→33%",                  # standardized blocks (Odessa / Kilgore)
    "57%",                                    # required course unavailable
    "2.3–2.8",                                # course shutout
    "5.41", "7.39",                           # conflict-aware tools
]


# --------------------------------------------------------------- step 1: constants
def test_all_claims_vetted():
    """Every curated claim is graded 'vetted' and fully populated — a future edit
    adding an ungraded / non-vetted claim must fail CI."""
    assert evidence.CLAIMS, "CLAIMS must be non-empty"
    for c in evidence.CLAIMS:
        assert c["grade"] == evidence.GRADE == "vetted"
        for field in ("id", "statement", "metric", "source", "supports"):
            assert c.get(field), f"claim {c.get('id')!r} missing {field}"
        assert isinstance(c["supports"], tuple) and c["supports"]


def test_kept_claims_present_verbatim():
    """All seven ✅ figures appear verbatim across the curated statements/metrics."""
    blob = " ".join(c["metric"] + " " + c["statement"] for c in evidence.CLAIMS)
    for fig in KEPT_FIGURES:
        assert fig in blob, f"kept figure {fig!r} missing from CLAIMS"


def test_excluded_claims_absent_from_module():
    """The HARD-EXCLUDED claims are not even present in the curated constant."""
    blob = str(evidence.CLAIMS)
    for tok in ("Denver", "Austin Peay", "APSU", "Degree Maps", "UCF", "Civitas"):
        assert tok not in blob, f"banned attribution {tok!r} leaked into CLAIMS"


# ------------------------------------------------- step 2: condition → citation map
def _bld_gap_results():
    return {"analysis": {"buildability": {"status": "active", "programs": [
        {"code": "BIOL", "title": "Biology", "missing": ["PHYSICS 6"],
         "time_conflict": {"feasible": True}}]}}}


def test_buildability_gap_surfaces_shutout_and_57():
    out = evidence.evidence_appendix(_bld_gap_results())
    assert out["status"] == "active"
    ids = {c["id"] for c in out["claims"]}
    assert "course_shutout" in ids
    assert "required_course_unavailable" in ids


def test_time_conflict_surfaces_conflict_aware():
    out = evidence.evidence_appendix({"analysis": {"time_block_collisions": [
        {"summary": "CHEM 101 & MATH 245 overlap"}]}})
    assert out["status"] == "active"
    assert "conflict_aware_tools" in {c["id"] for c in out["claims"]}


def test_bottleneck_and_demand_surface_availability_claims():
    for key in ("bottlenecks", "demand_supply"):
        block = {"status": "active"}
        block["leaderboard" if key == "bottlenecks" else "add_list"] = [{"course": "X"}]
        out = evidence.evidence_appendix({"analysis": {key: block}})
        ids = {c["id"] for c in out["claims"]}
        assert "required_course_unavailable" in ids, key
        assert "course_shutout" in ids, key


def test_grid_signals_surface_blocks():
    # off-grid conformance + morning-locked + mutual exclusions
    gp = {"status": "active",
          "conformance": {"on_grid_rate": 0.9},
          "morning_compression": {"morning_locked_count": 2},
          "mutual_exclusions": [{"courses": ["A", "B"]}]}
    out = evidence.evidence_appendix({"analysis": {"grid_pressure": gp}})
    ids = {c["id"] for c in out["claims"]}
    assert "standardized_blocks" in ids
    assert "conflict_aware_tools" in ids  # from mutual exclusivity


def test_ge_gap_surfaces_guided_pathways():
    bld = {"status": "active", "programs": [
        {"code": "B", "title": "B", "time_conflict": {"feasible": True},
         "ge": {"status": "active", "gaps": ["4"]}}]}
    out = evidence.evidence_appendix({"analysis": {"buildability": bld}})
    assert "guided_pathways" in {c["id"] for c in out["claims"]}


def _gateway_unschedulable_results():
    return {"analysis": {"gateway_momentum": {"status": "active",
        "english": {"identified": True, "course": "ENGL 101",
                    "schedulable_year1": True, "obstructions": []},
        "math": {"identified": True, "course": "MATH 227",
                 "schedulable_year1": False,
                 "obstructions": ["not offered in the analyzed schedule"]}}}}


def test_gateway_not_schedulable_surfaces_availability_claims():
    # A transfer-level gateway that cannot be scheduled in year 1 IS a required-
    # course availability problem -> the (vetted, no-new-number) availability claims.
    out = evidence.evidence_appendix(_gateway_unschedulable_results())
    assert out["status"] == "active"
    ids = {c["id"] for c in out["claims"]}
    assert "required_course_unavailable" in ids
    assert "course_shutout" in ids
    assert any(c["condition"] == "gateway_not_schedulable" for c in out["conditions"])


def test_gateway_schedulable_does_not_flag():
    # Both gateways schedulable -> no gateway condition fires.
    res = {"analysis": {"gateway_momentum": {"status": "active",
        "english": {"identified": True, "course": "E", "schedulable_year1": True},
        "math": {"identified": True, "course": "M", "schedulable_year1": True}}}}
    out = evidence.evidence_appendix(res)
    assert not any(c["condition"] == "gateway_not_schedulable"
                   for c in out.get("conditions", []))


def test_corequisite_not_co_offered_surfaces_availability_claim():
    res = {"analysis": {"corequisite_availability": {"status": "active",
        "english": {"identified": True, "course": "ENGL 101",
                    "has_corequisite": True, "co_offered_year1": True},
        "math": {"identified": True, "course": "MATH 150",
                 "has_corequisite": True, "co_offered_year1": False}}}}
    out = evidence.evidence_appendix(res)
    assert out["status"] == "active"
    assert "required_course_unavailable" in {c["id"] for c in out["claims"]}
    assert any(c["condition"] == "corequisite_not_co_offered" for c in out["conditions"])


def test_corequisite_co_offered_does_not_flag():
    res = {"analysis": {"corequisite_availability": {"status": "active",
        "english": {"identified": True, "course": "E", "has_corequisite": True,
                    "co_offered_year1": True},
        "math": {"identified": True, "course": "M", "has_corequisite": False}}}}
    out = evidence.evidence_appendix(res)
    assert not any(c["condition"] == "corequisite_not_co_offered"
                   for c in out.get("conditions", []))


def test_no_flags_default():
    """Clean build → inert, positive context only; problem-claims excluded."""
    out = evidence.evidence_appendix({"analysis": {
        "buildability": {"status": "inert", "reason": "no program"}}})
    assert out["status"] == "inert"
    assert out.get("reason")
    ids = {c["id"] for c in out["claims"]}
    assert ids == {"guided_pathways", "standardized_blocks"}
    # the problem-claims must NOT appear when nothing is flagged
    assert "course_shutout" not in ids
    assert "required_course_unavailable" not in ids
    assert "conflict_aware_tools" not in ids


def test_empty_results_inert_default():
    out = evidence.evidence_appendix({})
    assert out["status"] == "inert"
    assert {c["id"] for c in out["claims"]} == {"guided_pathways", "standardized_blocks"}


def test_relevant_only_not_full_dump():
    """Firing ONLY a time conflict must NOT pull in bottleneck-only availability
    claims that were not triggered — proves relevant-to-flags, not always-full."""
    out = evidence.evidence_appendix({"analysis": {"time_block_collisions": [
        {"summary": "x"}]}})
    ids = {c["id"] for c in out["claims"]}
    assert ids == {"conflict_aware_tools"}


def test_dedup_union():
    """Two conditions both mapping to course_shutout → it appears once."""
    res = {"analysis": {
        "bottlenecks": {"status": "active", "leaderboard": [{"course": "X"}]},
        "demand_supply": {"status": "active", "add_list": [{"course": "Y"}]}}}
    out = evidence.evidence_appendix(res)
    shutouts = [c for c in out["claims"] if c["id"] == "course_shutout"]
    assert len(shutouts) == 1


def test_conditions_carry_triggers():
    """Active envelope reports which conditions fired with a human trigger string."""
    out = evidence.evidence_appendix(_bld_gap_results())
    conds = {c["condition"] for c in out["conditions"]}
    assert "buildability_gap" in conds
    for c in out["conditions"]:
        assert c["trigger"]
        assert c["claims"]


# ------------------------------------------- step 3: anti-citation guard (the gate)
def _maximal_with_evidence():
    """A results dict firing EVERY F7 condition, with the evidence key populated
    exactly as the live post-pass does it."""
    res = {
        "campus": "LAMC", "live_terms": [2268],
        "program_info": {"title": "Biology AS-T", "award": "AS-T"},
        "programs": {"BIOLOGY": {"title": "Biology AS-T", "official_map_issues": [],
                                 "cohorts": {"full_time": {"terms_used": 4,
                                             "needs_fix": False,
                                             "plan": {1: ["BIOLOGY 3"]}, "fixes": []},
                                             "part_time": None}}},
        "analysis": {
            "buildability": {"status": "active", "horizon_terms": [2268],
                             "label": "Structural-feasibility PROXY.", "programs": [
                {"code": "B", "title": "Biology", "required_total": 4, "available": 3,
                 "missing": ["PHYSICS 6"], "single_section_required": ["BIOLOGY 3"],
                 "score": 47, "score_major_only": 55, "score_delta": -8,
                 "time_conflict": {"feasible": False},
                 "ge": {"status": "active", "areas_in_denominator": 2,
                        "areas_schedulable": 1, "gaps": ["4"], "draft": True}}]},
            "bottlenecks": {"status": "active", "leaderboard": [{"course": "MATH 227"}]},
            "demand_supply": {"status": "active", "add_list": [{"course": "MATH 227"}]},
            "grid_pressure": {"status": "active",
                              "conformance": {"on_grid_rate": 0.9},
                              "morning_compression": {"morning_locked_count": 2},
                              "mutual_exclusions": [{"courses": ["A", "B"]}]},
            "time_block_collisions": [{"summary": "x"}],
        },
    }
    res["analysis"]["evidence"] = evidence.evidence_appendix(res)
    return res


def _inert_with_evidence():
    res = {"programs": {"BIOLOGY": {"title": "Biology", "official_map_issues": [],
                        "cohorts": {"full_time": {"terms_used": 4, "needs_fix": False,
                                    "plan": {}, "fixes": []}, "part_time": None}}},
           "analysis": {"buildability": {"status": "inert", "reason": "no program"}}}
    res["analysis"]["evidence"] = evidence.evidence_appendix(res)
    return res


def test_anti_citation_guard():
    """THE honesty gate: no banned token may appear in CLAIMS, the appendix
    envelope, the rendered report, or the chat grounding — across active, inert,
    and no-flags states."""
    surfaces = []
    surfaces.append(("CLAIMS", str(evidence.CLAIMS)))
    for label, res in (("maximal", _maximal_with_evidence()),
                       ("inert", _inert_with_evidence()),
                       ("empty", {"analysis": {"evidence": evidence.evidence_appendix({})}})):
        surfaces.append((f"appendix:{label}", str(res["analysis"]["evidence"])))
        surfaces.append((f"report:{label}", report_export.render_report(res)))
        surfaces.append((f"context:{label}", chat_assist._context(res)))
    for tok in BANNED_TOKENS:
        for name, text in surfaces:
            assert tok not in text, f"banned token {tok!r} leaked into {name}"


def test_every_number_is_sourced():
    """Every digit-bearing figure F7 emits into the report's evidence section AND
    the chat grounding must be traceable to a CLAIMS metric/statement."""
    sourced = " ".join(c["metric"] + " " + c["statement"] for c in evidence.CLAIMS)
    # figures the curated claims legitimately contain (percentages / pp / decimals)
    fig_re = re.compile(r"\d[\d.,]*(?:%|pp|→|–|\.\d+)?")

    res = _maximal_with_evidence()
    report = report_export.render_report(res)
    ctx = chat_assist._context(res)

    # isolate just the F7 evidence section of the report
    marker = "Why this matters"
    assert marker in report
    f7_html = report[report.index(marker):]
    # cut at the next </section> close after the marker
    end = f7_html.index("</section>") + len("</section>")
    f7_html = f7_html[:end]
    # strip HTML numeric character references (e.g. &#x27; for an apostrophe) — they
    # are escaping noise, NOT research figures, and must not false-flag the guard.
    f7_html = re.sub(r"&#x?[0-9a-fA-F]+;", "", f7_html)

    # isolate just the F7 grounding block of the chat context
    gmarker = "WHY THIS MATTERS"
    assert gmarker in ctx
    f7_ctx = ctx[ctx.index(gmarker):]

    # Every digit-bearing token F7 emits must be a substring of the curated
    # metric/statement corpus — no hand-maintained skip list, so a future stray
    # number (a computed/fabricated figure) cannot slip through.
    for surface_name, surface in (("report-F7", f7_html), ("context-F7", f7_ctx)):
        for fig in fig_re.findall(surface):
            assert fig in sourced, (
                f"{surface_name} emitted figure {fig!r} not traceable to a CLAIMS field")


def test_no_flags_inert_chat_grounding_is_positive_only():
    """REALISTIC inert path: when analyze_live attaches a status:"inert" no-flags
    evidence block (the 2 positive claims), the chat grounding emits the caveated
    WHY-THIS-MATTERS block with guided_pathways + standardized_blocks ONLY — and
    NEVER leaks a problem-claim (shutout / 57% / +5.41pp persistence) into a build
    where nothing was flagged. The existing inert chat golden only exercises the
    no-evidence-key short-circuit, so this is the regression that would otherwise
    go uncaught."""
    res = _inert_with_evidence()
    assert res["analysis"]["evidence"]["status"] == "inert"
    ctx = chat_assist._context(res)

    # the caveated positive block IS grounded
    assert "WHY THIS MATTERS" in ctx
    assert "NOT a measurement or prediction of this campus" in ctx
    # both positive claims present (by their unambiguous attribution text)
    assert "Central Arizona" in ctx       # guided_pathways
    assert "Odessa" in ctx and "Kilgore" in ctx  # standardized_blocks

    # the no-flags surface must NOT contain ANY problem-claim text — a leak here
    # would mean the no-flags default started implying a problem that isn't present.
    f7_ctx = ctx[ctx.index("WHY THIS MATTERS"):]
    for problem in ("57%", "shutout", "5.41", "stop-out", "7.39"):
        assert problem not in f7_ctx, (
            f"problem-claim text {problem!r} leaked into the no-flags chat grounding")
