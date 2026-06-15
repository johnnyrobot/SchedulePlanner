#!/usr/bin/env bash
# fetch_jre.sh — download + checksum-verify + stage a pinned Temurin JRE into
# build/jre, so scripts/build_macos.sh can bundle it (zero-setup catalog-PDF
# parsing; the catalog feature needs a JVM — see BUILD.md / sources/pdf_loader.py).
#
# Pinned: Eclipse Temurin (Adoptium) 17.0.19+10 JRE. Network only here. Idempotent:
# re-running is a no-op once build/jre is staged and its checksum marker matches.
# build/ is gitignored, so the ~100 MB runtime never enters git.
set -euo pipefail

VERSION_TAG="jdk-17.0.19+10"
VERSION="17.0.19_10"
# sha256 of the Adoptium JRE tarballs (from api.adoptium.net assets metadata).
SHA256_aarch64="cef790b404cf168fd1a8a7abc5054fbb442c7d4bfe390cceccfe3f64b9b776a9"
SHA256_x64="91bbd07b9c65d9ecbe1fa0081b3c1ad549ed34ed21085a72fdb76598a740b54c"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${REPO_ROOT}/build/jre"
MARKER="${DEST}/.scheduleplanner-jre-sha256"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "error: fetch_jre.sh stages a macOS JRE; run it on macOS." >&2
  exit 1
fi
case "$(uname -m)" in
  arm64|aarch64) ARCH="aarch64"; SHA256="${SHA256_aarch64}" ;;
  x86_64)        ARCH="x64";     SHA256="${SHA256_x64}" ;;
  *) echo "error: unsupported arch '$(uname -m)'." >&2; exit 1 ;;
esac

# Idempotent skip when already staged for this exact checksum.
if [ -x "${DEST}/bin/java" ] && [ -f "${MARKER}" ] \
   && [ "$(cat "${MARKER}")" = "${SHA256}" ]; then
  echo "JRE already staged at ${DEST} (checksum matches) — skipping download."
  exit 0
fi

URL="https://github.com/adoptium/temurin17-binaries/releases/download/${VERSION_TAG//+/%2B}/OpenJDK17U-jre_${ARCH}_mac_hotspot_${VERSION}.tar.gz"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
TARBALL="${TMP}/jre.tar.gz"

echo "Downloading Temurin ${VERSION_TAG} JRE (${ARCH})…"
curl -fSL "${URL}" -o "${TARBALL}"

echo "Verifying sha256…"
ACTUAL="$(shasum -a 256 "${TARBALL}" | awk '{print $1}')"
if [ "${ACTUAL}" != "${SHA256}" ]; then
  echo "error: checksum mismatch for ${URL}" >&2
  echo "  expected ${SHA256}" >&2
  echo "  actual   ${ACTUAL}" >&2
  exit 1
fi

echo "Extracting + staging Contents/Home -> ${DEST}…"
tar -xzf "${TARBALL}" -C "${TMP}"
HOME_DIR="$(find "${TMP}" -maxdepth 4 -type d -path '*/Contents/Home' | head -n1)"
if [ -z "${HOME_DIR}" ] || [ ! -x "${HOME_DIR}/bin/java" ]; then
  echo "error: could not locate Contents/Home/bin/java in the tarball." >&2
  exit 1
fi
rm -rf "${DEST}"
mkdir -p "$(dirname "${DEST}")"
ditto "${HOME_DIR}" "${DEST}"          # preserves exec bits + symlinks (macOS)
printf '%s' "${SHA256}" > "${MARKER}"

echo "OK: staged ${DEST}"
"${DEST}/bin/java" -version
