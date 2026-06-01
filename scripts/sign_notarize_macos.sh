#!/usr/bin/env bash
#
# sign_notarize_macos.sh — opt-in Developer ID signing + Apple notarization for
# the SchedulePlanner macOS .app.
#
# This is a SEPARATE, OPT-IN, POST-BUILD step. It runs AFTER
# scripts/build_macos.sh has produced an (unsigned) dist/SchedulePlanner.app and
# it NEVER modifies that build script or the unsigned internal-tester path. It
# signs the bundle IN PLACE with the hardened runtime, verifies the signature,
# submits the signed bundle to Apple's notary service, staples the ticket, and
# re-verifies. Public-release order is: build -> sign+notarize (this) ->
# package -> checksum.
#
# It is fail-closed: it checks every required tool and credential up front and
# exits non-zero with an actionable list if anything is missing, prints the
# REAL tool output, and reports success ONLY when notarytool returns Accepted
# and both stapler and spctl pass. It NEVER fabricates a signature, NEVER claims
# success it did not observe, and NEVER hardcodes a credential.
#
# Credentials come ONLY from the environment (placeholders documented below);
# nothing secret is ever written into this file or printed.
#
# Required tools:   codesign, xcrun, ditto, stapler  (and spctl for assessment)
#
# Required env vars:
#   MAC_SIGN_IDENTITY      Developer ID Application identity string
#                          (e.g. the SHA-1 hash or the full
#                           "Developer ID Application: NAME (TEAMID)" name).
#                          Supplied by the operator; never hardcoded here.
#
# Notarization credentials — provide EITHER (preferred) a keychain profile:
#   MAC_NOTARY_PROFILE     name of a profile previously created with
#                          `xcrun notarytool store-credentials`
#   ...OR the fallback trio:
#   MAC_NOTARY_APPLE_ID    Apple ID email used for notarization
#   MAC_NOTARY_TEAM_ID     the 10-char Apple Developer Team ID
#   MAC_NOTARY_PASSWORD    an app-specific password (NOT the Apple ID password)
#
# Entitlements:
#   packaging/entitlements.mac.plist — the hardened-runtime entitlements TEMPLATE
#   (validate it on a signing-capable Mac; see that file's header).
#
# Usage:
#   ./scripts/sign_notarize_macos.sh                       # dist/SchedulePlanner.app
#   ./scripts/sign_notarize_macos.sh path/to/Other.app     # a given bundle
#
set -euo pipefail

# Resolve repo root = parent of the dir containing this script (works from any
# cwd), matching the other scripts in this repo.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
cd "${REPO_ROOT}"

# --- Inputs -----------------------------------------------------------------

# Optional bundle-path arg; default to the standard build output.
APP="${1:-dist/SchedulePlanner.app}"

ENTITLEMENTS="packaging/entitlements.mac.plist"
RESOURCE_CHECK="${SCRIPT_DIR}/verify_build_resources.sh"

# VERSION is the single source of truth (trimmed); fall back to 0.0.0-dev if
# absent. Used only for log/zip naming context here — packaging owns artifacts.
if [ -f "${REPO_ROOT}/VERSION" ]; then
  VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
else
  VERSION="0.0.0-dev"
fi
VERSION="${VERSION:-0.0.0-dev}"

# Where the notarization-submission zip is staged (under the gitignored dist/).
NOTARIZE_DIR="${REPO_ROOT}/dist/release"
NOTARIZE_ZIP="${NOTARIZE_DIR}/SchedulePlanner-${VERSION}-notarize-submission.zip"

# --- Fail-closed preflight: tools -------------------------------------------

missing_tools=()
for tool in codesign xcrun ditto stapler spctl; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    missing_tools+=("${tool}")
  fi
done

# notarytool ships inside the Xcode toolchain and is reached via `xcrun`; verify
# it actually resolves so we fail now (with guidance) rather than mid-submit.
if command -v xcrun >/dev/null 2>&1; then
  if ! xcrun --find notarytool >/dev/null 2>&1; then
    missing_tools+=("notarytool (via xcrun — install full Xcode or the command line tools)")
  fi
fi

if [ "${#missing_tools[@]}" -ne 0 ]; then
  echo "error: required tool(s) not available:" >&2
  for t in "${missing_tools[@]}"; do echo "  - ${t}" >&2; done
  echo "       Install Xcode (or the Command Line Tools: xcode-select --install)" >&2
  echo "       and ensure codesign/ditto/stapler/spctl are on PATH." >&2
  exit 1
fi

# --- Fail-closed preflight: inputs ------------------------------------------

if [ ! -d "${APP}" ]; then
  echo "error: app bundle not found at '${APP}'." >&2
  echo "       Build it first with: ./scripts/build_macos.sh" >&2
  echo "       (or pass an explicit bundle path as the first argument)." >&2
  exit 1
fi

if [ ! -f "${ENTITLEMENTS}" ]; then
  echo "error: entitlements plist not found at '${ENTITLEMENTS}'." >&2
  echo "       Expected the hardened-runtime template at packaging/entitlements.mac.plist." >&2
  exit 1
fi

if [ ! -f "${RESOURCE_CHECK}" ]; then
  echo "error: shared resource checker not found at '${RESOURCE_CHECK}'." >&2
  exit 1
fi

# --- Fail-closed preflight: credentials -------------------------------------

missing_creds=()

# Signing identity is always required.
if [ -z "${MAC_SIGN_IDENTITY:-}" ]; then
  missing_creds+=("MAC_SIGN_IDENTITY (Developer ID Application identity string)")
fi

# Notarization: prefer a keychain profile; otherwise require the full trio.
# Decide the mode now so a partially-specified trio fails loudly here.
NOTARY_MODE=""
if [ -n "${MAC_NOTARY_PROFILE:-}" ]; then
  NOTARY_MODE="profile"
elif [ -n "${MAC_NOTARY_APPLE_ID:-}" ] || [ -n "${MAC_NOTARY_TEAM_ID:-}" ] || [ -n "${MAC_NOTARY_PASSWORD:-}" ]; then
  # At least one trio var is set — require all three.
  NOTARY_MODE="trio"
  [ -z "${MAC_NOTARY_APPLE_ID:-}" ] && missing_creds+=("MAC_NOTARY_APPLE_ID (Apple ID email)")
  [ -z "${MAC_NOTARY_TEAM_ID:-}" ]  && missing_creds+=("MAC_NOTARY_TEAM_ID (10-char Team ID)")
  [ -z "${MAC_NOTARY_PASSWORD:-}" ] && missing_creds+=("MAC_NOTARY_PASSWORD (app-specific password)")
else
  missing_creds+=("MAC_NOTARY_PROFILE  (preferred keychain profile from 'xcrun notarytool store-credentials')")
  missing_creds+=("  ...or the trio MAC_NOTARY_APPLE_ID + MAC_NOTARY_TEAM_ID + MAC_NOTARY_PASSWORD")
fi

if [ "${#missing_creds[@]}" -ne 0 ]; then
  echo "error: missing required signing/notarization input(s):" >&2
  for c in "${missing_creds[@]}"; do echo "  - ${c}" >&2; done
  echo "" >&2
  echo "External prerequisites you must obtain from Apple first:" >&2
  echo "  * Apple Developer Program membership (paid)." >&2
  echo "  * A 'Developer ID Application' signing certificate + private key in the" >&2
  echo "    login keychain (for MAC_SIGN_IDENTITY)." >&2
  echo "  * Notary credentials: either a stored keychain profile created with" >&2
  echo "    'xcrun notarytool store-credentials', or an app-specific password" >&2
  echo "    (appleid.apple.com) plus your Apple ID and Team ID." >&2
  echo "" >&2
  echo "Set these as environment variables (never hardcode them) and re-run." >&2
  exit 1
fi

# --- Pre-sign gate: required runtime resources must be bundled --------------

echo "=== sign_notarize_macos: ${APP} (version ${VERSION}) ==="
echo "--- pre-sign resource gate (shared, OS-agnostic) ---"
if ! bash "${RESOURCE_CHECK}" "${APP}"; then
  echo "error: resource gate failed for '${APP}' — refusing to sign an incomplete bundle." >&2
  echo "       Rebuild with ./scripts/build_macos.sh and re-run." >&2
  exit 1
fi

# --- Codesign: inner-most code first, then the bundle (no --deep) -----------
#
# Apple discourages `codesign --deep` for SIGNING. The correct order is to sign
# the most deeply nested Mach-O code first (dylibs, .so files, nested
# frameworks' and helpers' executables), then sign the outer .app last so its
# seal covers already-signed inner code. PyInstaller bundles many native
# libraries (OR-Tools .dylibs, the pyobjc/WebKit bridge), so we walk and sign
# them explicitly. The hardened runtime (--options runtime), a secure timestamp
# (--timestamp), and our entitlements are applied throughout; --force re-signs
# anything already (ad-hoc) signed by PyInstaller.

SIGN_FLAGS=(
  --force
  --options runtime
  --timestamp
  --entitlements "${ENTITLEMENTS}"
  --sign "${MAC_SIGN_IDENTITY}"
)

echo "--- collecting nested code (inner-most first) ---"
# Collect nested Mach-O code: dynamic libs and any nested bundle executables.
# NUL-delimited from find to survive spaces in paths. We read into a bash array
# and then depth-sort by counting path separators, deepest first, so the most
# deeply nested items are signed before their containers.
#
# IMPORTANT: we deliberately do NOT pipe through `awk -v RS='\0'` to sort by
# depth — macOS ships BSD awk (/usr/bin/awk), which does NOT honor a NUL record
# separator and would collapse the whole stream into a single record (dropping
# every nested item but the first). The bash-array + slash-count approach below
# is portable across BSD/GNU tools.
nested_items=()
# Sign EVERY Mach-O file, identified by CONTENT (not name or location). A
# name/extension/location filter (*.dylib/*.so + framework Contents/MacOS) MISSES:
#   - framework binaries that live at Versions/<v>/<Name>, e.g.
#     Python.framework/Versions/3.13/Python (not under Contents/MacOS);
#   - bare extension-less Mach-O files, e.g. Frameworks/Tcl, Frameworks/Tk;
#   - CLI executables in bin/ dirs, e.g. torch/bin/protoc, torch/bin/torch_shm_manager.
# Apple notarization rejects ANY unsigned/adhoc Mach-O ("not signed with a valid
# Developer ID", "no secure timestamp", "no hardened runtime"), which is exactly
# how the first submission failed. `file` is the portable content check — slower
# across the whole bundle, but correct (the top-level .app binary is sealed when
# we sign the outer bundle last, so we exclude it here).
while IFS= read -r -d '' f; do
  case "$(file -b "${f}" 2>/dev/null)" in
    *Mach-O*) nested_items+=("${f}") ;;
  esac
done < <(find "${APP}" -type f -print0)

# Depth-sort deepest-first by counting '/' separators in each path. We use a
# newline-delimited intermediate here; PyInstaller bundle paths do not contain
# newlines, and any that did would simply sort imperfectly (not be dropped).
nested_sorted=()
if [ "${#nested_items[@]}" -ne 0 ]; then
  while IFS= read -r line; do
    nested_sorted+=("${line#*$'\t'}")
  done < <(
    for f in "${nested_items[@]}"; do
      slashes="${f//[!\/]/}"
      printf '%d\t%s\n' "${#slashes}" "${f}"
    done | sort -rn -k1
  )
fi

echo "--- signing nested code (inner-most first) ---"
nested_signed=0
for macho in "${nested_sorted[@]}"; do
  echo "  codesign: ${macho#"${APP}"/}"
  codesign "${SIGN_FLAGS[@]}" "${macho}"
  nested_signed=$((nested_signed + 1))
done
echo "  (signed ${nested_signed} nested Mach-O item(s))"

echo "--- signing the .app bundle (outermost, last) ---"
codesign "${SIGN_FLAGS[@]}" "${APP}"

# --- Verify the signature ----------------------------------------------------

echo "--- codesign --verify --strict ---"
codesign --verify --strict --verbose=2 "${APP}"

echo "--- codesign -dv --verbose=4 (signature details) ---"
# Report what was actually applied. codesign writes this report to stderr.
codesign -dv --verbose=4 "${APP}" 2>&1

echo "--- spctl --assess --type execute (Gatekeeper assessment) ---"
# Pre-notarization spctl will typically still reject (no stapled ticket yet);
# print the real result but do not treat a pre-notarization rejection as fatal.
if spctl --assess --type execute --verbose=4 "${APP}" 2>&1; then
  echo "  spctl: accepted at this stage."
else
  echo "  spctl: not yet accepted (expected before notarization+stapling)."
fi

# --- Zip for notarization submission (ditto -c -k --keepParent) -------------

mkdir -p "${NOTARIZE_DIR}"
rm -f "${NOTARIZE_ZIP}"
echo "--- packaging signed bundle for notarization ---"
# ditto with --keepParent preserves the .app wrapper, its symlinks, and the
# embedded signature inside the zip submitted to Apple.
ditto -c -k --keepParent "${APP}" "${NOTARIZE_ZIP}"
echo "  staged: ${NOTARIZE_ZIP#"${REPO_ROOT}"/}"

# --- Submit to Apple notary service (--wait) --------------------------------

echo "--- xcrun notarytool submit --wait ---"
NOTARY_LOG="${NOTARIZE_DIR}/notarytool-submit.log"
set +e
if [ "${NOTARY_MODE}" = "profile" ]; then
  echo "  using keychain profile: MAC_NOTARY_PROFILE"
  xcrun notarytool submit "${NOTARIZE_ZIP}" \
    --keychain-profile "${MAC_NOTARY_PROFILE}" \
    --wait 2>&1 | tee "${NOTARY_LOG}"
else
  echo "  using Apple ID + Team ID + app-specific password (from env)"
  xcrun notarytool submit "${NOTARIZE_ZIP}" \
    --apple-id "${MAC_NOTARY_APPLE_ID}" \
    --team-id "${MAC_NOTARY_TEAM_ID}" \
    --password "${MAC_NOTARY_PASSWORD}" \
    --wait 2>&1 | tee "${NOTARY_LOG}"
fi
NOTARY_STATUS="${PIPESTATUS[0]}"
set -e

if [ "${NOTARY_STATUS}" -ne 0 ]; then
  echo "error: notarytool submit failed (exit ${NOTARY_STATUS}). See output above and ${NOTARY_LOG#"${REPO_ROOT}"/}." >&2
  echo "       If 'Invalid' was reported, fetch the detailed log with:" >&2
  echo "         xcrun notarytool log <submission-id> [--keychain-profile <name> | --apple-id ... --team-id ... --password ...]" >&2
  exit 1
fi

# notarytool --wait exits 0 even when the final status is Invalid/Rejected, so
# we must confirm the status line actually says Accepted before claiming success.
if ! grep -Eq 'status:[[:space:]]*Accepted' "${NOTARY_LOG}"; then
  echo "error: notarization did NOT return 'Accepted' — refusing to claim success." >&2
  echo "       Review the status above and pull the detailed log via 'xcrun notarytool log'." >&2
  exit 1
fi
echo "  notarization status: Accepted"

# --- Staple the ticket and re-verify ----------------------------------------

echo "--- xcrun stapler staple ---"
xcrun stapler staple "${APP}"

echo "--- xcrun stapler validate ---"
xcrun stapler validate "${APP}"

echo "--- post-staple codesign --verify --strict ---"
codesign --verify --strict --verbose=2 "${APP}"

echo "--- post-staple spctl --assess --type execute ---"
if spctl --assess --type execute --verbose=4 "${APP}" 2>&1; then
  echo "  spctl: accepted (notarized + stapled)."
else
  echo "error: spctl still rejects the bundle after stapling — refusing to claim success." >&2
  exit 1
fi

echo ""
echo "=== RESULT: ${APP} is SIGNED (Developer ID, hardened runtime), NOTARIZED (Accepted), and STAPLED ==="
echo "(Notarization submission zip: ${NOTARIZE_ZIP#"${REPO_ROOT}"/} — the packaging step re-zips the stapled .app for release.)"
