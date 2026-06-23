"""Regression tests for the second code-review remediation batch (non-systemic
Major/Minor findings).

  * app.Api term parsing rejects invalid tokens instead of silently dropping them.
  * build_live_workbook.analyze_import enforces its NO-network contract: a transfer
    GE goal without a pre-fetched ASSIST map is rejected, never silently fetched.
  * equity_exposure._online_computable keys off modality PRESENCE, not truthiness.
  * chat_assist.chat tolerates results=None without raising.
"""
import pytest

import app
import build_live_workbook as blw
import chat_assist
import equity_exposure


# --------------------------------------------------- app: term-token rejection
def test_analyze_rejects_invalid_term_tokens():
    res = app.Api().fetch_live("LAMC", "2264,abc,2268", "Biology")
    assert "error" in res
    assert "abc" in res["error"]


def test_analyze_rejects_nonpositive_term_tokens():
    res = app.Api().fetch_live("LAMC", "0,-5", "Biology")
    assert "error" in res
    # both bad tokens are surfaced, not silently dropped
    assert "0" in res["error"] and "-5" in res["error"]


# ------------------------------------- analyze_import offline no-network guard
def test_analyze_import_rejects_network_transfer_goal(tmp_path):
    out = tmp_path / "imp.xlsx"
    with pytest.raises(ValueError, match="offline"):
        blw.analyze_import("files/lamc_schedule_sample.xlsx", str(out),
                           transfer_goal="csu")


def test_analyze_import_allows_local_goal_offline(tmp_path):
    # 'local' sources GE from the catalog PDF (no network) so it must NOT be
    # rejected by the guard (it may still no-op without a catalog, but never raise
    # the offline ValueError).
    out = tmp_path / "imp.xlsx"
    try:
        blw.analyze_import("files/lamc_schedule_sample.xlsx", str(out),
                           transfer_goal="local")
    except ValueError as exc:  # pragma: no cover - only on regression
        assert "offline" not in str(exc)


def test_analyze_import_accepts_injected_assist_areas(tmp_path):
    # A pre-fetched ASSIST map satisfies the contract -> no offline rejection.
    out = tmp_path / "imp.xlsx"
    report = blw.analyze_import(
        "files/lamc_schedule_sample.xlsx", str(out),
        transfer_goal="csu", assist_areas={"1A": ["ENGLISH 101"]})
    assert isinstance(report, dict)


# --------------------------------- equity_exposure: modality presence, not truthy
def test_online_computable_empty_modality_is_signal():
    # An empty-list modality still indicates the live path carries the field.
    assert equity_exposure._online_computable([{"course": "X 1", "modality": []}]) is True


def test_online_computable_absent_modality_no_room():
    assert equity_exposure._online_computable([{"course": "X 1"}]) is False


# ------------------------------------------- chat_assist: None-results tolerance
def test_chat_handles_none_results(monkeypatch):
    # A real question with results=None must not raise: the None->{} guard lets
    # _context/_defaults reach their "no analysis loaded" fallbacks. route() is the
    # only pre-model call, so neutralize it; the actual model call already degrades
    # gracefully in its own try/except.
    monkeypatch.setattr(chat_assist, "route", lambda *a, **k: {"lookup": "none"})
    res = chat_assist.chat("How many programs are there?", None)
    assert isinstance(res, dict)
    assert "answer" in res
