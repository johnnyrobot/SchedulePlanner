#!/usr/bin/env bash
#
# build_macos.sh — build the SchedulePlanner macOS .app with PyInstaller.
#
# This wraps the exact, tested command documented in BUILD.md. Run from anywhere;
# it operates from the repo root (the parent of this script's dir).
#
# Produces: dist/SchedulePlanner.app  (a launchable, no-Python-install bundle)
#
# Prereqs (see BUILD.md): a venv with `pip install -r requirements.txt` and
# `pip install pyinstaller`. PyInstaller is build-only and not in requirements.txt.
#
# Usage:
#   ./scripts/build_macos.sh            # clean rebuild
#
set -euo pipefail

# Resolve repo root = parent of the dir containing this script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Prefer `python` (e.g. inside an activated venv); fall back to `python3`.
if command -v python >/dev/null 2>&1; then
  PY=python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  echo "error: no python or python3 found on PATH." >&2
  exit 1
fi

if ! "${PY}" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "error: PyInstaller is not installed for '${PY}'." >&2
  echo "       Activate your venv and run: ${PY} -m pip install pyinstaller" >&2
  exit 1
fi

echo "Building SchedulePlanner.app from ${REPO_ROOT} (using ${PY}) ..."

# Stage a pinned Temurin JRE under build/jre so the catalog-PDF (Local AA/AS GE)
# feature is zero-setup — no user Java install. Downloaded + checksum-verified;
# bundled into the .app below via ditto. (build/ is gitignored.)
echo "Staging bundled JRE ..."
bash "${REPO_ROOT}/scripts/fetch_jre.sh"

# --clean drops PyInstaller's cache so this is a fresh, reproducible build.
# NOTE: --add-data separator is ':' on macOS/Linux (';' on Windows).
#
# opendataloader_pdf: --collect-DATA (ships the bundled Java CLI jar), NOT
# --collect-all. The catalog-PDF feature shells out to that jar via a JVM
# subprocess, so all PyInstaller needs from the package are its pure-Python
# entrypoints (wrapper/convert_generated/runner), which the import graph picks
# up automatically from sources/pdf_loader.py. --collect-all would additionally
# force-collect the optional `hybrid_server` submodule, whose lazy
# docling/torch/fastapi imports drag ~300+ MB of unused ML deps into the bundle
# (torch alone is ~284 MB). The --exclude-module guards below make that
# regression impossible even when the build host has those packages installed.
"${PY}" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name SchedulePlanner \
  --osx-bundle-identifier com.laccd.scheduleplanner \
  --add-data 'ui.html:.' \
  --add-data 'files/lamc_data.xlsx:files' \
  --collect-all ortools \
  --collect-data opendataloader_pdf \
  --exclude-module opendataloader_pdf.hybrid_server \
  --exclude-module torch \
  --exclude-module docling \
  --exclude-module fastapi \
  --exclude-module uvicorn \
  --exclude-module yt_dlp \
  app.py

APP="dist/SchedulePlanner.app"
if [ -d "${APP}" ]; then
  # Embed the bundled JRE under Contents/Resources/jre. ditto (not PyInstaller
  # --add-data) preserves the runtime's executable bits + symlinks. The sign step
  # (scripts/sign_notarize_macos.sh) then signs its Mach-O like any other nested
  # binary, with the JVM-ready entitlements already in packaging/entitlements.mac.plist.
  echo "Embedding bundled JRE -> ${APP}/Contents/Resources/jre ..."
  ditto "${REPO_ROOT}/build/jre" "${APP}/Contents/Resources/jre"

  # Stamp the bundle version from the repo-root VERSION file (PyInstaller
  # otherwise leaves CFBundle*Version at 0.0.0). Done BEFORE signing so the
  # signature seals the corrected Info.plist. CFBundleIdentifier is set at
  # build time via --osx-bundle-identifier above.
  VERSION_STR="$(cat "${REPO_ROOT}/VERSION" 2>/dev/null || echo 0.0.0)"
  PLIST="${APP}/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString ${VERSION_STR}" "${PLIST}" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string ${VERSION_STR}" "${PLIST}"
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion ${VERSION_STR}" "${PLIST}" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string ${VERSION_STR}" "${PLIST}"
  echo "Stamped bundle version ${VERSION_STR} + identifier com.laccd.scheduleplanner."

  echo ""
  echo "Build complete: ${REPO_ROOT}/${APP}"
  echo "Launch with:    open ${APP}"
  echo "(Unsigned build — see BUILD.md 'macOS Gatekeeper bypass' for first launch.)"
else
  echo "error: expected ${APP} was not produced." >&2
  exit 1
fi
