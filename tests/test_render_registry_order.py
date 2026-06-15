"""Render-registry order + cross-surface coverage pins (ship-review).

The 8 ANALYSIS_DETECTORS are order-pinned by test_analysis_detectors_registry.py,
but the LOAD-BEARING render order — chat_assist.GROUNDERS (the F7 evidence appendix
must CLOSE LAST so a finding is never grounded after the sources-research caveat)
and report_export.SECTION_RENDERERS — had no structural pin; only the byte goldens
caught a reorder, and the heterogeneous inline detectors had no order/coverage pin
at all (4-pillar ship review, Tests pillar).

These pins are fast and explicit:
  * evidence closes last on BOTH surfaces, build/programs opens first;
  * no duplicate registrations;
  * an exact ordered snapshot (update DELIBERATELY when adding a renderer); and
  * the NO-SILENT-DROP invariant across surfaces — every shared feature detector is
    rendered on BOTH report and chat, so a future edit cannot drop a finding from
    one surface while keeping it on the other.
"""
import chat_assist
import report_export


GROUNDERS_ORDER = [
    "_ground_build", "_ground_schedule_fetch", "_ground_term_plans",
    "_ground_ge_coverage", "_ground_reconciliation", "_ground_time_conflicts",
    "_ground_buildability", "_ground_bottlenecks", "_ground_demand_supply",
    "_ground_grid_pressure", "_ground_equity_exposure", "_ground_infeasibility",
    "_ground_gateway_momentum", "_ground_corequisite_availability",
    "_ground_demand_success", "_ground_equity_success_gap",
    "_ground_minimal_perturbation", "_ground_contact_hours", "_ground_room",
    "_ground_evidence",
]

RENDERERS_ORDER = [
    "_programs", "_diagnostics", "_buildability", "_bottlenecks", "_grid_pressure",
    "_demand_supply", "_equity_exposure", "_infeasibility", "_gateway_momentum",
    "_corequisite_availability", "_demand_success", "_equity_success_gap",
    "_minimal_perturbation", "_contact_hours", "_reconciliation", "_detectors",
    "_ge", "_evidence",
]

# Feature detectors that MUST surface on both the report and the chat (no surface
# may silently drop a finding the other shows).
SHARED_DETECTORS = [
    "buildability", "bottlenecks", "grid_pressure", "demand_supply",
    "equity_exposure", "infeasibility", "gateway_momentum",
    "corequisite_availability", "demand_success", "equity_success_gap",
    "minimal_perturbation", "contact_hours", "evidence",
]


def _names(reg):
    return [f.__name__ for f in reg]


def test_evidence_appendix_closes_last_on_both_surfaces():
    # The F7 honesty invariant: the sector-research evidence block is appended LAST
    # so nothing is ever grounded/rendered after its "other institutions" caveat.
    assert _names(chat_assist.GROUNDERS)[-1] == "_ground_evidence"
    assert _names(report_export.SECTION_RENDERERS)[-1] == "_evidence"


def test_grounders_open_and_render_order_is_pinned():
    names = _names(chat_assist.GROUNDERS)
    assert names[0] == "_ground_build"
    assert len(set(names)) == len(names), "a grounder is registered twice"
    assert names == GROUNDERS_ORDER, (
        "chat_assist.GROUNDERS order changed. If you ADDED a grounder, place it "
        "before _ground_evidence and update GROUNDERS_ORDER deliberately; a "
        "reorder/drop otherwise silently changes the grounding the model sees.")


def test_section_renderers_order_is_pinned():
    names = _names(report_export.SECTION_RENDERERS)
    assert names[0] == "_programs"
    assert len(set(names)) == len(names), "a section renderer is registered twice"
    assert names == RENDERERS_ORDER, (
        "report_export.SECTION_RENDERERS order changed. Update RENDERERS_ORDER "
        "deliberately when adding a section; a reorder/drop changes the report.")


def test_every_shared_detector_renders_on_both_report_and_chat():
    grounders = set(_names(chat_assist.GROUNDERS))
    renderers = set(_names(report_export.SECTION_RENDERERS))
    for key in SHARED_DETECTORS:
        assert f"_ground_{key}" in grounders, (
            f"chat surface has no grounder for {key!r} (no-silent-drop violation)")
        assert f"_{key}" in renderers, (
            f"report surface has no section renderer for {key!r} (no-silent-drop violation)")
