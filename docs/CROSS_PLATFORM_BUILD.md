# Cross-Platform Build (macOS / Windows / Linux)

SchedulePlanner is a PyInstaller bundle of the pywebview shell (`app.py` +
`ui.html`) over the deterministic OR-Tools engine (`engine.py`), shipping the
demo workbook `files/lamc_data.xlsx`.

> **PyInstaller is not a cross-compiler.** Each OS artifact must be built **on
> that OS**. macOS is the only platform built and verified on this dev host
> (see `BUILD.md`). The **Windows and Linux recipes below are documented but
> NOT produced or verified on the macOS dev host** — they must be run on a
> Windows / Linux machine respectively, then checked with the resource verifier
> and the manual GUI checklist there.

## The one gotcha that differs per OS: `--add-data` separator

PyInstaller's `--add-data SRC<SEP>DEST` uses a different `<SEP>` per platform:

| OS | `--add-data` separator | Example |
|---|---|---|
| macOS | `:` | `--add-data 'ui.html:.'` |
| Linux | `:` | `--add-data 'ui.html:.'` |
| Windows | `;` | `--add-data "ui.html;."` |

Using the wrong separator silently fails to bundle the file, so `ui.html` /
the demo workbook go missing at runtime. This is the single most common
cross-platform build mistake.

## pywebview backend per OS

`app.py` is a pywebview window. pywebview uses the platform's native web view,
so each OS needs a different backend (and, off macOS, extra system packages):

| OS | pywebview backend | Runtime requirement on the target machine |
|---|---|---|
| macOS | Cocoa / WebKit (system **WKWebView**) | none — built into macOS; nothing extra to bundle or sign |
| Windows | EdgeChromium / **WebView2** | **WebView2 Runtime** (ships with Win11 & current Win10; else install the Evergreen WebView2 Runtime). Without it the window stays blank. |
| Linux | **GTK** (WebKit2GTK) or **Qt** (QtWebEngine) | install ONE backend + its system libs before building (see below) |

### Linux backend install (pick ONE; Debian/Ubuntu names shown)

```bash
# GTK backend
sudo apt install gir1.2-webkit2-4.1 python3-gi
python -m pip install 'pywebview[gtk]'

# OR Qt backend
sudo apt install python3-pyqt5.qtwebengine
python -m pip install 'pywebview[qt]'
```

Package names vary by distro. On Linux, pywebview raises at startup if no
backend is installed.

## Per-OS build command

All three use the same flags as the verified macOS build (`--windowed`,
`--collect-all ortools`, the two `--add-data` resources); only the separator and
the helper script differ.

### macOS (built + verified — see BUILD.md)

```bash
./scripts/build_macos.sh
# -> dist/SchedulePlanner.app
```

### Windows (run on Windows — NOT verified on the macOS dev host)

```powershell
.\scripts\build_windows.ps1
# -> dist\SchedulePlanner\SchedulePlanner.exe
```

Raw command (note `;` separator):

```powershell
python -m PyInstaller `
  --noconfirm --clean --windowed `
  --name SchedulePlanner `
  --add-data "ui.html;." `
  --add-data "files/lamc_data.xlsx;files" `
  --collect-all ortools `
  app.py
```

### Linux (run on Linux — NOT verified on the macOS dev host)

```bash
./scripts/build_linux.sh
# -> dist/SchedulePlanner/SchedulePlanner
```

Raw command (note `:` separator):

```bash
python -m PyInstaller \
  --noconfirm --clean --windowed \
  --name SchedulePlanner \
  --add-data 'ui.html:.' \
  --add-data 'files/lamc_data.xlsx:files' \
  --collect-all ortools \
  app.py
```

## Verify bundled resources (OS-agnostic, headless)

After any build, confirm the three runtime resources were bundled with the
shared checker (runs anywhere bash + `find` exist, incl. Git Bash / WSL):

```bash
./scripts/verify_build_resources.sh dist/SchedulePlanner.app   # macOS bundle
./scripts/verify_build_resources.sh dist/SchedulePlanner       # Windows/Linux one-dir
```

It asserts `ui.html`, `files/lamc_data.xlsx`, and a platform OR-Tools native lib
(`libortools*.dylib` / `ortools*.dll` / `libortools*.so*`) are present, and
exits non-zero if any is missing. On macOS, `scripts/verify_macos_build.sh`
wraps this and adds a Mach-O assertion plus a negative-control self-test.

## Per-OS MANUAL GUI checklist (cannot be automated headlessly)

Launching the windowed app to click buttons needs an interactive desktop
session on that OS. After building, launch and confirm:

- [ ] App launches with **no console/terminal window** and shows a native window.
- [ ] `ui.html` renders the **Choose data file** and **Load demo data** buttons.
- [ ] **Load demo data** runs analysis on the bundled workbook and renders
      full-time and part-time cohort results — **with no file picking**.
- [ ] **Choose data file** opens the OS-native file picker and accepts an `.xlsx`.
- [ ] Missing Ollama does **not** block analysis (the AI layer is optional;
      results still render, just without the AI explanation).
- [ ] The app exits cleanly.

Launch per OS:

```bash
# macOS
open dist/SchedulePlanner.app
# or for logs: ./dist/SchedulePlanner.app/Contents/MacOS/SchedulePlanner
```

```powershell
# Windows
.\dist\SchedulePlanner\SchedulePlanner.exe
```

```bash
# Linux
./dist/SchedulePlanner/SchedulePlanner
```

Platform-specific GUI notes to watch for:

- **Windows:** a blank window almost always means the WebView2 Runtime is
  missing — install the Evergreen WebView2 Runtime and relaunch.
- **Linux:** a startup crash about a missing web view means no GTK/Qt backend is
  installed — install one (see "Linux backend install").
- **macOS:** unsigned builds are blocked by Gatekeeper on first open; see
  `BUILD.md` "macOS Gatekeeper bypass".

## See also

- `BUILD.md` — the verified macOS build, flag-by-flag rationale, frozen
  native-stack smoke test, and known risks.
- `scripts/build_macos_console_smoke.sh` — a `--console` smoke harness (not the
  shipped app) that proves the frozen OR-Tools + pandas + `engine.run` stack
  loads end to end. Useful to re-derive on Windows/Linux if a build is suspect.
