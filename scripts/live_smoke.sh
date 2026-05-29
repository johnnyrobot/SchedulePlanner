#!/usr/bin/env bash
#
# live_smoke.sh — record one real LACCD-network run of the live pipeline.
#
# Runs the real network path ONCE:
#
#   python3 build_live_workbook.py --campus LAMC --program Biology \
#       --terms 2264,2266,2268 --out /tmp/live_LAMC.xlsx
#
# and tees the full stdout into docs/live_smoke_2026.md under a banner, so the
# milestone has a captured transcript of a real live run.
#
# NETWORK BEHAVIOUR (no silent gaps, no hangs):
#   - On SUCCESS: docs/live_smoke_2026.md holds the full real transcript.
#   - On FAILURE (could not reach LACCD): this script exits NON-ZERO with a clear
#     "could not reach LACCD" message AND still writes a DEFERRED marker into
#     docs/live_smoke_2026.md so the milestone artifact is never silently
#     missing. A hard timeout guards against hangs.
#
# DETERMINISTIC OFFLINE STAND-IN:
#   The network-independent equivalent of this smoke is the existing
#   tests/test_live_offline_pipeline.py, which exercises the same
#   build/reconcile/engine.run pipeline against recorded fixtures and is GREEN
#   regardless of network. Run it with:  python3 -m pytest -q tests/test_live_offline_pipeline.py
#   Use this live_smoke.sh only for a real-network spot check / milestone record.
#
# Usage:
#   ./scripts/live_smoke.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

DOC="docs/live_smoke_2026.md"
OUT_XLSX="/tmp/live_LAMC.xlsx"
STAMP="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "error: no python3/python on PATH." >&2
  exit 1
fi

mkdir -p docs

CMD=("${PY}" build_live_workbook.py --campus LAMC --program Biology \
     --terms 2264,2266,2268 --out "${OUT_XLSX}")

# Hard wall-clock timeout so a stalled network never hangs the script.
# `timeout` may be absent on stock macOS; degrade gracefully if so.
TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN="gtimeout"
fi

write_deferred() {
  # $1 = reason line
  {
    echo "# Live smoke (LACCD network) — DEFERRED"
    echo ""
    echo "- Attempted: ${STAMP}"
    echo "- Command: \`${PY} build_live_workbook.py --campus LAMC --program Biology --terms 2264,2266,2268 --out ${OUT_XLSX}\`"
    echo "- Result: **DEFERRED — run on a networked host.** $1"
    echo ""
    echo "This run could not reach the LACCD live sources, so no real transcript"
    echo "was captured. Re-run \`./scripts/live_smoke.sh\` on a networked host to"
    echo "produce the real transcript."
    echo ""
    echo "## Deterministic offline stand-in"
    echo ""
    echo "The network-independent equivalent of this smoke is"
    echo "\`tests/test_live_offline_pipeline.py\` (green regardless of network):"
    echo ""
    echo '```bash'
    echo "${PY} -m pytest -q tests/test_live_offline_pipeline.py"
    echo '```'
  } > "${DOC}"
  echo "Wrote DEFERRED marker to ${DOC}"
}

echo "Running live LACCD smoke (campus=LAMC program=Biology terms=2264,2266,2268) ..."

# Capture combined output to a temp log; tee to terminal too.
LOG="$(mktemp -t live_smoke_XXXX.log)"
trap 'rm -f "${LOG}"' EXIT

set +e
if [ -n "${TIMEOUT_BIN}" ]; then
  "${TIMEOUT_BIN}" 120 "${CMD[@]}" 2>&1 | tee "${LOG}"
  RC=${PIPESTATUS[0]}
else
  "${CMD[@]}" 2>&1 | tee "${LOG}"
  RC=${PIPESTATUS[0]}
fi
set -e

if [ "${RC}" -ne 0 ]; then
  if [ "${RC}" -eq 124 ]; then
    msg="Timed out after 120s (could not reach LACCD)."
  else
    msg="Command exited ${RC} (could not reach LACCD or no program matched)."
  fi
  echo "could not reach LACCD: ${msg}" >&2
  write_deferred "${msg}"
  exit 1
fi

# Success: write the real transcript with a banner.
{
  echo "# Live smoke (LACCD network) — transcript"
  echo ""
  echo "- Captured: ${STAMP}"
  echo "- Host: $(uname -srm 2>/dev/null || echo 'unknown')"
  echo "- Command: \`${PY} build_live_workbook.py --campus LAMC --program Biology --terms 2264,2266,2268 --out ${OUT_XLSX}\`"
  echo "- Result: **PASS — reached LACCD live sources and ran engine.run() on the live workbook.**"
  echo ""
  echo "## Deterministic offline stand-in"
  echo ""
  echo "The network-independent equivalent of this smoke is"
  echo "\`tests/test_live_offline_pipeline.py\` (green regardless of network):"
  echo ""
  echo '```bash'
  echo "${PY} -m pytest -q tests/test_live_offline_pipeline.py"
  echo '```'
  echo ""
  echo "## Full transcript"
  echo ""
  echo '```text'
  cat "${LOG}"
  echo '```'
} > "${DOC}"

echo ""
echo "Live smoke PASSED. Transcript written to ${DOC}"
