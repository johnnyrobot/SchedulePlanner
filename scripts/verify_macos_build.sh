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

  # Check 4: the bundled JRE (zero-setup catalog-PDF parsing) exists and runs.
  local jre_java="${app}/Contents/Resources/jre/bin/java"
  echo "--- bundled JRE check ---"
  if [ ! -x "${jre_java}" ]; then
    echo "FAIL: bundled JRE launcher missing/not executable at ${jre_java}" >&2
    echo "      (scripts/build_macos.sh should ditto build/jre into the bundle)" >&2
    exit 1
  fi
  if ! file "${jre_java}" | grep -q 'Mach-O'; then
    echo "FAIL: ${jre_java} is not a Mach-O binary: $(file "${jre_java}")" >&2
    exit 1
  fi
  if "${jre_java}" -version >/dev/null 2>&1; then
    echo "PASS: bundled JRE runs ($("${jre_java}" -version 2>&1 | head -1))"
  else
    echo "FAIL: bundled JRE at ${jre_java} did not run '-version'" >&2
    exit 1
  fi

  # Check 5: the OpenDataLoader Java CLI jar (catalog-PDF / Local AA/AS GE) is
  # bundled. It ships via `--collect-data opendataloader_pdf`; this asserts the
  # trim (collect-data, not collect-all) still includes the jar the feature runs.
  echo "--- catalog jar check ---"
  local odl_jar
  odl_jar="$(find "${app}" -name 'opendataloader-pdf-cli.jar' -print 2>/dev/null | head -n 1)"
  if [ -n "${odl_jar}" ]; then
    echo "PASS: OpenDataLoader CLI jar bundled (${odl_jar##*/})"
  else
    echo "FAIL: opendataloader-pdf-cli.jar not found under ${app}" >&2
    echo "      (build_macos.sh must keep --collect-data opendataloader_pdf)" >&2
    exit 1
  fi

  # Check 6: NO hybrid-mode ML bloat. The catalog feature runs the bundled jar in
  # a JVM subprocess, so the Python side never needs torch/docling. A stray
  # `--collect-all opendataloader_pdf` would drag ~300+ MB of those in via the
  # optional hybrid_server submodule; assert they're absent so the trim can't
  # silently regress.
  echo "--- no-bloat regression guard ---"
  local bloat=""
  local hit
  for pat in 'libtorch*.dylib' 'libtorch*.so'; do
    hit="$(find "${app}" -name "${pat}" -print 2>/dev/null | head -n 1)"
    [ -n "${hit}" ] && bloat="${bloat} ${hit}"
  done
  for d in torch docling; do
    hit="$(find "${app}" -type d -name "${d}" -print 2>/dev/null | head -n 1)"
    [ -n "${hit}" ] && bloat="${bloat} ${hit}"
  done
  if [ -n "${bloat}" ]; then
    echo "FAIL: unexpected ML/hybrid bloat in bundle:${bloat}" >&2
    echo "      (build scripts must use --collect-data opendataloader_pdf, not --collect-all)" >&2
    exit 1
  fi
  echo "PASS: no torch/docling hybrid-mode bloat in bundle"

  # Check 7: release metadata — a reverse-DNS bundle identifier and a real
  # version string (PyInstaller leaves these at 'SchedulePlanner' / 0.0.0 unless
  # build_macos.sh stamps them). Guards a distributable bundle's identity.
  echo "--- release metadata check ---"
  local plist="${app}/Contents/Info.plist"
  local bid ver
  bid="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "${plist}" 2>/dev/null || echo '')"
  ver="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "${plist}" 2>/dev/null || echo '')"
  if [[ "${bid}" == *.* ]] && [ "${bid}" != "SchedulePlanner" ]; then
    echo "PASS: CFBundleIdentifier is reverse-DNS (${bid})"
  else
    echo "FAIL: CFBundleIdentifier should be reverse-DNS, got '${bid}'" >&2
    exit 1
  fi
  if [ -n "${ver}" ] && [ "${ver}" != "0.0.0" ]; then
    echo "PASS: CFBundleShortVersionString set (${ver})"
  else
    echo "FAIL: CFBundleShortVersionString is unset/0.0.0 ('${ver}')" >&2
    exit 1
  fi

  # Negative control: prove the resource check bites.
  echo "--- negative control ---"
  negative_control

  echo "=== verify_macos_build: ALL HEADLESS CHECKS PASSED ==="
  echo "(GUI behaviour is manual — see BUILD.md 'Manual GUI checklist'.)"
}

main "$@"
