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
import os
import pathlib
import re
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
            if ln.strip().startswith("tests/") and "::" in ln}


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

# Known live nodes that the gate must keep DESELECTING (the live set). The
# eLumen-client live test (test_live_lamc_endpoint_schema) was added with the
# real eLumen prereq client; it hits the public endpoint and is deselected by
# default. This set is the SINGLE SOURCE OF TRUTH for the live count:
# scripts/run_qa.sh derives EXPECTED_DESELECTED from len(KNOWN_LIVE_NODES), so
# the shell gate cannot drift out of lock-step with it.
KNOWN_LIVE_NODES = {
    "tests/test_live_roundtrip.py::test_live_lamc_end_to_end",
    "tests/test_llm_assist.py::test_live_explain_against_real_ollama",
    "tests/test_engine_features.py::test_llm_assist_cli_no_args_runs",
    "tests/test_elumen_client.py::test_live_lamc_endpoint_schema",
    # Spec 2: real OpenDataLoader PDF extraction (needs Java 11+ + the package).
    "tests/test_pdf_loader.py::test_extract_real_pdf_roundtrip",
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


def test_live_set_is_exactly_the_known_nodes(live_nodes):
    """The live set the gate deselects is precisely KNOWN_LIVE_NODES.

    This is the single source of truth for the live count: scripts/run_qa.sh
    derives EXPECTED_DESELECTED from len(KNOWN_LIVE_NODES), so pinning the set
    here transitively keeps the shell gate in lock-step. Add/remove a live test
    and you edit ONLY KNOWN_LIVE_NODES — both this assertion and the gate follow.
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
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable (chmod +x)"
    text = SCRIPT.read_text()
    # It must deselect live EXPLICITLY (not lean on pytest.ini addopts) and use python3.
    assert "not live" in text, "run_qa.sh must pass -m 'not live' explicitly"
    assert "python3" in text, "run_qa.sh must invoke python3 (no bare `python`)"
    assert "set -euo pipefail" in text, "run_qa.sh must use strict bash mode"


def test_run_qa_derives_deselected_count_from_known_live_nodes():
    """EXPECTED_DESELECTED is DERIVED from KNOWN_LIVE_NODES, never a second
    hard-coded magic literal that can silently drift out of lock-step with the
    documented live set (review M8).

    The meta-test above pins KNOWN_LIVE_NODES == the real live collection, so
    deriving the shell's deselected count from len(KNOWN_LIVE_NODES) transitively
    couples the gate to reality through ONE source of truth.
    """
    text = SCRIPT.read_text()
    assert "from tests.test_qa_gate import KNOWN_LIVE_NODES" in text, (
        "run_qa.sh must derive the live count from KNOWN_LIVE_NODES, not hard-code it."
    )
    assert re.search(r"len\(\s*KNOWN_LIVE_NODES\s*\)", text), (
        "run_qa.sh must compute len(KNOWN_LIVE_NODES) for EXPECTED_DESELECTED."
    )
    assert not re.search(r"EXPECTED_DESELECTED\s*=\s*[0-9]", text), (
        "EXPECTED_DESELECTED must not be a hard-coded number; derive it so the "
        "two counts cannot drift apart."
    )


def test_ge_caveat_is_honest():
    from build_live_workbook import _GE_CAVEAT
    low = _GE_CAVEAT.lower()
    assert "pending" in low and "best-effort" in low
    assert "production-ready" not in low or "not production-ready" in low
