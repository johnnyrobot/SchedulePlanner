"""Fast structural pins for the ``ANALYSIS_DETECTORS`` registry.

This is the cheap unit guard that bites BEFORE the end-to-end byte-identity
determinism gate (``test_determinism_e2e`` / ``test_live_offline_pipeline``).
A future careless edit -- F6 appending in the wrong slot, reordering the
tuple, dropping the ``[program]`` list-wrap on the buildability compute, or
drifting an ``analysis_key`` -- fails HERE, instantly and with a clear message,
instead of only surfacing as a mismatched JSON blob in the e2e diff.

The registry order is load-bearing: the four entries map to ``inert_detectors``
elements [5..8] (after modality/prereq, ge_scheduling, time_block, room) and
the canonicalized ``results`` + ``inert_detectors`` output must stay
byte-identical, which means list/append ORDER is significant. Note also that
``analysis_key`` is intentionally DISTINCT from the detector key for F2
(``"bottlenecks"`` vs ``"program_bottleneck"``).
"""
from types import SimpleNamespace

import build_live_workbook
from build_live_workbook import ANALYSIS_DETECTORS

# The contract these tests pin, in registry order: (analysis_key, detector_key).
# analysis_key = where the block lands in results["analysis"]; detector_key =
# what the entry helper stamps into inert_detectors (distinct for F2).
EXPECTED = [
    ("buildability", "program_buildability"),
    ("bottlenecks", "program_bottleneck"),
    ("demand_supply", "demand_supply"),
    ("grid_pressure", "grid_pressure"),
    ("equity_exposure", "equity_exposure"),
]


def test_registry_has_exactly_five_entries_in_order():
    # Pins the slot order the inert_detectors [5..9] sequence + the determinism
    # gate depend on. Reordering or appending in the wrong slot fails here. F6
    # (equity_exposure) is APPENDED LAST — never reordering F1-F5.
    assert [d.analysis_key for d in ANALYSIS_DETECTORS] == [
        "buildability", "bottlenecks", "demand_supply", "grid_pressure",
        "equity_exposure"]
    assert len(ANALYSIS_DETECTORS) == 5


def test_each_entry_compute_and_entry_are_callable():
    for d in ANALYSIS_DETECTORS:
        assert callable(d.compute), f"{d.analysis_key}.compute not callable"
        assert callable(d.entry), f"{d.analysis_key}.entry not callable"


def test_each_entry_stamps_the_expected_detector_key():
    # Feed a minimal inert-shaped block through each entry helper and assert the
    # detector KEY it stamps. This pins the analysis_key="bottlenecks" vs
    # detector-key="program_bottleneck" distinction (and the other three).
    for d, (analysis_key, detector_key) in zip(ANALYSIS_DETECTORS, EXPECTED):
        assert d.analysis_key == analysis_key  # zip alignment sanity
        out = d.entry({"status": "inert"})
        assert out["detector"] == detector_key, (
            f"{analysis_key} entry stamped {out['detector']!r}, "
            f"expected {detector_key!r}")


def test_buildability_compute_wraps_program_in_a_list(monkeypatch):
    # Pins the [program] list-wrap on the F1+F4 compute: drop it and this bites.
    captured = {}

    def _fake_report(programs, sections, *, ge_coverage=None, active_courses=None,
                     by_design=None):
        captured["programs"] = programs
        captured["sections"] = sections
        captured["ge_coverage"] = ge_coverage
        captured["active_courses"] = active_courses
        captured["by_design"] = by_design
        return {"status": "inert"}

    monkeypatch.setattr(build_live_workbook.buildability,
                        "buildability_report", _fake_report)

    program = object()
    sections = object()
    ge_coverage = object()
    active_courses = object()
    by_design = object()
    ctx = SimpleNamespace(
        program=program, sections=sections, ge_coverage=ge_coverage,
        active_courses=active_courses, program_demand=None, facility=None,
        by_design=by_design)

    block = ANALYSIS_DETECTORS[0].compute(ctx)

    assert block == {"status": "inert"}
    # First positional arg is the program wrapped in a fresh list, not the bare
    # program object.
    assert isinstance(captured["programs"], list)
    assert captured["programs"] == [program]
    # And the rest of the call is threaded straight from the ctx.
    assert captured["sections"] is sections
    assert captured["ge_coverage"] is ge_coverage
    assert captured["active_courses"] is active_courses
    assert captured["by_design"] is by_design


def test_equity_exposure_compute_wraps_program_and_threads_by_design(monkeypatch):
    # Pins the F6 [program] list-wrap + by_design threading (the 5th entry).
    captured = {}

    def _fake_report(programs, sections, *, ge_coverage=None, active_courses=None,
                     by_design=None):
        captured["programs"] = programs
        captured["sections"] = sections
        captured["by_design"] = by_design
        return {"status": "inert"}

    monkeypatch.setattr(build_live_workbook.equity_exposure,
                        "equity_exposure_report", _fake_report)

    program, sections, by_design = object(), object(), object()
    ctx = SimpleNamespace(
        program=program, sections=sections, ge_coverage=None,
        active_courses=None, program_demand=None, facility=None,
        by_design=by_design)

    block = ANALYSIS_DETECTORS[4].compute(ctx)

    assert block == {"status": "inert"}
    assert captured["programs"] == [program]
    assert captured["sections"] is sections
    assert captured["by_design"] is by_design
