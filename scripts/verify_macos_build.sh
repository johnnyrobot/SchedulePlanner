#!/usr/bin/env bash
#
# verify_macos_build.sh — headless verification of a built macOS .app bundle.
#
# Automates the headless checks from BUILD.md "Verified headlessly" (checks
# 1-3): the bundle exists, its executable is a Mach-O binary, and the three
# runtime resources (ui.html, files/lamc_data.xlsx, libortools*.dylib) are
# bundled. The GUI checklist in BUILD.md still requires a manual interactive
# session and is NOT covered here.
#
# Resource presence is delegated to scripts/verify_build_resources.sh (shared,
# OS-agnostic). This script adds the macOS-only Mach-O assertion plus a
# self-test NEGATIVE CONTROL so we can prove the resource check actually bites.
#
# Usage:
#   ./scripts/verify_macos_build.sh                       # checks dist/SchedulePlanner.app
#   ./scripts/verify_macos_build.sh path/to/Other.app     # checks a given bundle
#   ./scripts/verify_macos_build.sh --self-test           # run the negative control only
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RESOURCE_CHECK="${SCRIPT_DIR}/verify_build_resources.sh"

# negative_control: point the resource checker at a bundle-like dir that is
# MISSING the ortools lib and assert it FAILS. This proves the check bites
# rather than silently passing. Returns 0 if the control behaved as expected.
negative_control() {
  local tmp
  tmp="$(mktemp -d)"
  # Build a fake bundle with ui.html + the demo xlsx but NO ortools lib.
  mkdir -p "${tmp}/Contents/Resources/files"
  echo "<html></html>" > "${tmp}/Contents/Resources/ui.html"
  echo "fake" > "${tmp}/Contents/Resources/files/lamc_data.xlsx"

  echo "[negative control] resource check on a bundle missing libortools*.dylib:"
  if bash "${RESOURCE_CHECK}" "${tmp}" >/dev/null 2>&1; then
    echo "  UNEXPECTED PASS — the resource check did NOT bite on a missing lib" >&2
    rm -rf "${tmp}"
    return 1
  fi
  echo "  OK: resource check correctly reported FAIL (exit non-zero) on missing lib"
  rm -rf "${tmp}"
  return 0
}

main() {
  if [ ! -f "${RESOURCE_CHECK}" ]; then
    echo "FAIL: shared resource checker not found at ${RESOURCE_CHECK}" >&2
    exit 1
  fi

  if [ "${1:-}" = "--self-test" ]; then
    negative_control
    echo "self-test complete."
    exit 0
  fi

  local app="${1:-${REPO_ROOT}/dist/SchedulePlanner.app}"

  echo "=== verify_macos_build: ${app} ==="

  # Check 1: bundle exists.
  if [ -d "${app}" ]; then
    echo "PASS: bundle exists (${app})"
  else
    echo "FAIL: bundle not found at ${app} (run scripts/build_macos.sh first)" >&2
    exit 1
  fi

  # Check 2: the bundle executable is a Mach-O binary.
  local exe="${app}/Contents/MacOS/SchedulePlanner"
  if [ ! -f "${exe}" ]; then
    echo "FAIL: bundle executable missing at ${exe}" >&2
    exit 1
  fi
  if file "${exe}" | grep -q 'Mach-O'; then
    echo "PASS: $(file "${exe}")"
  else
    echo "FAIL: ${exe} is not a Mach-O binary: $(file "${exe}")" >&2
    exit 1
  fi

  # Check 3: bundled resources (delegated to the shared, OS-agnostic checker).
  echo "--- resource check (shared) ---"
  bash "${RESOURCE_CHECK}" "${app}"

  # Negative control: prove the resource check bites.
  echo "--- negative control ---"
  negative_control

  echo "=== verify_macos_build: ALL HEADLESS CHECKS PASSED ==="
  echo "(GUI behaviour is manual — see BUILD.md 'Manual GUI checklist'.)"
}

main "$@"
