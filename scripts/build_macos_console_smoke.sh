#!/usr/bin/env bash
#
# build_macos_console_smoke.sh — frozen native-stack SMOKE HARNESS (not shipped).
#
# WHY THIS EXISTS
#   The shipped app is built with `--windowed` (scripts/build_macos.sh), which
#   produces a GUI .app with NO console entry point you can drive headlessly.
#   So you cannot prove, from a script, that the *frozen* native stack
#   (OR-Tools' native libs + pandas/openpyxl reading the bundled .xlsx) actually
#   loads and runs end to end inside a PyInstaller bundle.
#
#   This script builds a SEPARATE, small `--console` exe whose entry:
#     1. `from ortools.sat.python import cp_model`  (loads the OR-Tools native libs)
#     2. `import pandas`                            (the Excel reader stack)
#     3. `engine.run(<bundled demo xlsx>)`          (the real solver on real data)
#   and prints a one-line PASS/FAIL. If the frozen ortools dylib or the bundled
#   workbook is broken, this exe fails loudly — independent of any GUI.
#
#   This is a TEST HARNESS, NOT the product. It uses the SAME high-risk flags as
#   the shipped build (`--collect-all ortools`, `--add-data files/...`) so a green
#   run here is evidence the shipped `--windowed` build's native stack is sound.
#
# Prereqs: a venv with `pip install -r requirements.txt` + `pip install pyinstaller`
#          (same as scripts/build_macos.sh / BUILD.md).
#
# Usage:
#   ./scripts/build_macos_console_smoke.sh           # build the console exe
#   ./scripts/build_macos_console_smoke.sh --run     # build, then run it and assert PASS
#
# Output: dist/SchedulePlannerSmoke (a console binary; gitignored under dist/).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

RUN_AFTER=0
if [ "${1:-}" = "--run" ]; then
  RUN_AFTER=1
fi

# Prefer `python` (activated venv); fall back to `python3`.
if command -v python >/dev/null 2>&1; then
  PY=python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  echo "error: no python or python3 on PATH." >&2
  exit 1
fi

if ! "${PY}" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "error: PyInstaller is not installed for '${PY}'." >&2
  echo "       Activate your venv and run: ${PY} -m pip install pyinstaller" >&2
  exit 1
fi

# The smoke entry point. Written to a temp file so it never lands in the repo.
ENTRY="$(mktemp -t schedplanner_smoke_XXXX.py)"
trap 'rm -f "${ENTRY}"' EXIT

cat > "${ENTRY}" <<'PYEOF'
"""Frozen native-stack smoke entry (built with --console by the harness)."""
import os
import sys


def main():
    # 1. OR-Tools native libs must load when frozen.
    from ortools.sat.python import cp_model  # noqa: F401
    # 2. pandas / openpyxl Excel stack must be present.
    import pandas as pd  # noqa: F401
    # 3. the real engine must run on the bundled demo workbook.
    import engine

    # Resolve the bundled demo workbook: under sys._MEIPASS when frozen,
    # next to engine.py otherwise.
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(engine.__file__)))
    xlsx = os.path.join(base, "files", "lamc_data.xlsx")
    if not os.path.exists(xlsx):
        print(f"SMOKE FAIL: bundled demo workbook missing at {xlsx}")
        return 1

    rows = len(pd.read_excel(xlsx))
    results = engine.run(xlsx)

    # Assert the result shape engine.run() promises.
    for key in ("terms_in_data", "analysis", "programs"):
        if key not in results:
            print(f"SMOKE FAIL: engine.run() result missing key {key!r}")
            return 1
    if not results["programs"]:
        print("SMOKE FAIL: engine.run() returned no programs")
        return 1

    print(f"SMOKE PASS: cp_model imported; read {rows} rows from bundled xlsx; "
          f"engine.run() returned {len(results['programs'])} program(s), "
          f"{results['terms_in_data']} term(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PYEOF

echo "Building console smoke exe from ${REPO_ROOT} (using ${PY}) ..."
echo "(This is a smoke HARNESS, not the shipped app — see header.)"

# Mirror the shipped build's high-risk flags, but --console (not --windowed),
# --onefile for a single drivable binary, and a distinct name so it never
# collides with dist/SchedulePlanner.app.
# NOTE: --add-data separator is ':' on macOS/Linux (';' on Windows).
"${PY}" -m PyInstaller \
  --noconfirm \
  --clean \
  --console \
  --onefile \
  --name SchedulePlannerSmoke \
  --add-data 'files/lamc_data.xlsx:files' \
  --collect-all ortools \
  "${ENTRY}"

EXE="dist/SchedulePlannerSmoke"
if [ ! -f "${EXE}" ]; then
  echo "error: expected ${EXE} was not produced." >&2
  exit 1
fi
echo "Built: ${REPO_ROOT}/${EXE}"

if [ "${RUN_AFTER}" -eq 1 ]; then
  echo "--- running frozen smoke exe ---"
  out="$("${EXE}")"
  echo "${out}"
  if echo "${out}" | grep -q '^SMOKE PASS:'; then
    echo "=== console smoke: PASS ==="
  else
    echo "=== console smoke: FAIL ===" >&2
    exit 1
  fi
else
  echo "Run it with: ${EXE}   (or re-run this script with --run)"
fi
