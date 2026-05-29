"""Headless tests for the pywebview desktop shell (app.py).

The pywebview window is only created inside main() under
`if __name__ == "__main__"`, so importing `app` and exercising `Api`
directly never opens a window — safe for CI and offline runs.

Covers the m2 "one-click demo" path: Api.demo_path() resolves the bundled
synthetic workbook, Api.load_demo() runs the same code path as a normal
analyze, and a non-workbook path surfaces a readable error dict instead of
raising.
"""
import os

import app


def test_demo_path_points_at_bundled_workbook():
    p = app.Api().demo_path()
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
    via_analyze = api.analyze(api.demo_path())
    via_demo = app.Api().load_demo()
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
