#!/usr/bin/env bash
#
# build_linux.sh — build the SchedulePlanner Linux binary with PyInstaller.
#
# RUN THIS ON LINUX. PyInstaller is NOT a cross-compiler: the Linux artifact can
# only be produced and verified on a Linux host. This script was authored (but
# NOT executed) on the macOS dev host, so treat it as the documented build
# recipe to run on Linux, not something verified here.
#
# Mirror of scripts/build_macos.sh, with the Linux differences:
#   1. The PyInstaller --add-data separator is ':' on Linux (same as macOS;
#      ';' is Windows-only) — kept explicit here for symmetry with the docs.
#   2. pywebview uses a GTK or Qt backend on Linux (macOS uses the system
#      WKWebView). You must install a webview backend + its system libs FIRST,
#      or pywebview raises at startup. Pick ONE:
#        GTK:  apt install gir1.2-webkit2-4.1 python3-gi  &&  pip install pywebview[gtk]
#        Qt :  apt install python3-pyqt5.qtwebengine       &&  pip install pywebview[qt]
#      (Package names vary by distro; the above are Debian/Ubuntu examples.)
#
# Prereqs:
#   python3 -m venv .venv
#   source .venv/bin/activate
#   python -m pip install --upgrade pip
#   python -m pip install -r requirements.txt
#   python -m pip install pyinstaller
#   # plus a pywebview backend (see note 2 above)
#
# Usage:
#   ./scripts/build_linux.sh            # clean rebuild
#
# Produces: dist/SchedulePlanner/SchedulePlanner (one-dir bundle).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Prefer `python` (activated venv); fall back to `python3`.
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

echo "Building SchedulePlanner (Linux) from ${REPO_ROOT} (using ${PY}) ..."

# NOTE: --add-data separator is ':' on Linux/macOS (';' on Windows).
# pywebview needs a GTK or Qt backend installed on this host (see header note 2).
"${PY}" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name SchedulePlanner \
  --add-data 'ui.html:.' \
  --add-data 'files/lamc_data.xlsx:files' \
  --collect-all ortools \
  app.py

EXE="dist/SchedulePlanner/SchedulePlanner"
if [ -f "${EXE}" ]; then
  echo ""
  echo "Build complete: ${REPO_ROOT}/${EXE}"
  echo "Verify resources with: ./scripts/verify_build_resources.sh dist/SchedulePlanner"
  echo "Then complete the MANUAL GUI checklist in BUILD.md."
else
  echo "error: expected ${EXE} was not produced." >&2
  exit 1
fi
