#!/usr/bin/env bash
#
# package_dmg_macos.sh — build a SIGNED + NOTARIZED + STAPLED .dmg installer
# (drag-to-Applications) from a built dist/SchedulePlanner.app.
#
# Uses ONLY built-in macOS tools (hdiutil + osascript) — no Homebrew/create-dmg
# required. Run this AFTER scripts/sign_notarize_macos.sh has produced a signed,
# notarized, stapled .app; this packages that app into a disk image and then
# signs + notarizes + staples the DMG itself (so the downloaded .dmg also passes
# Gatekeeper, not just the app inside).
#
# Fail-closed: checks tools + credentials up front, prints real tool output, and
# claims success ONLY when notarytool returns Accepted and stapler + spctl pass.
# The Finder window styling (icon positions) is BEST-EFFORT and non-fatal — if it
# can't run (e.g. Finder automation not permitted) the DMG still ships with the
# app + an Applications alias so drag-to-install works; only the cosmetic layout
# is skipped.
#
# Required env vars (same contract as sign_notarize_macos.sh):
#   MAC_SIGN_IDENTITY    Developer ID Application identity string
#   MAC_NOTARY_PROFILE   notarytool keychain profile  (OR the trio below)
#   MAC_NOTARY_APPLE_ID + MAC_NOTARY_TEAM_ID + MAC_NOTARY_PASSWORD
#
# Usage:
#   ./scripts/package_dmg_macos.sh                    # dist/SchedulePlanner.app
#   ./scripts/package_dmg_macos.sh path/to/Other.app
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
cd "${REPO_ROOT}"

# --- Inputs -----------------------------------------------------------------
APP="${1:-dist/SchedulePlanner.app}"
APP_BASENAME="$(basename "${APP}")"            # SchedulePlanner.app
APP_NAME="${APP_BASENAME%.app}"                # SchedulePlanner

if [ -f "${REPO_ROOT}/VERSION" ]; then
  VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
else
  VERSION="0.0.0-dev"
fi
VERSION="${VERSION:-0.0.0-dev}"

case "$(uname -m)" in
  arm64)  ARCH="arm64" ;;
  x86_64) ARCH="x86_64" ;;
  *)      ARCH="$(uname -m)" ;;
esac

VOL_NAME="${APP_NAME} ${VERSION}"
RELEASE_DIR="${REPO_ROOT}/dist/release"
DMG="${RELEASE_DIR}/${APP_NAME}-${VERSION}-macos-${ARCH}.dmg"
NOTARY_LOG="${RELEASE_DIR}/notarytool-dmg-submit.log"

# --- Fail-closed preflight: tools -------------------------------------------
missing_tools=()
for tool in hdiutil codesign xcrun stapler spctl osascript shasum ditto; do
  command -v "${tool}" >/dev/null 2>&1 || missing_tools+=("${tool}")
done
if command -v xcrun >/dev/null 2>&1; then
  xcrun --find notarytool >/dev/null 2>&1 || missing_tools+=("notarytool (via xcrun)")
fi
if [ "${#missing_tools[@]}" -ne 0 ]; then
  echo "error: required tool(s) not available:" >&2
  for t in "${missing_tools[@]}"; do echo "  - ${t}" >&2; done
  exit 1
fi

# --- Fail-closed preflight: inputs + credentials ----------------------------
if [ ! -d "${APP}" ]; then
  echo "error: app bundle not found at '${APP}' (run scripts/build_macos.sh +" >&2
  echo "       scripts/sign_notarize_macos.sh first)." >&2
  exit 1
fi

NOTARY_MODE=""
missing_creds=()
[ -z "${MAC_SIGN_IDENTITY:-}" ] && missing_creds+=("MAC_SIGN_IDENTITY")
if [ -n "${MAC_NOTARY_PROFILE:-}" ]; then
  NOTARY_MODE="profile"
elif [ -n "${MAC_NOTARY_APPLE_ID:-}" ] || [ -n "${MAC_NOTARY_TEAM_ID:-}" ] || [ -n "${MAC_NOTARY_PASSWORD:-}" ]; then
  NOTARY_MODE="trio"
  [ -z "${MAC_NOTARY_APPLE_ID:-}" ] && missing_creds+=("MAC_NOTARY_APPLE_ID")
  [ -z "${MAC_NOTARY_TEAM_ID:-}" ]  && missing_creds+=("MAC_NOTARY_TEAM_ID")
  [ -z "${MAC_NOTARY_PASSWORD:-}" ] && missing_creds+=("MAC_NOTARY_PASSWORD")
else
  missing_creds+=("MAC_NOTARY_PROFILE (or the trio MAC_NOTARY_APPLE_ID + MAC_NOTARY_TEAM_ID + MAC_NOTARY_PASSWORD)")
fi
if [ "${#missing_creds[@]}" -ne 0 ]; then
  echo "error: missing required signing/notarization input(s):" >&2
  for c in "${missing_creds[@]}"; do echo "  - ${c}" >&2; done
  exit 1
fi

echo "=== package_dmg_macos: ${APP_NAME} ${VERSION} (${ARCH}) ==="

# --- Pre-package gate: the app must already be signed (ideally stapled) ------
echo "--- verifying the source .app is Developer-ID signed ---"
codesign --verify --strict --verbose=2 "${APP}"
if xcrun stapler validate "${APP}" >/dev/null 2>&1; then
  echo "  source .app is notarized + stapled."
else
  echo "  WARNING: source .app is not stapled; notarizing the DMG below still" >&2
  echo "           covers the disk image, but run sign_notarize_macos.sh first" >&2
  echo "           for a fully stapled app." >&2
fi

# --- Stage a folder containing only the app + an Applications alias ----------
STAGE="$(mktemp -d)"
MOUNT_DIR=""
RW_DMG="$(mktemp -u)_rw.dmg"
cleanup() {
  [ -n "${MOUNT_DIR}" ] && hdiutil detach "${MOUNT_DIR}" >/dev/null 2>&1 || true
  rm -rf "${STAGE}" 2>/dev/null || true
  rm -f "${RW_DMG}" 2>/dev/null || true
}
trap cleanup EXIT

echo "--- staging bundle + Applications alias ---"
ditto "${APP}" "${STAGE}/${APP_BASENAME}"     # preserves signature/symlinks
ln -s /Applications "${STAGE}/Applications"

# --- Build a read-write DMG sized to content --------------------------------
echo "--- hdiutil create (read-write, sized to content) ---"
hdiutil create -srcfolder "${STAGE}" -volname "${VOL_NAME}" \
  -fs HFS+ -format UDRW -ov "${RW_DMG}" >/dev/null
echo "  staged read-write image: ${RW_DMG}"

# --- Mount and apply BEST-EFFORT Finder window styling ----------------------
echo "--- mounting to apply layout ---"
# NB: do NOT pass -nobrowse here — Finder must "see" the mounted volume to script
# its window layout, else `tell disk` fails with -1728 (can't get disk). Let it
# mount at /Volumes/<VOL_NAME> so Finder can reference it by name.
hdiutil attach "${RW_DMG}" -noverify -noautoopen >/dev/null
MOUNT_DIR="/Volumes/${VOL_NAME}"
sleep 2   # give Finder a moment to register the mounted volume

style_window() {
  # Returns non-zero if Finder styling can't be applied (non-fatal).
  osascript <<OSA
tell application "Finder"
  tell disk "${VOL_NAME}"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 120, 840, 500}
    set theViewOptions to the icon view options of container window
    set arrangement of theViewOptions to not arranged
    set icon size of theViewOptions to 120
    set position of item "${APP_BASENAME}" of container window to {160, 200}
    set position of item "Applications" of container window to {480, 200}
    update without registering applications
    delay 1
    close
  end tell
end tell
OSA
}

echo "--- applying Finder window layout (best-effort, may prompt to control Finder) ---"
STYLE_OK="no"
# Run in the background with a manual watchdog so a stuck automation prompt can
# never hang the build; macOS has no `timeout(1)`.
set +e
style_window >/tmp/dmg_style.log 2>&1 &
STYLE_PID=$!
waited=0
while kill -0 "${STYLE_PID}" 2>/dev/null; do
  sleep 1; waited=$((waited + 1))
  if [ "${waited}" -ge 30 ]; then
    kill "${STYLE_PID}" 2>/dev/null
    break
  fi
done
wait "${STYLE_PID}" 2>/dev/null && STYLE_OK="yes"
set -e
if [ "${STYLE_OK}" = "yes" ]; then
  echo "  PASS: window layout applied (positioned icons + Applications alias)."
else
  echo "  NOTE: Finder styling skipped ($(tail -1 /tmp/dmg_style.log 2>/dev/null || echo 'no detail'))."
  echo "        DMG still ships with the app + Applications alias — drag-to-install works,"
  echo "        just with Finder's default icon arrangement."
fi
sync

echo "--- unmounting ---"
hdiutil detach "${MOUNT_DIR}" >/dev/null
MOUNT_DIR=""

# --- Convert to compressed, read-only DMG -----------------------------------
echo "--- hdiutil convert (UDZO, compressed) ---"
mkdir -p "${RELEASE_DIR}"
rm -f "${DMG}"
hdiutil convert "${RW_DMG}" -format UDZO -imagekey zlib-level=9 -o "${DMG}" >/dev/null
echo "  built: ${DMG#"${REPO_ROOT}"/}"

# --- Sign the DMG -----------------------------------------------------------
echo "--- codesign the DMG (Developer ID + timestamp) ---"
codesign --force --sign "${MAC_SIGN_IDENTITY}" --timestamp "${DMG}"
codesign --verify --verbose=2 "${DMG}"

# --- Notarize the DMG (--wait) ----------------------------------------------
echo "--- xcrun notarytool submit --wait (the DMG) ---"
set +e
if [ "${NOTARY_MODE}" = "profile" ]; then
  echo "  using keychain profile: MAC_NOTARY_PROFILE"
  xcrun notarytool submit "${DMG}" --keychain-profile "${MAC_NOTARY_PROFILE}" --wait 2>&1 | tee "${NOTARY_LOG}"
else
  echo "  using Apple ID + Team ID + app-specific password (from env)"
  xcrun notarytool submit "${DMG}" \
    --apple-id "${MAC_NOTARY_APPLE_ID}" \
    --team-id "${MAC_NOTARY_TEAM_ID}" \
    --password "${MAC_NOTARY_PASSWORD}" --wait 2>&1 | tee "${NOTARY_LOG}"
fi
NOTARY_STATUS="${PIPESTATUS[0]}"
set -e
if [ "${NOTARY_STATUS}" -ne 0 ]; then
  echo "error: notarytool submit failed (exit ${NOTARY_STATUS}). See ${NOTARY_LOG#"${REPO_ROOT}"/}." >&2
  exit 1
fi
if ! grep -Eq 'status:[[:space:]]*Accepted' "${NOTARY_LOG}"; then
  echo "error: DMG notarization did NOT return 'Accepted' — refusing to claim success." >&2
  echo "       Pull the detailed log: xcrun notarytool log <id> --keychain-profile <name>" >&2
  exit 1
fi
echo "  DMG notarization status: Accepted"

# --- Staple + verify --------------------------------------------------------
echo "--- xcrun stapler staple (the DMG) ---"
xcrun stapler staple "${DMG}"
xcrun stapler validate "${DMG}"

echo "--- spctl --assess --type install (Gatekeeper, DMG) ---"
# For a DISK IMAGE the correct assessment type is `install`, NOT `open`:
# `spctl -a -t open` returns a spurious 'rejected / source=Insufficient Context'
# for notarized DMGs, whereas `-t install` reports the true verdict
# ('accepted / source=Notarized Developer ID'). The stapled ticket (validated
# above) is the authoritative proof; this is the Gatekeeper cross-check.
if spctl -a -t install --verbose=4 "${DMG}" 2>&1; then
  echo "  spctl: accepted (notarized + stapled DMG)."
else
  echo "error: spctl (-t install) rejects the DMG after stapling — refusing to claim success." >&2
  exit 1
fi

# --- Checksum ---------------------------------------------------------------
( cd "${RELEASE_DIR}" && shasum -a 256 "$(basename "${DMG}")" | tee -a SHA256SUMS )

echo ""
echo "=== package_dmg_macos: DONE ==="
echo "artifact : ${DMG}"
echo "size     : $(du -h "${DMG}" | cut -f1)"
echo "layout   : Finder styling = ${STYLE_OK} (drag-to-Applications either way)"
