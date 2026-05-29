# SchedulePlanner Desktop Build (macOS first)

This document defines how to build the v1 desktop app as a single launchable
macOS `.app` bundle that runs the bundled demo with **no Python install** on the
target machine.

The v1 desktop app **is** the pywebview shell: entry `app.py` + UI `ui.html`,
with the bundled synthetic demo workbook at `files/lamc_data.xlsx`. The
deterministic OR-Tools engine (`engine.py`) and the optional Ollama AI layer
(`llm_assist.py`) sit behind it.

> The exact PyInstaller command below has been **run and verified** on this repo
> (PyInstaller 6.20.0, Python 3.13, ortools 9.15, pandas 3.0, macOS arm64). It
> produces `dist/SchedulePlanner.app`. See "Verification" for what was confirmed
> headlessly and what must be checked manually.

## Target product

| Item | Value |
|---|---|
| Platform | macOS 13+ (built/verified on arm64) |
| Artifact | `dist/SchedulePlanner.app` (a one-bundle `.app`) |
| Entry point | `app.py` (pywebview) |
| UI | `ui.html` (loaded via `resource_path`, `sys._MEIPASS`-aware) |
| Bundled demo | `files/lamc_data.xlsx` |
| Python on target | **not required** — bundled by PyInstaller |
| Ollama | optional, external; the app runs fully without it |

PyInstaller is not a cross-compiler: build the macOS artifact on macOS. Windows
is a later target (see "Windows (later)").

## Prerequisites

A clean virtual environment with the runtime deps plus PyInstaller:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller
```

`requirements.txt` already pins the runtime stack (pandas, openpyxl, ortools,
pywebview, httpx) plus `pytest` for the test suite. PyInstaller is the only
build tool intentionally excluded from `requirements.txt` (install it
separately, as above).

## The build command (tested)

Run from the repo root:

```bash
python -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name SchedulePlanner \
  --add-data 'ui.html:.' \
  --add-data 'files/lamc_data.xlsx:files' \
  --collect-all ortools \
  app.py
```

`--clean` forces a fresh, reproducible rebuild by dropping PyInstaller's cache
(costs ~a minute); this matches what `scripts/build_macos.sh` does.

Output:

```text
dist/SchedulePlanner.app          <- the launchable bundle (open this)
dist/SchedulePlanner/             <- the equivalent one-dir tree (same binary)
build/                            <- intermediate build cache (gitignored)
```

`build/`, `dist/`, and any generated `*.spec` are gitignored — they are
artifacts, not source. Do **not** commit them. This file (and
`scripts/build_macos.sh`) are the source of truth for the build; there is no
committed `.spec`.

### What each flag does and why it is required

| Flag | Why it is needed |
|---|---|
| `--noconfirm` | Suppresses the interactive "output directory already exists, overwrite?" prompt so rebuilds run unattended. |
| `--clean` | Drops PyInstaller's build cache before building for a fresh, reproducible result. |
| `--windowed` | No terminal/console window; produces a real `.app` GUI bundle. |
| `--name SchedulePlanner` | Names the bundle `SchedulePlanner.app`. |
| `--add-data 'ui.html:.'` | Ships `ui.html` at the bundle root so `resource_path("ui.html")` resolves under `sys._MEIPASS` when frozen. **Without this the window has no UI to load.** |
| `--add-data 'files/lamc_data.xlsx:files'` | Ships the demo workbook under `files/` so `resource_path("files", "lamc_data.xlsx")` resolves. **Without this "Load demo data" fails with "File not found."** |
| `--collect-all ortools` | Pulls in OR-Tools' native libraries (`libortools.9.dylib` + the bundled `absl` `.dylib`s under `ortools/.libs/`) plus all submodules and data. **This is the documented high-risk item** — without it the frozen app raises an import/dyld error the moment the solver is touched. |

> `--add-data` separator is **`:`** on macOS/Linux and **`;`** on Windows.

### Hidden imports / extra flags that turned out NOT to be needed

These are commonly required for similar apps; on this toolchain version they were
**verified unnecessary**, so the command above is intentionally minimal. If a
future dependency bump breaks the build, these are the first things to add:

- **openpyxl** — *not* needed as an explicit `--hidden-import`. pandas reads
  `.xlsx` through openpyxl, which pandas imports lazily (so static analysis can
  miss it), **but** the bundled PyInstaller pandas hook
  (`pyinstaller-hooks-contrib`) already collects the full `openpyxl` package into
  the archive. Verified: the frozen build reads the demo workbook successfully.
  If a future pandas/openpyxl combo regresses, add:
  `--hidden-import openpyxl --collect-submodules openpyxl`.
- **pandas / numpy** — handled by their PyInstaller hooks; no extra flags. (The
  build log shows ~1200 benign "missing module named numpy._core.*" warnings —
  those are numpy lazy-attribute false positives, not real gaps.)
- **pywebview (macOS)** — the Cocoa backend (`webview/platforms/cocoa.py`) and
  its `pyobjc` / `WebKit` bridge are collected automatically. macOS uses the
  **system WKWebView**, so no separate browser runtime is bundled. (This is why
  the m2 framework decision to keep pywebview is viable for packaging — there is
  no third-party WebKit blob to sign.)
- **llm_assist** — stdlib-only (`json`, `shutil`, `subprocess`,
  `urllib.request`); nothing to collect. Ollama stays external.

## Optional helper script

`scripts/build_macos.sh` wraps the exact command above (clean rebuild). Run:

```bash
./scripts/build_macos.sh
```

## Verification

### Verified headlessly (automated, reproducible)

1. The PyInstaller build **completes with exit 0** and prints
   `Build complete! ... dist`. No missing-ortools error.
2. `dist/SchedulePlanner.app` exists and is a proper bundle; its executable
   `Contents/MacOS/SchedulePlanner` is a valid `Mach-O arm64` binary.
3. Bundled resources are present somewhere inside `dist/SchedulePlanner.app`
   (locate them with `find` — the exact sub-path is a PyInstaller `--windowed`
   layout detail and may differ between versions, so do not assert it):
   - `ui.html`
   - `files/lamc_data.xlsx`
   - `libortools.*.dylib` (plus the bundled `ortools/.libs/*.dylib`)
   - the pywebview Cocoa backend + `WebKit`/`objc` bridge
4. **Frozen native-stack smoke test.** A console PyInstaller build using the
   *same* `--collect-all ortools` + `--add-data files` flags was run and
   executed end to end inside the frozen bundle:
   `from ortools.sat.python import cp_model` imported, `pandas.read_excel` read
   the bundled workbook via openpyxl (620 rows), and `engine.run(<bundled
   xlsx>)` returned the expected result keys. This confirms the OR-Tools native
   libraries load and the Excel path works when frozen — independent of the GUI.

To re-run checks 1–3 after a build:

```bash
test -d dist/SchedulePlanner.app && echo "bundle OK"
find dist/SchedulePlanner.app -name ui.html
find dist/SchedulePlanner.app -name lamc_data.xlsx
find dist/SchedulePlanner.app -name 'libortools*.dylib'
file dist/SchedulePlanner.app/Contents/MacOS/SchedulePlanner
```

### Manual GUI checklist (cannot be automated headlessly)

Launching the windowed app to click buttons requires an interactive macOS
session. On a Mac with a desktop session:

```bash
open dist/SchedulePlanner.app
# or, to see logs/errors in a terminal:
./dist/SchedulePlanner.app/Contents/MacOS/SchedulePlanner
```

Then confirm:

- [ ] The app launches with **no terminal window** and shows the native window.
- [ ] `ui.html` renders (buttons **Choose data file** and **Load demo data**).
- [ ] **Load demo data** runs analysis on the bundled workbook and renders
      results for full-time and part-time cohorts — with **no file picking**.
- [ ] **Choose data file** opens the macOS file picker and accepts an `.xlsx`.
- [ ] Missing Ollama does **not** block analysis (AI is optional; results still
      render, just without the AI explanation).
- [ ] The app exits cleanly.

## macOS Gatekeeper bypass (internal testers)

The bundle is **unsigned and un-notarized**. macOS Gatekeeper will block it on
first open ("...can't be opened because Apple cannot check it for malicious
software" / "is damaged"). For **internal testers only**, bypass it one of these
ways:

- **Finder:** right-click (or Control-click) the app -> **Open** -> confirm
  **Open** in the dialog. This whitelists that specific copy.
- **System Settings:** after a blocked launch, go to **System Settings ->
  Privacy & Security**, scroll to the message about SchedulePlanner, click
  **Open Anyway**.
- **Terminal (clears the quarantine attribute):**

  ```bash
  xattr -dr com.apple.quarantine /path/to/SchedulePlanner.app
  ```

For real distribution outside the dev machine, the proper fix is an Apple
Developer ID signature + notarization (`codesign` + `notarytool`); that is out
of scope for internal v1 testing.

## Where live-fetch outputs go (as shipped in `app.py`)

A **frozen `.app` has no writable working directory** you can rely on:

- `sys._MEIPASS` is a temporary, read-only extraction dir — never write there.
- The app's CWD when launched from Finder is `/` (not the app folder), and the
  bundle itself should be treated as read-only (it may live in `/Applications`,
  and writing into it breaks the code signature).

The **Build-from-live-LACCD** feature in `app.py` (`Api.fetch_live`) honors this
by writing the intermediate workbook into a throwaway
`tempfile.TemporaryDirectory()` and handing that path straight to
`engine.run()`. The temp dir is removed when the fetch returns, so **nothing is
written next to the `.app`, into `sys._MEIPASS`, or into the user's workspace** —
the live-fetch path has no persistent output and no writable-bundle dependency.
The bundled read-only demo (`files/lamc_data.xlsx`, resolved via
`resource_path`) is unaffected.

If a future feature instead needs to **persist** a generated workbook (e.g. a
"Save workbook…" action), it must NOT write into the bundle or `_MEIPASS`; route
it through a per-user writable application-support directory
(`~/Library/Application Support/SchedulePlanner/` on macOS,
`%APPDATA%\SchedulePlanner\` on Windows, `$XDG_DATA_HOME/SchedulePlanner/` on
Linux) created on demand. As of v1 no such persistent path exists — live fetch
is ephemeral by design.

## Privacy / network rules (unchanged)

The engine stays deterministic and offline: no live HTTP, no external AI calls,
no persistence side effects inside `engine.run`. The only allowed network paths
are optional public-source fetches (the live-in-UI / `sources/*` path) and
optional local Ollama calls to `http://localhost:11434`. No telemetry.

## Known build risks / notes

- **OR-Tools native libs** — the high-risk item. Resolved by
  `--collect-all ortools`; verified loading inside the frozen bundle. If a future
  ortools release relocates its `.dylib`s, re-check `--collect-all ortools` still
  bundles `libortools.*.dylib` and `ortools/.libs/`.
- **Bundle size** — the `.app` is large (~800 MB) because OR-Tools + pandas +
  numpy + WebKit bridge are all included. Expected; not a correctness issue.
- **One-file builds** — prefer the default one-bundle `.app` (above). Only try
  `--onefile` after this is stable; one-file apps start slower and make
  native-library debugging harder.
- **Gatekeeper** — see the bypass section; unsigned builds are for internal
  testers only.

## Windows / Linux (prepared, not built on the macOS host)

PyInstaller is not a cross-compiler, so the Windows and Linux artifacts must be
built **on those OSes**. The recipes, helper scripts
(`scripts/build_windows.ps1`, `scripts/build_linux.sh`), per-OS pywebview
backend requirements (WebView2 on Windows; GTK/Qt WebKit on Linux), and the
`--add-data` separator gotcha (`;` on Windows, `:` on macOS/Linux) live in
**`docs/CROSS_PLATFORM_BUILD.md`**. They are prepared but **NOT produced or
verified on this macOS dev host** — treat them as untested until run on the
target OS and checked with `scripts/verify_build_resources.sh` plus the manual
GUI checklist there.

The Windows command, for quick reference (note the `;` separator):

```powershell
python -m PyInstaller `
  --noconfirm `
  --windowed `
  --name SchedulePlanner `
  --add-data "ui.html;." `
  --add-data "files/lamc_data.xlsx;files" `
  --collect-all ortools `
  app.py
```

## See also

- `docs/CROSS_PLATFORM_BUILD.md` — Windows/Linux recipes, per-OS pywebview
  backends, and the shared `verify_build_resources.sh` resource checker.
- `docs/M8_QA_REPORT.md` — what was verified headlessly vs. what is manual-only
  (incl. the frozen native-stack smoke test and the PRD F/N coverage matrix).
- `scripts/build_macos_console_smoke.sh` — the `--console` smoke harness that
  proves the frozen OR-Tools + pandas + `engine.run` stack loads end to end.

## Reference

- PyInstaller manual: https://pyinstaller.org/en/stable/
- PyInstaller `--add-data` / data files: https://pyinstaller.org/en/stable/spec-files.html#adding-data-files
- OR-Tools install docs: https://developers.google.com/optimization/install
- pywebview: https://pywebview.flowrl.com/
