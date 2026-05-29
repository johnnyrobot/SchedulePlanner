"""Meta-test: the offline QA gate covers every required test axis (m8-A t1).

This is a *test about the test suite*. It guards against silent erosion of the
green-suite gate by asserting that each coverage axis the PRD relies on is
exercised by SPECIFIC, NON-LIVE test nodeids that pytest actually collects.

Why introspect collection instead of grepping filenames? Because two of the
target files carry BOTH offline and `live` tests:

  * test_engine_features.py also holds ``test_llm_assist_cli_no_args_runs`` (live)
  * test_llm_assist.py     also holds ``test_live_explain_against_real_ollama`` (live)

A file-substring check would happily "pass" on the live test and miss a deleted
offline one. So we collect the suite, build a nodeid -> markers map, and assert
each named node is (a) collected and (b) NOT live-marked.

Axes asserted:
  * synthetic    — a non-live node in test_engine_features.py (engine vs bundled data)
  * mocked-live  — test_live_offline_pipeline.py + a FakeClient node in test_app.py
  * AI-fallback  — a non-live monkeypatched node in test_llm_assist.py

The companion ``scripts/run_qa.sh`` is the single human/CI entry point; this
test locks what that gate must keep covering.
"""
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "run_qa.sh"


def _collect_nodeids_with_markers():
    """Return {nodeid: set(marker_names)} for every test pytest collects under
    the default (offline) selection PLUS the live tests, so we can prove a named
    node is collectible at all and inspect whether it carries the `live` mark.

    We run a tiny in-process pytest collection via a subprocess to avoid any
    cross-test plugin state, parsing the machine-stable ``--collect-only -q``
    nodeid list. To see live nodes too (which `-m "not live"` would hide) we
    override the marker filter with ``-m ""``.
    """
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", "",
         "-p", "no:cacheprovider"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert out.returncode == 0, (
        f"collection failed (rc={out.returncode}):\n{out.stdout}\n{out.stderr}"
    )
    return {ln.strip() for ln in out.stdout.splitlines()
            if "::" in ln.strip() and "test" in ln}


def _live_nodeids():
    """Nodeids that ARE live-marked (collected only when -m live is selected)."""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", "live",
         "-p", "no:cacheprovider"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert out.returncode == 0, (
        f"live collection failed (rc={out.returncode}):\n{out.stdout}\n{out.stderr}"
    )
    return {ln.strip() for ln in out.stdout.splitlines()
            if "::" in ln and "test" in ln}


# Specific nodeids that MUST exist and MUST be offline (not live), one per axis.
SYNTHETIC_NODE = "tests/test_engine_features.py::test_f1_xlsx_loads"
MOCKED_LIVE_PIPELINE_NODE = (
    "tests/test_live_offline_pipeline.py::test_full_chain_offline_through_engine"
)
MOCKED_LIVE_APP_NODE = (
    "tests/test_app.py::test_fetch_live_returns_results_plus_reconciliation_and_inert"
)
AI_FALLBACK_NODE = "tests/test_llm_assist.py::test_explain_falls_back_when_unavailable"

# Known live nodes that the gate must keep DESELECTING (the "exactly 3" live set).
KNOWN_LIVE_NODES = {
    "tests/test_live_roundtrip.py::test_live_lamc_end_to_end",
    "tests/test_llm_assist.py::test_live_explain_against_real_ollama",
    "tests/test_engine_features.py::test_llm_assist_cli_no_args_runs",
}


@pytest.fixture(scope="module")
def collected():
    return _collect_nodeids_with_markers()


@pytest.fixture(scope="module")
def live_nodes():
    return _live_nodeids()


@pytest.mark.parametrize(
    "axis,nodeid",
    [
        ("synthetic", SYNTHETIC_NODE),
        ("mocked-live (pipeline)", MOCKED_LIVE_PIPELINE_NODE),
        ("mocked-live (app/FakeClient)", MOCKED_LIVE_APP_NODE),
        ("AI-fallback", AI_FALLBACK_NODE),
    ],
)
def test_axis_node_is_collected_and_offline(axis, nodeid, collected, live_nodes):
    """Each coverage axis is anchored to a real, collectible, NON-live node."""
    assert nodeid in collected, (
        f"{axis} axis node {nodeid!r} is not collected — was it renamed/removed?"
    )
    assert nodeid not in live_nodes, (
        f"{axis} axis node {nodeid!r} is `live`-marked; the offline gate must "
        f"cover this axis without network/Ollama."
    )


def test_live_set_is_exactly_the_three_known_nodes(live_nodes):
    """The live set the gate deselects is precisely the documented three.

    If this changes, scripts/run_qa.sh's EXPECTED_DESELECTED must change too;
    this test makes that coupling explicit rather than silent.
    """
    assert live_nodes == KNOWN_LIVE_NODES, (
        f"live test set drifted.\n  expected: {sorted(KNOWN_LIVE_NODES)}\n"
        f"  got:      {sorted(live_nodes)}\n"
        f"If intentional, update KNOWN_LIVE_NODES here AND EXPECTED_DESELECTED "
        f"in scripts/run_qa.sh."
    )


def test_mocked_live_app_node_uses_fakeclient(collected):
    """Sanity: the app mocked-live node we anchor to is the FakeClient-driven
    one (its name encodes the reconciliation+inert contract it drives through
    an injected client)."""
    assert MOCKED_LIVE_APP_NODE in collected
    # The companion non-live FakeClient nodes in test_app.py must also collect.
    for node in (
        "tests/test_app.py::test_fetch_live_parses_comma_terms",
        "tests/test_app.py::test_fetch_live_no_match_returns_error",
    ):
        assert node in collected, f"expected FakeClient app node {node!r} collected"


def test_run_qa_script_exists_and_is_executable():
    """The single QA entry point exists and is runnable."""
    assert SCRIPT.exists(), f"missing QA gate script at {SCRIPT}"
    import os
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable (chmod +x)"
    text = SCRIPT.read_text()
    # It must deselect live EXPLICITLY (not lean on pytest.ini addopts) and use python3.
    assert "not live" in text, "run_qa.sh must pass -m 'not live' explicitly"
    assert "python3" in text, "run_qa.sh must invoke python3 (no bare `python`)"
    assert "set -euo pipefail" in text, "run_qa.sh must use strict bash mode"
