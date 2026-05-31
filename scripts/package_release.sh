#!/usr/bin/env bash
#
# package_release.sh — package a built SchedulePlanner bundle into a named,
# checksummed release artifact (macOS / Linux).
#
# This is a SEPARATE, opt-in, post-build step. It does NOT build or modify the
# unsigned build produced by scripts/build_macos.sh / scripts/build_linux.sh —
# it only zips/tars what is already under dist/ and records a SHA-256.
#
# Public-release order is:  build  ->  sign+notarize  ->  package (this script)
# ->  checksum (this script appends it). Signing/notarization is a separate
# opt-in step that runs BEFORE this one; THIS SCRIPT NEVER SIGNS ANYTHING. On
# macOS it only REPORTS whether the bundle is already signed (codesign / spctl);
# it NEVER claims a build was notarized.
#
# Shared conventions (matched across all packaging artifacts):
#   - VERSION source : repo-root file VERSION (one trimmed semver line).
#                      Missing -> fall back to 0.0.0-dev.
#   - artifact name  : SchedulePlanner-<version>-<os>-<arch>.<ext>
#                        os   = macos | linux   (Windows uses package_release.ps1)
#                        arch = arm64 | x64      (arm64/aarch64->arm64; x86_64/amd64->x64)
#                        ext  = macOS .zip via `ditto -c -k --keepParent`
#                               (preserves the .app bundle, symlinks, signature)
#                             | Linux .tar.gz of the one-dir tree
#   - output dir     : dist/release/  (created on demand; under gitignored dist/)
#   - checksums      : dist/release/SHA256SUMS, one "<sha256>  <filename>" line
#                      per artifact (shasum -a 256 format), verifiable with:
#                        (cd dist/release && shasum -a 256 -c SHA256SUMS)
#
# Pre-package gate: runs scripts/verify_build_resources.sh against the build dir
# FIRST and ABORTS (non-zero) if any required runtime resource is missing, so we
# never publish an incomplete bundle.
#
# Usage:
#   ./scripts/package_release.sh                 # auto-locate the build dir for this OS
#   ./scripts/package_release.sh <build-dir>     # override (e.g. a signed copy)
#
set -euo pipefail

# Resolve repo root = parent of the dir containing this script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

RESOURCE_CHECK="${SCRIPT_DIR}/verify_build_resources.sh"
RELEASE_DIR="${REPO_ROOT}/dist/release"
SUMS_FILE="${RELEASE_DIR}/SHA256SUMS"

die() { echo "error: $*" >&2; exit 1; }

# --- version (single source of truth) ---------------------------------------
VERSION_FILE="${REPO_ROOT}/VERSION"
if [ -f "${VERSION_FILE}" ]; then
  VERSION="$(tr -d '[:space:]' < "${VERSION_FILE}")"
  [ -n "${VERSION}" ] || die "VERSION file is empty (expected one semver line)."
else
  VERSION="0.0.0-dev"
  echo "note: no VERSION file at ${VERSION_FILE}; falling back to ${VERSION}." >&2
fi

# --- detect os + arch (per the naming convention) ---------------------------
UNAME_S="$(uname -s)"
case "${UNAME_S}" in
  Darwin) OS="macos" ;;
  Linux)  OS="linux" ;;
  *)      die "unsupported OS '${UNAME_S}' for this script (use package_release.ps1 on Windows)." ;;
esac

UNAME_M="$(uname -m)"
case "${UNAME_M}" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64|amd64)  ARCH="x64" ;;
  *)             die "unrecognized machine arch '${UNAME_M}' (expected arm64/aarch64 or x86_64/amd64)." ;;
esac

# --- locate the build dir ----------------------------------------------------
if [ "$#" -gt 1 ]; then
  die "too many arguments. Usage: $0 [build-dir]"
fi

if [ "$#" -eq 1 ]; then
  BUILD_DIR="$1"
  [ -d "${BUILD_DIR}" ] || die "build dir '${BUILD_DIR}' does not exist."
else
  case "${OS}" in
    macos) BUILD_DIR="${REPO_ROOT}/dist/SchedulePlanner.app" ;;
    linux) BUILD_DIR="${REPO_ROOT}/dist/SchedulePlanner" ;;
  esac
  if [ ! -d "${BUILD_DIR}" ]; then
    echo "error: expected build dir not found: ${BUILD_DIR}" >&2
    case "${OS}" in
      macos) echo "       Build it first with: ./scripts/build_macos.sh" >&2 ;;
      linux) echo "       Build it first with: ./scripts/build_linux.sh" >&2 ;;
    esac
    exit 1
  fi
fi
BUILD_DIR="$(cd "${BUILD_DIR}" && pwd)"

# --- pre-package gate: required runtime resources must be bundled ------------
[ -f "${RESOURCE_CHECK}" ] || die "resource checker not found at ${RESOURCE_CHECK}"
echo "=== package_release: ${OS}-${ARCH} v${VERSION} ==="
echo "--- pre-package resource gate (${BUILD_DIR}) ---"
if ! bash "${RESOURCE_CHECK}" "${BUILD_DIR}"; then
  die "resource gate FAILED for ${BUILD_DIR}; refusing to package an incomplete bundle."
fi

# --- package -----------------------------------------------------------------
mkdir -p "${RELEASE_DIR}"
ARTIFACT_BASE="SchedulePlanner-${VERSION}-${OS}-${ARCH}"

case "${OS}" in
  macos)
    ARTIFACT="${RELEASE_DIR}/${ARTIFACT_BASE}.zip"
    command -v ditto >/dev/null 2>&1 || die "'ditto' not found (required to zip a macOS .app)."
    echo "--- packaging (ditto -c -k --keepParent) ---"
    rm -f "${ARTIFACT}"
    # ditto --keepParent preserves the .app dir itself, its internal symlinks,
    # and any embedded code signature — unlike plain `zip`, which can corrupt them.
    ditto -c -k --keepParent "${BUILD_DIR}" "${ARTIFACT}"
    ;;
  linux)
    ARTIFACT="${RELEASE_DIR}/${ARTIFACT_BASE}.tar.gz"
    command -v tar >/dev/null 2>&1 || die "'tar' not found (required to package the Linux one-dir)."
    echo "--- packaging (tar -czf) ---"
    rm -f "${ARTIFACT}"
    # Archive the one-dir BY ITS PARENT so the tree unpacks under its own name.
    tar -czf "${ARTIFACT}" -C "$(dirname "${BUILD_DIR}")" "$(basename "${BUILD_DIR}")"
    ;;
esac
[ -f "${ARTIFACT}" ] || die "packaging did not produce ${ARTIFACT}"

# --- checksum (append to the single SHA256SUMS) ------------------------------
ARTIFACT_NAME="$(basename "${ARTIFACT}")"
echo "--- sha256 ---"
# Compute from within RELEASE_DIR so the line carries the BARE filename, which is
# exactly what `shasum -a 256 -c SHA256SUMS` expects when run from that dir.
SUM_LINE="$(cd "${RELEASE_DIR}" && shasum -a 256 "${ARTIFACT_NAME}")"
# Replace (don't duplicate) any prior line for this exact filename so re-packaging
# the same artifact updates its checksum in place.
touch "${SUMS_FILE}"
if grep -q "  ${ARTIFACT_NAME}\$" "${SUMS_FILE}" 2>/dev/null; then
  grep -v "  ${ARTIFACT_NAME}\$" "${SUMS_FILE}" > "${SUMS_FILE}.tmp" || true
  mv "${SUMS_FILE}.tmp" "${SUMS_FILE}"
fi
echo "${SUM_LINE}" >> "${SUMS_FILE}"
SHA256="${SUM_LINE%% *}"

# --- signed-state reporting (report ONLY; NEVER claims notarization) ---------
if [ "${OS}" = "macos" ]; then
  echo "--- signing state (report only — does NOT assert notarization) ---"
  # codesign exits 0 for ANY signature, INCLUDING the ad-hoc signature PyInstaller
  # applies on Apple Silicon. Distinguish ad-hoc (not distributable) from a real
  # Developer ID signature so the one-word verdict is not misleading.
  if CS_OUT="$(codesign -dv --verbose=4 "${BUILD_DIR}" 2>&1)"; then
    if printf '%s\n' "${CS_OUT}" | grep -q 'Signature=adhoc'; then
      echo "AD-HOC SIGNED: ${BUILD_DIR} carries only an AD-HOC signature (Signature=adhoc,"
      echo "  no Developer ID, TeamIdentifier=not set). This is exactly what PyInstaller"
      echo "  emits for the UNSIGNED internal build — it is NOT a distributable signature"
      echo "  and Gatekeeper (spctl) will reject it. For a PUBLIC release, run"
      echo "  scripts/sign_notarize_macos.sh (Developer ID + notarization) BEFORE packaging."
    else
      echo "SIGNED: ${BUILD_DIR} carries a code signature (see Authority/TeamIdentifier below)."
    fi
    printf '%s\n' "${CS_OUT}" | sed 's/^/    /'
    echo "  Gatekeeper assessment (spctl --assess --type execute):"
    spctl --assess --type execute --verbose=4 "${BUILD_DIR}" 2>&1 | sed 's/^/    /' || true
    echo "  NOTE: the lines above are the ACTUAL codesign/spctl output. A signature"
    echo "        and even an 'accepted' spctl verdict do NOT by themselves prove the"
    echo "        app was NOTARIZED — confirm notarization separately with the operator."
  else
    echo "UNSIGNED: ${BUILD_DIR} has no code signature (codesign found none)."
    echo "  This is an internal-tester build. Sign+notarize as a SEPARATE opt-in step"
    echo "  BEFORE public release; this packaging script does not sign."
  fi
elif [ "${OS}" = "linux" ]; then
  echo "--- signing state ---"
  echo "N/A: OS-level code signing is not applicable to the Linux one-dir artifact."
fi

# --- summary -----------------------------------------------------------------
echo ""
echo "=== package_release: DONE ==="
echo "artifact : ${ARTIFACT}"
echo "sha256   : ${SHA256}"
echo "checksums: ${SUMS_FILE}"
echo "verify   : (cd ${RELEASE_DIR} && shasum -a 256 -c SHA256SUMS)"
