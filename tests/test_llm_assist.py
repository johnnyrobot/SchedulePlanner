"""Offline tests for the optional Gemma/Ollama layer (llm_assist.py).

Milestone m6 — "AI verification, Ollama/model-tag correction, graceful fallback".

These tests are FULLY OFFLINE: every Ollama HTTP call (the module talks to a
local Ollama server over urllib, NOT httpx) is monkeypatched so the default
`pytest -q` suite never needs a running Ollama or any model pulled. They lock
both branches of the AI surface:

  * make_prereq_parser() — present (callable, parses) vs absent (None)
  * explain()            — success JSON, malformed output, timeout/exception
                           all degrade to the templated fallback
  * the templated fallback names every program even with AI forced off
  * onboarding logic (F19-F22): ai_status fields, ensure_model present/absent,
    model_present tag-exact matching, available() composition

The single live smoke test is marked `live` (deselected by default) and only
exercises the real machine if Ollama happens to be up.
"""
import json
import urllib.error

import pytest

import llm_assist


# --------------------------------------------------------------------------- #
# Fixtures: a realistic engine `results` dict and Ollama /api/tags payloads.   #
# --------------------------------------------------------------------------- #
@pytest.fixture
def results():
    """Minimal results matching engine.run() shape: programs -> cohorts."""
    return {
        "programs": {
            "BUS": {
                "title": "Business AS-T",
                "official_map_issues": [],
                "cohorts": {
                    "full_time": {"terms_used": 4, "needs_fix": False,
                                  "fixes": [], "plan": {}},
                    "part_time": {"terms_used": 6, "needs_fix": False,
                                  "fixes": [], "plan": {}},
                },
            },
            "ENGR": {
                "title": "Engineering AS-T",
                "official_map_issues": [
                    "ENGR 102 mapped to sem 1 (Fall) but only offered ['Fall']"
                ],
                "cohorts": {
                    "full_time": {
                        "terms_used": 5, "needs_fix": True,
                        "fixes": [{"course": "ENGR 102", "season": "Spring"}],
                        "plan": {},
                    },
                    "part_time": None,
                },
            },
        }
    }


def _tags_payload(*names):
    return {"models": [{"name": n} for n in names]}


class _FakeHTTPResponse:
    """Context-manager response object matching the slice urllib gives us."""
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _patch_tags(monkeypatch, payload):
    """Make every urllib urlopen to /api/tags return `payload` (offline)."""
    def fake_urlopen(arg, *args, **kwargs):
        return _FakeHTTPResponse(payload)
    monkeypatch.setattr(llm_assist.urllib.request, "urlopen", fake_urlopen)


def _patch_available(monkeypatch, value):
    """Force llm_assist.available() to a fixed value without touching HTTP."""
    monkeypatch.setattr(llm_assist, "available", lambda *a, **k: value)


def _patch_chat(monkeypatch, fn):
    """Replace the low-level chat helper (the only outbound /api/chat call)."""
    monkeypatch.setattr(llm_assist, "_chat", fn)


# --------------------------------------------------------------------------- #
# 1. model_present() — tag-exact matching (the corrected behavior).            #
# --------------------------------------------------------------------------- #
def test_name_matches_is_tag_exact():
    # A different tag of the same family must NOT satisfy a wanted tag.
    assert llm_assist._name_matches("gemma4:31b", "gemma4:e2b") is False
    assert llm_assist._name_matches("gemma4:e2b", "gemma4:e2b") is True
    # Bare family name resolves to the :latest tag.
    assert llm_assist._name_matches("gemma4:latest", "gemma4") is True
    assert llm_assist._name_matches("gemma4:31b", "gemma4") is False
    assert llm_assist._name_matches("", "gemma4:e2b") is False


def test_model_present_true_for_installed_tag(monkeypatch):
    _patch_tags(monkeypatch, _tags_payload("gemma4:31b", "nomic-embed-text:latest"))
    assert llm_assist.model_present("gemma4:31b") is True


def test_model_present_false_for_other_tag_same_family(monkeypatch):
    # Regression: previously this matched on 'gemma4' prefix and returned True.
    _patch_tags(monkeypatch, _tags_payload("gemma4:31b"))
    assert llm_assist.model_present("gemma4:e2b") is False


def test_model_present_false_when_absent(monkeypatch):
    _patch_tags(monkeypatch, _tags_payload("qwen3-vl:latest"))
    assert llm_assist.model_present("gemma4:31b") is False


def test_model_present_false_on_connection_error(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(llm_assist.urllib.request, "urlopen", boom)
    assert llm_assist.model_present("gemma4:31b") is False


# --------------------------------------------------------------------------- #
# 2. make_prereq_parser() — both branches.                                     #
# --------------------------------------------------------------------------- #
def test_make_prereq_parser_returns_none_when_unavailable(monkeypatch):
    _patch_available(monkeypatch, False)
    assert llm_assist.make_prereq_parser() is None


def test_make_prereq_parser_returns_callable_when_available(monkeypatch):
    _patch_available(monkeypatch, True)
    _patch_chat(monkeypatch, lambda *a, **k: '[["MATH 125","MATH 134"],["ENGL 101"]]')
    parser = llm_assist.make_prereq_parser()
    assert callable(parser)
    assert parser("MATH 125 or MATH 134, and ENGL 101") == \
        [["MATH 125", "MATH 134"], ["ENGL 101"]]


def test_make_prereq_parser_strips_code_fences(monkeypatch):
    _patch_available(monkeypatch, True)
    _patch_chat(monkeypatch, lambda *a, **k: '```json\n[["CS 101"]]\n```')
    parser = llm_assist.make_prereq_parser()
    assert parser("after CS 101") == [["CS 101"]]


def test_make_prereq_parser_empty_list_on_malformed_json(monkeypatch):
    # parse_prereq_text -> None on bad JSON; the parser wrapper turns that into [].
    _patch_available(monkeypatch, True)
    _patch_chat(monkeypatch, lambda *a, **k: "not json at all")
    parser = llm_assist.make_prereq_parser()
    assert parser("garbage") == []


def test_make_prereq_parser_empty_list_on_chat_exception(monkeypatch):
    _patch_available(monkeypatch, True)

    def boom(*a, **k):
        raise TimeoutError("ollama timed out")
    _patch_chat(monkeypatch, boom)
    parser = llm_assist.make_prereq_parser()
    assert parser("anything") == []


# --------------------------------------------------------------------------- #
# 3. parse_prereq_text() directly — success / malformed / unavailable.         #
# --------------------------------------------------------------------------- #
def test_parse_prereq_text_none_when_unavailable(monkeypatch):
    _patch_available(monkeypatch, False)
    assert llm_assist.parse_prereq_text("MATH 101") is None


def test_parse_prereq_text_rejects_non_list_json(monkeypatch):
    _patch_available(monkeypatch, True)
    _patch_chat(monkeypatch, lambda *a, **k: '{"not": "a list"}')
    assert llm_assist.parse_prereq_text("x") is None


def test_parse_prereq_text_rejects_list_of_scalars(monkeypatch):
    _patch_available(monkeypatch, True)
    _patch_chat(monkeypatch, lambda *a, **k: '["MATH 101", "ENGL 101"]')
    # inner items are not lists -> structural check fails -> None
    assert llm_assist.parse_prereq_text("x") is None


# --------------------------------------------------------------------------- #
# 4. explain() — success JSON, malformed, timeout/exception -> fallback.       #
# --------------------------------------------------------------------------- #
def test_explain_uses_llm_when_available(monkeypatch, results):
    _patch_available(monkeypatch, True)
    _patch_chat(monkeypatch, lambda *a, **k: "  DEAN BRIEFING: act now.  ")
    out = llm_assist.explain(results)
    assert out == "DEAN BRIEFING: act now."


def test_explain_falls_back_on_chat_timeout(monkeypatch, results):
    _patch_available(monkeypatch, True)

    def boom(*a, **k):
        raise TimeoutError("read timed out")
    _patch_chat(monkeypatch, boom)
    out = llm_assist.explain(results)
    # Degrades to the templated summary, which names the programs.
    assert "Business AS-T" in out
    assert "Engineering AS-T" in out


def test_explain_falls_back_on_chat_generic_exception(monkeypatch, results):
    _patch_available(monkeypatch, True)

    def boom(*a, **k):
        raise urllib.error.URLError("server hung up")
    _patch_chat(monkeypatch, boom)
    out = llm_assist.explain(results)
    assert "Business AS-T" in out and "Engineering AS-T" in out


def test_explain_falls_back_when_unavailable(monkeypatch, results):
    """Core m6 guarantee: AI absent -> non-empty templated summary naming programs."""
    _patch_available(monkeypatch, False)
    # _chat must never be called on the fallback path; trip the test if it is.
    def must_not_call(*a, **k):
        raise AssertionError("explain() called the LLM while unavailable")
    _patch_chat(monkeypatch, must_not_call)

    out = llm_assist.explain(results)
    assert out.strip()                       # non-empty
    assert "Business AS-T" in out
    assert "Engineering AS-T" in out


# --------------------------------------------------------------------------- #
# 5. _template_summary() — surfaces fixes / map issues / needs-change tags.    #
# --------------------------------------------------------------------------- #
def test_template_summary_surfaces_fix_and_map_issue(results):
    out = llm_assist._template_summary(results)
    assert "needs schedule change" in out          # ENGR full_time needs_fix
    assert "add ENGR 102 in Spring" in out          # concrete fix surfaced
    assert "published map broken" in out            # official_map_issues surfaced
    assert "full-time 4 terms" in out               # BUS clean cohort line


def test_template_summary_empty_for_no_programs():
    assert llm_assist._template_summary({"programs": {}}) == ""
    assert llm_assist._template_summary({}) == ""


# --------------------------------------------------------------------------- #
# 6. Onboarding (F19-F22): ai_status fields, ensure_model, available().        #
# --------------------------------------------------------------------------- #
def test_available_composition(monkeypatch):
    # available() == ollama_running() AND model_present()
    monkeypatch.setattr(llm_assist, "ollama_running", lambda: True)
    monkeypatch.setattr(llm_assist, "model_present", lambda *a, **k: True)
    assert llm_assist.available() is True

    monkeypatch.setattr(llm_assist, "model_present", lambda *a, **k: False)
    assert llm_assist.available() is False

    monkeypatch.setattr(llm_assist, "ollama_running", lambda: False)
    monkeypatch.setattr(llm_assist, "model_present", lambda *a, **k: True)
    assert llm_assist.available() is False


def test_ensure_model_false_when_ollama_not_installed(monkeypatch):
    monkeypatch.setattr(llm_assist, "ollama_installed", lambda: False)
    msgs = []
    assert llm_assist.ensure_model(progress=msgs.append) is False
    assert any("not installed" in m.lower() for m in msgs)


def test_ensure_model_true_when_model_already_present(monkeypatch):
    monkeypatch.setattr(llm_assist, "ollama_installed", lambda: True)
    monkeypatch.setattr(llm_assist, "model_present", lambda *a, **k: True)
    # subprocess.run must NOT be called when the model is already present.
    def must_not_pull(*a, **k):
        raise AssertionError("ensure_model pulled despite model already present")
    monkeypatch.setattr(llm_assist.subprocess, "run", must_not_pull)
    assert llm_assist.ensure_model() is True


def test_ensure_model_pulls_then_confirms_present(monkeypatch):
    monkeypatch.setattr(llm_assist, "ollama_installed", lambda: True)
    # Absent before pull, present after pull -> two-stage model_present.
    states = iter([False, True])
    monkeypatch.setattr(llm_assist, "model_present", lambda *a, **k: next(states))
    pulled = {}
    def fake_run(cmd, check=False, **k):
        pulled["cmd"] = cmd
        class _R:  # minimal CompletedProcess stand-in
            returncode = 0
        return _R()
    monkeypatch.setattr(llm_assist.subprocess, "run", fake_run)
    msgs = []
    assert llm_assist.ensure_model(progress=msgs.append) is True
    assert pulled["cmd"][:2] == ["ollama", "pull"]
    assert any("download" in m.lower() for m in msgs)


def test_ensure_model_false_when_pull_raises(monkeypatch):
    monkeypatch.setattr(llm_assist, "ollama_installed", lambda: True)
    monkeypatch.setattr(llm_assist, "model_present", lambda *a, **k: False)
    def boom(*a, **k):
        raise OSError("pull failed")
    monkeypatch.setattr(llm_assist.subprocess, "run", boom)
    msgs = []
    assert llm_assist.ensure_model(progress=msgs.append) is False
    assert any("failed" in m.lower() for m in msgs)


def test_ollama_running_true_and_false(monkeypatch):
    monkeypatch.setattr(llm_assist.urllib.request, "urlopen",
                        lambda *a, **k: _FakeHTTPResponse({"models": []}))
    assert llm_assist.ollama_running() is True

    def boom(*a, **k):
        raise urllib.error.URLError("no server")
    monkeypatch.setattr(llm_assist.urllib.request, "urlopen", boom)
    assert llm_assist.ollama_running() is False


def test_ai_status_shape_mirrors_module(monkeypatch):
    """The fields app.py.Api.ai_status() surfaces (F19) come straight from here."""
    monkeypatch.setattr(llm_assist, "ollama_installed", lambda: True)
    monkeypatch.setattr(llm_assist, "ollama_running", lambda: True)
    monkeypatch.setattr(llm_assist, "model_present", lambda *a, **k: False)
    status = {
        "installed": llm_assist.ollama_installed(),
        "running": llm_assist.ollama_running(),
        "model": llm_assist.model_present(),
        "model_name": llm_assist.MODEL,
    }
    assert status == {"installed": True, "running": True,
                      "model": False, "model_name": "gemma4:31b"}


# --------------------------------------------------------------------------- #
# 7. The corrected default MODEL is a tag-form (family:tag), not bare family.  #
# --------------------------------------------------------------------------- #
def test_default_model_is_a_tagged_gemma4():
    assert llm_assist.MODEL.startswith("gemma4:")
    assert ":" in llm_assist.MODEL


# --------------------------------------------------------------------------- #
# 8. LIVE smoke (deselected by default; only runs with -m live + real Ollama). #
# --------------------------------------------------------------------------- #
@pytest.mark.live
def test_live_explain_against_real_ollama():
    if not llm_assist.available():
        pytest.skip("Ollama or the configured model is not available")
    import engine
    res = engine.run(engine._default_data_path(), llm=None)
    out = llm_assist.explain(res)
    assert out.strip()
