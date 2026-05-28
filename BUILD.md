# Building the Desktop App

A standalone native app: user picks a data file, the OR-Tools solver generates
the schedules, and Gemma 4 E2B (via Ollama) optionally parses messy prerequisites
and writes the admin briefing.

## Architecture

```
   ui.html  ──(pywebview bridge)──►  app.py
                                       │
                         ┌─────────────┴─────────────┐
                         ▼                           ▼
                    engine.py                   llm_assist.py
              (OR-Tools solver,             (Gemma 4 E2B via Ollama;
               deterministic — owns          parses prereq text + writes
               the schedule)                 explanations; optional)
```

The solver owns correctness. The LLM only reads and writes text. If Gemma 4 is
absent the app still works fully — prereqs fall back to a regex parser and the
briefing falls back to a templated summary.

## Run in development

```bash
pip install -r requirements.txt
python app.py
```

## The AI layer (Gemma 4 E2B)

Gemma 4 is Google's open model family (Apache 2.0, released April 2026). The
E2B ("Effective 2B") size is built for laptops/edge and is the right pick here.

The app talks to a local **Ollama** server over HTTP (localhost:11434), so:

1. User installs Ollama once: https://ollama.com/download
2. The app detects it and offers a one-click "get it" that runs
   `ollama pull gemma4:e2b` (a few-GB download, first run only).
3. After that, parsing and explanations run fully offline and private.

If you prefer a *fully self-contained* binary with no separate Ollama install,
swap `llm_assist.py`'s HTTP calls for **llama-cpp-python** loading a Gemma 4 E2B
GGUF you bundle or download on first launch. More setup, but one artifact.

> Verify the exact Ollama tag before shipping (`ollama show gemma4:e2b`); the
> model string in `llm_assist.py` (`MODEL = "gemma4:e2b"`) may need adjusting to
> match the published tag.

## Package into a single binary (PyInstaller)

```bash
pip install pyinstaller

pyinstaller --noconfirm --windowed --name "SchedulePlanner" \
  --add-data "ui.html:." \
  app.py
```

- `--windowed` hides the console (drop it while debugging).
- `--add-data "ui.html:."` bundles the UI. On Windows use `;` instead of `:`
  → `--add-data "ui.html;."`.
- Output lands in `dist/SchedulePlanner/` (or a single file with `--onefile`).

### Per-platform notes

- **macOS**: produces `SchedulePlanner.app`. For distribution outside your own
  machine, codesign + notarize. pywebview uses the system WKWebView (no extra
  runtime).
- **Windows**: pywebview uses WebView2 (preinstalled on Win11; ships a bootstrap
  on Win10). `--onefile` works but starts slower.
- **Linux**: pywebview needs `python3-gi` + `gir1.2-webkit2-4.0` (GTK) or a Qt
  backend. Test the target distro.

### OR-Tools + PyInstaller

OR-Tools bundles native libraries. If the frozen app can't find them, add a hook:

```bash
pyinstaller ... --collect-all ortools app.py
```

## What ships vs. what downloads

- **Ships in the binary**: app code, solver, UI.
- **Downloaded on first use**: the Gemma 4 model (via Ollama). Keeps the binary
  small and lets users opt out of AI entirely.
- **Never bundled**: any student data. The app only reads the file the user picks.

## Data file format

A single `.xlsx` workbook with three sheets — `sections`, `catalog`, `programs`
— matching the column labels in the data request spec. A folder of the three
CSVs also works (see `engine.load_data`).
