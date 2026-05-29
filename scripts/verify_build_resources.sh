#!/usr/bin/env bash
#
# verify_build_resources.sh — OS-agnostic resource check for a built dist dir.
#
# Asserts that a PyInstaller build (a .app bundle, a one-dir tree, or any dir
# PyInstaller emitted) actually bundled the three things the app needs at
# runtime, regardless of platform:
#
#   1. ui.html                       (the UI — without it the window is blank)
#   2. files/lamc_data.xlsx          (the bundled demo workbook)
#   3. a platform OR-Tools native lib (libortools*.dylib on macOS,
#                                       ortools*.dll on Windows,
#                                       libortools*.so* on Linux)
#
# The exact sub-path inside the bundle is a PyInstaller layout detail that
# varies by version/platform, so we locate by NAME with `find`, never by a
# hardcoded path (matches BUILD.md guidance).
#
# This script is the shared core that scripts/verify_macos_build.sh and the
# cross-platform docs build on. It is intentionally platform-agnostic so the
# same check runs on a Windows or Linux build dir copied to / inspected here.
#
# Usage:
#   ./scripts/verify_build_resources.sh <dist-dir>
#
# Exit: 0 if all three resources are present; non-zero (with a FAIL line) if any
# is missing. This non-zero-on-missing behaviour is what lets the negative
# controls in verify_macos_build.sh prove the check actually bites.
set -euo pipefail

usage() {
  echo "usage: $0 <built-dist-dir>" >&2
  echo "  e.g. $0 dist/SchedulePlanner.app" >&2
}

if [ "$#" -ne 1 ]; then
  usage
  exit 2
fi

DIST_DIR="$1"

if [ ! -d "${DIST_DIR}" ]; then
  echo "FAIL: '${DIST_DIR}' is not a directory" >&2
  exit 1
fi

# find_one NAME GLOB... : echo the first match by name under DIST_DIR, or empty.
find_first() {
  # $@ are -name patterns OR'd together
  local expr=()
  local first=1
  for pat in "$@"; do
    if [ "${first}" -eq 1 ]; then
      expr+=(-name "${pat}")
      first=0
    else
      expr+=(-o -name "${pat}")
    fi
  done
  find "${DIST_DIR}" \( "${expr[@]}" \) -print 2>/dev/null | head -n 1
}

fails=0
ok() { echo "PASS: $1"; }
bad() { echo "FAIL: $1" >&2; fails=$((fails + 1)); }

# 1. ui.html
if [ -n "$(find_first 'ui.html')" ]; then
  ok "ui.html bundled"
else
  bad "ui.html not found under ${DIST_DIR}"
fi

# 2. lamc_data.xlsx — match by filename anywhere under the bundle (its exact
#    sub-path under files/ is a PyInstaller layout detail that varies).
xlsx="$(find_first 'lamc_data.xlsx')"
if [ -n "${xlsx}" ]; then
  ok "lamc_data.xlsx bundled (${xlsx#"${DIST_DIR}"/})"
else
  bad "lamc_data.xlsx not found under ${DIST_DIR}"
fi

# 3. platform OR-Tools native lib (macOS .dylib / Windows .dll / Linux .so).
ortools_lib="$(find_first 'libortools*.dylib' 'ortools*.dll' 'libortools*.so' 'libortools*.so.*')"
if [ -n "${ortools_lib}" ]; then
  ok "OR-Tools native lib bundled (${ortools_lib##*/})"
else
  bad "no OR-Tools native lib (libortools*.dylib / ortools*.dll / libortools*.so*) under ${DIST_DIR}"
fi

if [ "${fails}" -ne 0 ]; then
  echo "RESULT: ${fails} missing resource(s) in ${DIST_DIR}" >&2
  exit 1
fi

echo "RESULT: all required resources present in ${DIST_DIR}"
