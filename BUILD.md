# EdgeSched Desktop Build Specification

This document defines the target build for EdgeSched as a native, standalone
desktop application for macOS and Windows.

## Target Product

EdgeSched should ship as a single-user native desktop app. The app opens local
LAMC scheduling data, optionally builds a workbook from live public sources,
runs the deterministic scheduling engine, and renders results without requiring
an IT-hosted server.

The build must preserve the core engine boundary:

```text
input workbook or CSV folder -> engine.run(path) -> results dict
```

Network calls for live sources and optional AI setup stay outside the solver.
The engine remains deterministic and offline.

## Framework Decision (v1): keep pywebview; PySide6 is a non-goal for now

**Decision (m2):** v1 ships on the existing **pywebview** shell (`app.py` +
`ui.html`). A PySide6 / Qt rewrite is an explicit **non-goal** for v1 and is
**deferred**.

Rationale:

- The pywebview shell already satisfies the v1 goal: a non-technical user
  launches the app and, with one click ("Load demo data"), sees the full
  analysis on the bundled synthetic dataset — no file hunting, no CLI.
- The engine boundary (`engine.run(path) -> results dict`) is unchanged and
  remains the single source of truth, so the UI layer is replaceable later at
  low cost.
- Rewriting to PySide6 now is effort that does not move the v1 demo forward and
  risks regressions in a shell that already works.

When to revisit: **only if m5 packaging proves pywebview unviable** (for
example, if PyInstaller cannot reliably bundle the webview runtime / WebKit on
a target platform, or signing/notarization of the bundled browser runtime
blocks distribution). If that happens, the PySide6 layout and packaging guidance
later in this document become the migration target. Until then, treat the
PySide6 sections below as **future / deferred reference**, not the v1 build.

For freezing to work in both dev and frozen modes, bundled resources (`ui.html`
and `files/lamc_data.xlsx`) are resolved through a `sys._MEIPASS`-aware helper
in `app.py` (`resource_path`), falling back to the source directory in dev.

## Platform Targets

| Platform | Artifact | Build host |
|---|---|---|
| macOS 13+ | `EdgeSched.app` | macOS |
| Windows 10/11 x64 | `EdgeSched.exe` or app folder | Windows |

PyInstaller is not a cross-compiler. Build macOS artifacts on macOS and Windows
artifacts on Windows.

## Recommended Stack

| Layer | Choice | Reason |
|---|---|---|
| Runtime | Python 3.12 | Conservative compatibility target for the Python stack |
| Desktop UI (v1) | pywebview (`app.py` + `ui.html`) | Already meets the v1 demo goal; engine boundary keeps it swappable |
| Desktop UI (deferred) | PySide6 / Qt for Python | Native widgets with no JS bridge — revisit only if m5 packaging proves pywebview unviable |
| Packaging | PyInstaller | Mature Python desktop bundling for macOS and Windows |
| Solver | OR-Tools CP-SAT | Existing deterministic scheduling engine |
| Data IO | pandas + openpyxl | Existing workbook/CSV ingestion |
| Live sources | httpx sync clients | Simple desktop/CLI fetch path |
| Optional AI | Ollama on `localhost:11434` | Keeps model use local and opt-in |

Do not make the final desktop build depend on Electron, Tauri, a local FastAPI
server, or a browser runtime. Those add extra process and packaging boundaries
around a Python-native engine.

## Source Layout

Target layout:

```text
edgesched/
  engine.py                  # deterministic planner; no UI/network coupling
  llm_assist.py              # optional Ollama integration
  sources/                   # live source clients and mapping
    __init__.py
    http.py
    schedule.py
    program_mapper.py
    mapping.py
  desktop/                   # PySide6 app layer
    __init__.py
    main.py                  # app entry point for packaging
    main_window.py           # primary window and actions
    workers.py               # QThread/QRunnable wrappers for engine/source work
    result_models.py         # Qt models/adapters for result display
  build_live_workbook.py     # CLI smoke path: fetch -> workbook -> engine.run()
  files/                     # committed synthetic demo data only
  data/                      # ignored local/live generated outputs
```

For v1, the desktop app **is** the `app.py` / `ui.html` pywebview shell (see
"Framework Decision (v1)" above). The `desktop/` PySide6 layout is the deferred
migration target and is only adopted if m5 packaging proves pywebview unviable.

## Python Dependencies

Runtime dependencies:

```text
pandas>=2.0
openpyxl>=3.1
ortools>=9.8
PySide6>=6.7
httpx>=0.27
```

Build/test dependencies:

```text
pyinstaller>=6.0
pytest>=8.0
```

Optional external tools:

```text
ollama                 # optional local AI runtime, installed separately
neo4j                  # optional graph/debug workflow, not part of desktop app
```

Keep Ollama outside the packaged app for v1. The desktop app may detect it and
offer setup guidance, but the binary should run fully without it.

## Environment Setup

Use a clean virtual environment per platform:

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # macOS
# .venv\Scripts\activate         # Windows PowerShell

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller pytest
```

Once the PySide migration is implemented, `requirements.txt` should include
`PySide6` and `httpx`.

## Development Run

v1 desktop app (pywebview shell):

```bash
python app.py
```

This opens the window with **Choose data file** and **Load demo data** buttons.
Clicking **Load demo data** runs the analysis on the bundled synthetic workbook
(`files/lamc_data.xlsx`) with no file picking.

Deferred PySide6 app (only if m5 packaging proves pywebview unviable):

```bash
python -m desktop.main
```

Live-source workbook smoke path:

```bash
python build_live_workbook.py \
  --campus LAMC \
  --terms 2264,2266,2268 \
  --program "Computer Science" \
  --out data/live_LAMC.xlsx
```

The smoke path should write an engine-compatible workbook, call
`engine.run("data/live_LAMC.xlsx")`, and print the known limitations:

- fill/waitlist detectors are inert when live APIs cannot provide enrollment
  counts
- prerequisite ordering is incomplete until eLumen/source prereqs are added
- live terms are fewer than the ideal eight-term historical window

## Packaging Commands

### macOS

```bash
python -m PyInstaller \
  --noconfirm \
  --windowed \
  --name EdgeSched \
  --collect-all ortools \
  --collect-all PySide6 \
  desktop/main.py
```

Expected output:

```text
dist/EdgeSched.app
```

For internal testing, an unsigned `.app` is enough. For distribution outside the
developer machine, add Apple Developer ID signing and notarization.

### Windows

Run from a Windows machine:

```powershell
python -m PyInstaller `
  --noconfirm `
  --windowed `
  --name EdgeSched `
  --collect-all ortools `
  --collect-all PySide6 `
  desktop/main.py
```

Expected output:

```text
dist\EdgeSched\EdgeSched.exe
```

Prefer one-folder builds first. Use `--onefile` only after the one-folder build
is stable; one-file apps start slower and make native-library debugging harder.

## Bundled vs. External Assets

Bundled:

- application code
- PySide6 UI layer
- deterministic engine
- pandas/openpyxl/OR-Tools runtime dependencies
- synthetic demo data if intentionally committed

Not bundled:

- student-level data
- local generated workbooks under `data/`
- raw live-source responses under `data/raw/`
- Ollama model weights
- Neo4j database/runtime

## Privacy and Network Rules

The desktop app must run offline once the user has a local workbook. It should
not send telemetry, analytics, crash reports, or scheduling data to external
services.

Allowed network paths:

- optional public API fetches in `build_live_workbook.py` / `sources/*`
- optional local Ollama calls to `http://localhost:11434`

Disallowed in the engine:

- live HTTP calls
- external AI calls
- persistence side effects
- student-level data processing

## Verification Before Release

Run these checks on each platform before publishing an artifact:

```bash
python -m pytest
python engine.py files/lamc_data.xlsx
python build_live_workbook.py --help
python -m PyInstaller --clean --noconfirm --windowed \
  --name EdgeSched \
  --collect-all ortools \
  --collect-all PySide6 \
  desktop/main.py
```

Manual smoke checklist:

- app launches without a terminal window
- file picker accepts `.xlsx` workbook input
- synthetic workbook analysis completes
- results render for full-time and part-time cohorts
- missing Ollama does not block analysis
- live-source workbook generation clearly labels known data gaps
- app exits cleanly

## Release Artifacts

Recommended release structure:

```text
release/
  macos/
    EdgeSched.app
    EdgeSched-macOS-readme.txt
  windows/
    EdgeSched/
      EdgeSched.exe
      ...
    EdgeSched-Windows-readme.txt
```

Each release note should state:

- platform and architecture
- Python version used for build
- whether the app is signed/notarized
- whether Ollama is optional
- which demo data, if any, is bundled
- known limitations for live-source data

## Known Build Risks

- OR-Tools native libraries may require `--collect-all ortools`.
- PySide6 plugins may require `--collect-all PySide6` or a maintained
  `.spec` file if PyInstaller misses platform plugins.
- macOS distribution outside the developer machine requires signing and
  notarization.
- Windows unsigned executables may trigger SmartScreen warnings.
- For v1 the pywebview shell is the desktop target; PyInstaller must bundle the
  webview runtime and the `files/`/`ui.html` data resources. If that proves
  unviable, fall back to the deferred PySide6 target (see "Framework Decision").

## Reference Documentation

- Qt for Python / PySide6: https://doc.qt.io/qtforpython-6/
- Qt for Python deployment: https://doc.qt.io/qtforpython-6/deployment/
- PyInstaller manual: https://pyinstaller.org/en/stable/
- OR-Tools install docs: https://developers.google.com/optimization/install
