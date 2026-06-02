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

# --clean drops PyInstaller's cache so this is a fresh, reproducible build.
# NOTE: --add-data separator is ':' on macOS/Linux (';' on Windows).
"${PY}" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name SchedulePlanner \
  --add-data 'ui.html:.' \
  --add-data 'files/lamc_data.xlsx:files' \
  --collect-all ortools \
  --collect-all opendataloader_pdf \
  app.py

APP="dist/SchedulePlanner.app"
if [ -d "${APP}" ]; then
  echo ""
  echo "Build complete: ${REPO_ROOT}/${APP}"
  echo "Launch with:    open ${APP}"
  echo "(Unsigned build — see BUILD.md 'macOS Gatekeeper bypass' for first launch.)"
else
  echo "error: expected ${APP} was not produced." >&2
  exit 1
fi
