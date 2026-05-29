#!/usr/bin/env bash
#
# run_qa.sh — single green-suite QA gate for edgesched.
#
# Runs the full OFFLINE test suite (the `live` mark is deselected explicitly,
# NOT via ambient pytest.ini addopts) and reports a count-agnostic PASS line.
#
#   * exit 0 only when pytest exits 0 AND exactly the live tests were deselected
#   * the passed-count is intentionally NOT asserted: sibling tasks add tests
#     concurrently, so freezing it would make this gate brittle. We assert the
#     stable invariant instead — the number of `live`-deselected tests.
#
# Usage:  scripts/run_qa.sh
#
set -euo pipefail

# Resolve the repo root from this script's own location (works regardless of
# the caller's cwd), so the gate runs against the right checkout.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
cd "${REPO_ROOT}"

# The number of `live`-marked tests deselected by `-m "not live"`. This is the
# stable invariant the gate locks; bump it only when a live test is added/removed.
EXPECTED_DESELECTED=3

# Only python3 is on PATH in this environment (no bare `python`).
PYTHON=python3

if ! command -v "${PYTHON}" >/dev/null 2>&1; then
    echo "QA gate ERROR: ${PYTHON} not found on PATH" >&2
    exit 2
fi

if ! "${PYTHON}" -c "import pytest" >/dev/null 2>&1; then
    echo "QA gate ERROR: pytest is not installed for ${PYTHON} (try: ${PYTHON} -m pip install pytest)" >&2
    exit 2
fi

# Run the offline suite. Pass `-m 'not live'` EXPLICITLY rather than relying on
# pytest.ini's addopts, so the gate is self-contained. Capture output so we can
# both echo it and parse the deselected count.
set +e
OUTPUT="$("${PYTHON}" -m pytest -q -m 'not live' "$@" 2>&1)"
STATUS=$?
set -e

echo "${OUTPUT}"

if [ "${STATUS}" -ne 0 ]; then
    echo "QA gate FAIL: pytest exited ${STATUS}" >&2
    exit "${STATUS}"
fi

# Parse the "<N> deselected" figure from pytest's summary line (e.g.
# "120 passed, 3 deselected in 6.01s"). Absent means 0 deselected.
DESELECTED="$(printf '%s\n' "${OUTPUT}" | grep -oE '[0-9]+ deselected' | grep -oE '^[0-9]+' | tail -n1)"
DESELECTED="${DESELECTED:-0}"

if [ "${DESELECTED}" -ne "${EXPECTED_DESELECTED}" ]; then
    echo "QA gate FAIL: expected ${EXPECTED_DESELECTED} live tests deselected, saw ${DESELECTED}" >&2
    echo "(If a live test was intentionally added/removed, update EXPECTED_DESELECTED in scripts/run_qa.sh.)" >&2
    exit 1
fi

echo "QA gate PASS (live deselected: ${DESELECTED})"
exit 0
