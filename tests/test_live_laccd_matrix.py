"""Non-gating live E2E matrix for the desktop "Build from live LACCD data" path.

These tests intentionally hit public LACCD / eLumen / ASSIST endpoints and are
therefore marked ``live``. They are deselected by default; run with:

    python3 -m pytest -m live tests/test_live_laccd_matrix.py
"""
import json

import pytest

import app
import report_export


LIVE_CASES = [
    pytest.param("LAMC", [2264, 2266, 2268], "Biology", "igetc",
                 id="lamc-biology-igetc"),
    pytest.param("LACC", [2264, 2266, 2268], "Psychology", "cal-getc",
                 id="lacc-psychology-cal-getc"),
    pytest.param("ELAC", [2268], "Business Administration", "csu-ge",
                 id="elac-business-admin-csu-ge"),
    pytest.param("LATTC", [2266, 2268], "Administration of Justice", "igetc",
                 id="lattc-admin-justice-igetc"),
]


@pytest.mark.live
@pytest.mark.parametrize("campus,terms,program,ge_goal", LIVE_CASES)
def test_live_laccd_matrix(campus, terms, program, ge_goal):
    """Exercise the real UI bridge contract with GE + eLumen, no enrollment export."""
    try:
        res = app.Api().fetch_live(
            campus, ",".join(str(t) for t in terms), program,
            enrollment_path=None, elumen_live=True, transfer_goal=ge_goal)
    except Exception as exc:  # pragma: no cover - only for live canary clarity
        pytest.fail(
            f"live upstream/API drift while fetching {campus} {program}: "
            f"{type(exc).__name__}: {exc}")

    if "error" in res:
        pytest.fail(
            f"live upstream/API drift for {campus} {program} ({ge_goal}): "
            f"{res['error']}")

    json.dumps(res)
    assert res.get("section_count", 0) > 0
    assert res.get("program_info") and res["program_info"].get("title")
    assert res.get("reconciliation")
    assert res["reconciliation"]["matched_count"] >= 0
    assert res.get("programs")
    assert res.get("analysis")

    ge = res.get("ge_coverage")
    assert ge and ge["requested"] is True
    assert ge["pattern"] == ge_goal
    assert ge.get("areas")
    assert ge.get("reviewed") is False
    assert ge.get("draft_warning")

    prereq = next(d for d in res["inert_detectors"]
                  if d["detector"] == "prerequisite_ordering")
    if prereq.get("status") == "active":
        summary = prereq.get("prereq_summary") or {}
        applied = (summary.get("with_hard_prereq_count", 0)
                   + summary.get("fallback_count", 0))
        assert applied >= 1
    else:
        assert prereq.get("reason") or prereq.get("label")

    capacity = next(d for d in res["inert_detectors"]
                    if d["detector"] == "modality_mismatch")
    assert capacity["status"] == "inert"
    assert "enrollment/capacity counts" in capacity["reason"]
    assert res["analysis"]["modality_mismatch"] == []
    assert "under_supply" in res["analysis"]

    html = report_export.render_report(res)
    assert "General Education" in html
    assert "Draft" in html
    assert "Capacity / fill-rate" in html
    assert "not yet measurable" in html
    assert "not all clear" in html
