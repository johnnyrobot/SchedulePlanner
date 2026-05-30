# EdgeSched — Manual QA Runbook

A step-by-step manual test pass for the **SchedulePlanner** desktop app, written for a non-technical tester. Milestones **m0–m8 have shipped**; the core engine, privacy, performance, and determinism are already covered by automated tests (`python3 -m pytest -q` passes, with only the 3 `live`-marked tests deselected; `./scripts/run_qa.sh`). **This runbook covers only the manual-only items** that automation cannot reach: the GUI pixels, the packaged `.app`, live network fetches, and the optional Ollama AI. Run it top to bottom in one sitting.

---

## Before you start

**Environment needed:**
- A **macOS desktop session** (you must be able to see and click windows — this is not a headless/SSH task).
- **Python 3** available as `python3` on your `PATH`.
- Dependencies installed: `pip install -r requirements.txt`
- **Network is OPTIONAL** — needed only for the live sections (4 and 5).
- **Ollama is OPTIONAL** — needed only for the AI section (7). Everything else must work without it.

**Pre-flight automated gate (pass/fail — do not continue if these fail):**

- [ ] **Action:** Run `python3 -m pytest -q`
  - **Expected:** ends with `<N> passed, 3 deselected` and no `failed`/`error`
  - **Fail signs:** any `failed` / `error` (the passed count grows as tests are added — that's expected; only failures/errors matter)
  - **Evidence:** terminal tail showing the pass line
- [ ] **Action:** Run `./scripts/run_qa.sh`
  - **Expected:** tail shows `<N> passed, 3 deselected` then `QA gate PASS (live deselected: 3)`
  - **Fail signs:** `QA gate FAIL: ...` or a non-zero exit
  - **Evidence:** terminal tail showing `QA gate PASS (live deselected: 3)`

---

## 1. Launch the dev app

- [ ] **Action:** Run `python3 app.py`
  - **Expected:** A native window opens titled **`LAMC Schedule Planner`**; the page header reads **`SCHEDULE PLANNER`** with the tag **`2-Year Completion · LAMC`** beneath it.
  - **Fail signs:** No window; a browser tab instead of a native window; blank/white window; a Python traceback in the terminal.
  - **Evidence:** Screenshot of the full window (title bar + header).
- [ ] **Action:** Look at the small status pill near the header right after launch.
  - **Expected:** It first shows **`checking AI…`**, then settles into one of: **`Gemma 4 ready (...)`**, **`Gemma 4 not downloaded — get it`**, or **`Offline mode (no Gemma 4)`**. Any of the three is acceptable here.
  - **Fail signs:** Pill stuck on `checking AI…` indefinitely; pill missing.
  - **Evidence:** Screenshot of the settled AI pill.

---

## 2. One-click demo — `Load demo data`

- [ ] **Action:** Click the **`Load demo data`** button.
  - **Expected:** Button briefly shows **`Loading…`**, the status line shows **`running solver on demo data`**, the picked-file line becomes **`bundled demo data`**, then results render automatically (no file picker, no extra clicks).
  - **Fail signs:** A file picker opens; an error card; nothing renders.
  - **Evidence:** Screenshot of the rendered results.
- [ ] **Action:** Review the four program cards and their badges.
  - **Expected:** Four programs render. Per the demo data: **Business AS-T** shows **`on track`**; **CS AS-T** and **Biology AS-T** show **`map broken`** (official map invalid, but a corrected 4-term path is found); **Engineering AS-T** shows **`needs fix`** with a fix line indicating **add one ENGR 102 section in Spring**.
  - **Fail signs:** Fewer than four cards; all `on track`; no `needs fix`; no ENGR 102 fix line on Engineering.
  - **Evidence:** Screenshot showing all four badges and the ENGR 102 fix line.
- [ ] **Action:** On each program card, check the cohort and supply sections.
  - **Expected:** Each program shows **`Full-time`** and **`Part-time`** cohort cards (a cohort with no path shows an em dash `—`), plus a **`Supply diagnostics (N terms)`** card with columns **`Rotation gaps`**, **`Single-section risk`**, **`Modality mismatch`**, and **`Under-supply`** (empty columns read `none`).
  - **Fail signs:** Missing cohort cards; missing supply diagnostics; broken/empty layout.
  - **Evidence:** Screenshot of one program's cohort + supply cards.

> **KNOWN QUIRK — do NOT do this:** After a demo load, **do not click `Generate schedules`**. Demo data does not set a file path, so that button reports **`File not found.`** This is expected behavior, not a bug. To re-run on a file, use the file-pick flow in section 3.

---

## 3. File-pick flow — `Choose data file` → `Generate schedules`

- [ ] **Action:** Click **`Choose data file`** and pick a workbook. Suggested: `files/lamc_data.xlsx` (or `files/lamc_sample_enrollment.xlsx`).
  - **Expected:** A native macOS file picker opens and accepts `.xlsx`. After choosing, the picked-file line shows the file path you selected.
  - **Fail signs:** No picker; picker rejects `.xlsx`; path line stays empty after choosing.
  - **Evidence:** Screenshot showing the picked file path.
- [ ] **Action:** Click **`Generate schedules`**.
  - **Expected:** Button briefly shows **`Solving…`** with status **`running solver`**, then results render (program cards, cohorts, supply diagnostics) just like the demo.
  - **Fail signs:** **`File not found.`** (means no path was set — re-pick the file); any other error card; nothing renders.
  - **Evidence:** Screenshot of the rendered results from the picked file.

---

## 4. Live LACCD build (in-app) — REQUIRES network

- [ ] **Action:** In the **`Build from live LACCD data`** card, confirm the defaults: Campus **`LAMC`**, Terms **`2264,2266,2268`**, Program **`Biology`**. Then click **`Build from live LACCD data`**.
  - **Expected:** Button briefly shows **`Fetching…`** with status **`pulling live LACCD data`** (or **`fetching + solving`**); the picked line becomes **`live LACCD data · LAMC · Biology`**. Results then include a **`Live data reconciliation`** card and a **`Detectors inert on live data`** card.
  - **Fail signs:** **`Could not fetch live LACCD data: ...`** (network/source issue); spinner never resolves; neither live card appears.
  - **Evidence:** Screenshot of the `Live data reconciliation` and `Detectors inert on live data` cards.
- [ ] **Action:** (Bad-input check) Clear the Terms field, type **`abc`**, and click **`Build from live LACCD data`**.
  - **Expected:** An error card appears whose text begins **`No valid term codes in ...`** and tells you to enter numeric term codes like `2264,2266,2268`.
  - **Fail signs:** App tries to fetch anyway; a crash; a generic/blank error.
  - **Evidence:** Screenshot of the `No valid term codes` error card.

---

## 5. Live LACCD build (CLI) — REQUIRES network

- [ ] **Action:** Run `python3 build_live_workbook.py --campus LAMC --program "Biology" --terms 2264,2266,2268 --out data/live_LAMC.xlsx`
  - **Expected:** Command completes without error and writes the workbook to `data/live_LAMC.xlsx` (the file persists on disk — `--out` defaults to that path if omitted).
  - **Fail signs:** An HTTP 403 / `SourceHTTPError`; no file produced; a traceback.
  - **Evidence:** Terminal tail + confirm the file exists at `data/live_LAMC.xlsx`.
- [ ] **Action:** Run `python3 engine.py data/live_LAMC.xlsx`
  - **Expected:** Prints JSON analysis (keys include `terms_in_data`, `analysis`, `programs`) for the live-built workbook.
  - **Fail signs:** `InputDataError` about missing sheets/columns; empty output; a traceback.
  - **Evidence:** Terminal tail of the JSON output.

---

## 6. Packaged macOS app

- [ ] **Action:** Set up the build venv (one time):
  ```
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  python -m pip install pyinstaller
  ```
  (PyInstaller is intentionally **not** in `requirements.txt` — install it separately as above.)
  - **Expected:** All installs complete without error.
  - **Fail signs:** pip errors; PyInstaller not found later.
  - **Evidence:** Terminal tail of the last install.
- [ ] **Action:** Run `./scripts/build_macos.sh`
  - **Expected:** Build finishes and prints `Build complete: <root>/dist/SchedulePlanner.app`, then `Launch with:    open dist/SchedulePlanner.app`, then an unsigned-build note. The artifact exists at **`dist/SchedulePlanner.app`**.
  - **Fail signs:** `error: expected dist/SchedulePlanner.app was not produced.`; PyInstaller errors; no `.app`.
  - **Evidence:** Terminal tail showing `Build complete: ...`.
- [ ] **Action:** Run the headless verifiers:
  ```
  ./scripts/verify_macos_build.sh
  ./scripts/verify_build_resources.sh dist/SchedulePlanner.app
  ```
  - **Expected:** First prints `=== verify_macos_build: ALL HEADLESS CHECKS PASSED ===`. Second prints `PASS:` lines for `ui.html`, `lamc_data.xlsx`, and the OR-Tools native lib, ending with `RESULT: all required resources present in dist/SchedulePlanner.app`.
  - **Fail signs:** Any `FAIL:` line; a non-zero exit; a missing-resource result.
  - **Evidence:** Terminal tail showing both PASS results.
- [ ] **Action:** First launch is blocked by Gatekeeper (the app is unsigned). Bypass it once using any of: Finder right-click (or Control-click) the app → **Open** → confirm **Open**; or System Settings → **Privacy & Security** → **Open Anyway**; or Terminal `xattr -dr com.apple.quarantine /path/to/SchedulePlanner.app`. Then run `open dist/SchedulePlanner.app`.
  - **Expected:** The app launches in a native window after the bypass.
  - **Fail signs:** "cannot be opened because the developer cannot be verified" with no way past; app never opens.
  - **Evidence:** Screenshot of the launched packaged app.
- [ ] **Action:** Run the BUILD.md manual GUI checklist on the packaged app. Confirm each:
  - **Expected:**
    - Launches with **no terminal window** and shows the native window.
    - `ui.html` renders (buttons **`Choose data file`** and **`Load demo data`** visible).
    - **`Load demo data`** runs analysis on the bundled workbook and renders results for **Full-time** and **Part-time** cohorts — with no file picking.
    - **`Choose data file`** opens the macOS file picker and accepts an `.xlsx`.
    - Missing Ollama does **not** block analysis (results still render, just without the AI explanation).
    - The app **exits cleanly**.
  - **Fail signs:** Any item above fails; a terminal window pops up alongside the app.
  - **Evidence:** Screenshots of demo results and the file picker in the packaged app.

---

## 7. AI / Ollama — OPTIONAL path

> Only do this section if Ollama is installed locally. Analysis must already work without it (verified in sections 2–3).

- [ ] **Action:** Pull the model: `ollama pull gemma4:e2b` (first-run download is ~7 GB and slow).
  - **Expected:** Pull completes.
  - **Fail signs:** Ollama not installed; pull errors.
  - **Evidence:** Terminal tail of the completed pull.
- [ ] **Action:** Quick detection check: `python3 llm_assist.py`
  - **Expected:** Reports the model as present (exact tag `gemma4:e2b` — a different tag of the same family does not count).
  - **Fail signs:** Reports not found despite a successful pull; an error.
  - **Evidence:** Terminal tail.
- [ ] **Action:** Relaunch `python3 app.py` and check the AI pill.
  - **Expected:** Pill shows **`Gemma 4 ready (gemma4:e2b)`**.
  - **Fail signs:** `Offline mode (no Gemma 4)` or `Gemma 4 not downloaded — get it` despite the model being pulled.
  - **Evidence:** Screenshot of the `Gemma 4 ready (gemma4:e2b)` pill.
- [ ] **Action:** Run an analysis (demo or file-pick), then click **`Explain for admin`**.
  - **Expected:** A **`Briefing`** card appears (it first shows **`writing summary…`**). The result status line reads **`Gemma 4 enabled`** when the LLM wrote it; if you click `Explain for admin` with no analysis yet, you get **`Run an analysis first.`**
  - **Fail signs:** No Briefing card; a crash; pill says ready but status shows `regex parse (offline)` (means the LLM did not actually run).
  - **Evidence:** Screenshot of the `Briefing` card with the `Gemma 4 enabled` status line.
- [ ] **Action:** (Fallback sanity) With Ollama stopped/absent, repeat `Explain for admin`.
  - **Expected:** A Briefing card still appears, with status line **`regex parse (offline)`** (templated fallback). Analysis is never blocked by missing AI.
  - **Fail signs:** No briefing at all; an error blocks the results.
  - **Evidence:** Screenshot of the offline `regex parse (offline)` status.

---

## 8. Privacy / local-data checks

- [ ] **Action:** Turn **Wi-Fi OFF**, relaunch `python3 app.py`, and click **`Load demo data`**.
  - **Expected:** Demo analysis still runs and renders fully — no network is required for the demo/file paths.
  - **Fail signs:** Demo fails or hangs waiting on network.
  - **Evidence:** Screenshot of demo results with Wi-Fi off.
- [ ] **Action:** Run an **in-app** live build (section 4, network back ON), then check your working directory and Downloads for a stray workbook.
  - **Expected:** The in-app live fetch leaves **no** workbook behind (it uses an ephemeral temp dir). Only the explicit CLI `--out` from section 5 should produce a persisted file.
  - **Fail signs:** A live workbook appears on disk after an in-app fetch.
  - **Evidence:** Directory listing before/after showing no new file.
- [ ] **Action:** Run the privacy gate: `python3 -m pytest tests/test_privacy_invariants.py -q`
  - **Expected:** all privacy tests pass (no `failed`/`error`). Confirms no student data, no PII, and no telemetry.
  - **Fail signs:** Any failure.
  - **Evidence:** Terminal tail showing the pass line.

---

## Known limitations

- **Live enrollment is zero.** The live LACCD schedule API returns `Cap Enrl = Tot Enrl = Wait Tot = 0`, so the **`Modality mismatch`** and **`Under-supply`** detectors are **inert on live data**. They activate only when the IR PeopleSoft enrollment export arrives (m7, pending). The committed sample `files/lamc_sample_enrollment.xlsx` proves they fire.
- **Prerequisites blank on live data.** eLumen is not wired, so the live solver runs without prerequisite ordering.
- **Program Mapper:** one program per run (no bulk export); it requires a browser User-Agent + campus Origin or it returns HTTP 403.
- **N7 — synchronous solve.** The solver runs synchronously inside the app's bridge calls, so a very large dataset can briefly freeze the window while it runs.
- **Unsigned app.** `dist/SchedulePlanner.app` is unsigned and un-notarized (internal testers only); first launch needs the Gatekeeper bypass in section 6. The bundle is large (~800 MB) by design.
- **Windows / Linux bundles** are prepared (recipes/scripts exist) but were **not built or verified** on the macOS host — PyInstaller is not a cross-compiler.
