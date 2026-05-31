# EdgeSched — Manual QA Runbook & Release-Candidate Gate

A release-candidate (RC) gate for the **SchedulePlanner** desktop app, written so a
non-technical tester can run it top to bottom in one sitting. The engineering
milestones (m0–m8) on `main` have shipped (see `docs/M8_QA_REPORT.md`); the core
engine, privacy, performance, and determinism are already covered by automated
tests (`python3 -m pytest -q`; `./scripts/run_qa.sh`). This document keeps every
pytest expectation **count-agnostic** — the passed count grows as tests are added,
so only `failed`/`error` matter (the single frozen figure is the **3** `live`-marked
tests the gate deselects, which is a locked invariant, not a pass count).

The checks are grouped into clearly-separated QA categories. Run the **automated
gate first**; it is the pass/fail pre-flight. The last two categories
(**signing/notarization** and **Windows/Linux**) are **deferred** and are *not*
part of this macOS RC sign-off — they are documented so a future RC can pick them
up, and so testers never mistake "not done" for "broken."

---

## Release-candidate sign-off (macOS RC)

Tick every IN-SCOPE box to call this build a release candidate on macOS. The
DEFERRED items are explicitly **not** part of this RC — do **not** sign them off as
done.

**IN SCOPE for this macOS RC:**

- [ ] **1. Automated gate** — `python3 -m pytest -q` ends `<N> passed, 4 deselected`
  (no `failed`/`error`) **and** `./scripts/run_qa.sh` prints `QA gate PASS (live deselected: 4)`.
- [ ] **2. Manual GUI QA (dev app)** — launch, demo, and file-pick flows render on a real macOS desktop session.
- [ ] **3. Live network QA** — in-app + CLI live builds reach the public LACCD APIs and render the reconciliation + inert-detector panels (network required).
- [ ] **4. Packaged macOS app** — `build_macos.sh` produces `dist/SchedulePlanner.app`, both verifiers PASS, and the unsigned bundle launches after the Gatekeeper bypass.
- [ ] **5. Optional Ollama AI** — only if Ollama + `gemma4:e2b` are installed; analysis MUST already work without them.
- [ ] **6. Privacy / local data** — Wi-Fi-off demo renders, in-app live fetch persists nothing, privacy suite is green.

**DEFERRED — NOT part of this RC (do not sign off as done):**

- [ ] **7. Code-signing / notarization** — NOT performed for this RC. Signing/notarization **scaffolding** exists (`scripts/sign_notarize_macos.sh`, `scripts/sign_windows.ps1`, `packaging/entitlements.mac.plist`, `docs/PRODUCTION_RELEASE_CHECKLIST.md`) but is **not run** here and is blocked on external Developer ID / signing credentials. `dist/SchedulePlanner.app` ships **unsigned + un-notarized**; internal testers only; first launch needs the Gatekeeper bypass.
- [ ] **8. Windows / Linux bundles** — recipes/scripts only (`docs/CROSS_PLATFORM_BUILD.md`); **NOT built or verified** on the macOS host (PyInstaller is not a cross-compiler).
- [ ] **Live eLumen prerequisites** — wired as **opt-in** (`--elumen-live`, default off) and **approval-gated**: NOT relied on for production until institutional/eLumen Terms-of-Use + rate-limit + data-handling sign-off (see `docs/eLUMEN_LIVE_USAGE.md`). A bare live build (no flag) still has blank prerequisites; the offline `--elumen-fixture` path is a separate synthetic stand-in.
- [ ] **Real IR / PeopleSoft enrollment** — still blocked / out of scope: there is **no real IR export**. The live schedule API returns `Cap/Tot/Wait = 0`, so the enrollment-driven detectors are inert on live data. Only the **synthetic** sample `files/lamc_sample_enrollment.xlsx` (not real IR data) exercises them.

---

## Before you start

**Environment needed:**

- A **macOS desktop session** (you must be able to see and click windows — this is not a headless/SSH task).
- **Python 3** available as `python3` on your `PATH`.
- Dependencies installed: `pip install -r requirements.txt`
- **Network is OPTIONAL** — needed only for the live category (3).
- **Ollama is OPTIONAL** — needed only for the AI category (5). Everything else must work without it.

**How to read each step:** every check is an **Action** (what to do), an **Expected**
result (the PASS), **Fail signs** (what a FAIL looks like), and the **Evidence** to
capture (a screenshot or terminal tail) for the QA log.

---

## 1. Automated QA gate (offline green suite) — run this FIRST

> The pass/fail pre-flight. Do **not** continue to the manual categories if this
> fails. The offline suite must be fully green and the `live` test set must be
> exactly the documented set (currently **four**). The **passed count is
> intentionally NOT frozen** (sibling work adds tests); the gate locks only the
> number of **`live`-marked tests deselected** (`EXPECTED_DESELECTED` in
> `scripts/run_qa.sh`, currently `4`). Network and Ollama must NOT be required to
> pass this gate.

- [ ] **Action:** Run `python3 -m pytest -q`
  - **Expected:** the summary line ends with `<N> passed, 4 deselected`; no `failed`, no `error`.
  - **Fail signs:** any `failed`/`error`; a deselected count other than `4`. (The `<N> passed` number growing is normal — not a fail sign.)
  - **Evidence:** terminal tail showing the `<N> passed, 4 deselected` line.
- [ ] **Action:** Run `./scripts/run_qa.sh` (with **no extra arguments** — added pytest flags change the deselected count and trip the gate).
  - **Expected:** it echoes the pytest run, then the tail shows `<N> passed, 4 deselected` immediately followed by `QA gate PASS (live deselected: 4)`; exit code 0.
  - **Fail signs:** `QA gate FAIL: pytest exited <code>` (a real test failure), `QA gate FAIL: expected 4 live tests deselected, saw <N>` (the live set drifted), any non-zero exit, or `QA gate ERROR:` if `python3`/`pytest` is missing.
  - **Evidence:** terminal tail showing `QA gate PASS (live deselected: 4)`.
- [ ] **Action:** Confirm the gate's own invariants are intact: `python3 -m pytest tests/test_qa_gate.py -q`
  - **Expected:** all green. The gate's meta-test in `tests/test_qa_gate.py` (`test_live_set_is_exactly_the_three_known_nodes` — the name predates the 4th node but it now pins the full set) confirms the deselected live set is precisely `tests/test_live_roundtrip.py::test_live_lamc_end_to_end`, `tests/test_llm_assist.py::test_live_explain_against_real_ollama`, `tests/test_engine_features.py::test_llm_assist_cli_no_args_runs`, and `tests/test_elumen_client.py::test_live_lamc_endpoint_schema`.
  - **Fail signs:** any failure — especially a `live test set drifted` assertion (a `live` test was added/removed without updating the gate).
  - **Evidence:** terminal tail showing the gate tests pass.

> If the `live` set legitimately changes, update `KNOWN_LIVE_NODES` in
> `tests/test_qa_gate.py` **and** `EXPECTED_DESELECTED` in `scripts/run_qa.sh`
> together — they are deliberately coupled.

---

## 2. Manual GUI QA — dev app (launch, demo, file-pick)

> Run `python3 app.py` on a macOS desktop session (not headless). All strings below
> are the literal on-screen text; where CSS uppercases a label it is noted. **No
> network or Ollama is required for any check in this category** — a clean offline
> AI reading is acceptable here.

### 2a. Launch + chrome

- [ ] **Action:** Run `python3 app.py`.
  - **Expected:** a native window (not a browser tab) opens; the title bar reads **`LAMC Schedule Planner`**; the header shows **`SCHEDULE PLANNER`** with the tag **`2-Year Completion · LAMC`** beneath it. (Both the header and tag render UPPERCASE via CSS, so on screen they read `SCHEDULE PLANNER` and `2-YEAR COMPLETION · LAMC` — that casing is expected, not a mismatch.)
  - **Fail signs:** no window; a browser tab instead of a native window; a blank/white window; a Python traceback in the terminal.
  - **Evidence:** screenshot of the full window (title bar + header).
- [ ] **Action:** Watch the small status pill near the header right after launch.
  - **Expected:** it first shows **`checking AI…`**, then settles into exactly one of **`Gemma 4 ready (gemma4:e2b)`**, **`Gemma 4 not downloaded — get it`**, or **`Offline mode (no Gemma 4)`**. Any of the three is acceptable here (AI is optional).
  - **Fail signs:** pill stuck on `checking AI…` indefinitely; pill missing.
  - **Evidence:** screenshot of the settled AI pill.

### 2b. One-click demo — `Load demo data`

- [ ] **Action:** Click the **`Load demo data`** button.
  - **Expected:** the button briefly shows **`Loading…`**, the status line shows **`running solver on demo data`**, the picked-file line becomes **`bundled demo data`**, then results render automatically (no file picker, no extra clicks).
  - **Fail signs:** a file picker opens; an error card; nothing renders.
  - **Evidence:** screenshot of the rendered results.
- [ ] **Action:** Review the four program cards and their badges.
  - **Expected:** four cards render with their full on-screen titles: **`Business Administration AS-T`** → **`on track`**; **`Computer Science AS-T`** → **`map broken`**; **`Biology AS-T`** → **`map broken`**; **`Engineering AS-T`** → **`needs fix`**, with a green fix line reading **`fix: + ENGR 102 in Spring`** and a red **`(with fix)`** marker on its Full-time cohort. (`map broken` = the official map is flagged invalid but a valid path is still found; `needs fix` = a fix was required to find a path.)
  - **Fail signs:** fewer than four cards; all `on track`; no `needs fix`; no ENGR 102 fix line on Engineering.
  - **Evidence:** screenshot showing all four badges and the ENGR 102 fix line.
- [ ] **Action:** On each program card, check the cohort and supply sections.
  - **Expected:** each program shows **`Full-time`** and **`Part-time`** cohort cards (a cohort with no path shows an em dash `—`), plus a **`Supply diagnostics (N terms)`** card (the demo shows `8 terms`) with columns **`Rotation gaps`**, **`Single-section risk`**, **`Modality mismatch`**, and **`Under-supply`** (an empty column reads `none`).
  - **Fail signs:** missing cohort cards; missing supply diagnostics; broken/empty layout.
  - **Evidence:** screenshot of one program's cohort + supply cards.

> **KNOWN QUIRK — expected, not a bug:** after a demo load, do **not** click
> `Generate schedules`. Demo data does not set a file path, so that button returns
> a red **`File not found.`** error card. To re-run on a file, use the
> Choose-data-file flow below.

### 2c. File-pick flow — `Choose data file` → `Generate schedules`

- [ ] **Action:** Click **`Choose data file`** and pick a workbook. Suggested: `files/lamc_data.xlsx` (or `files/lamc_sample_enrollment.xlsx`).
  - **Expected:** a native macOS file picker opens; its default filter is **`Data files (*.xlsx;*.xls)`** with an **`All files (*.*)`** option. After choosing, the picked-file line shows the path you selected.
  - **Fail signs:** no picker; the path line stays empty after choosing.
  - **Evidence:** screenshot showing the picked file path.
- [ ] **Action:** Click **`Generate schedules`**.
  - **Expected:** the button briefly shows **`Solving…`** with status **`running solver`**, then results render (program cards, cohorts, supply diagnostics) just like the demo. The bottom status line reads **`Gemma 4 enabled`** when the AI layer was available for the run, or **`regex parse (offline)`** when it was not — both are acceptable.
  - **Fail signs:** **`File not found.`** (no path was set — re-pick the file); any other error card; nothing renders.
  - **Evidence:** screenshot of the rendered results from the picked file.

---

## 3. Live network QA — in-app build + CLI (REQUIRES network)

> Both live sources (the LACCD schedule API + Program Mapper) are
> public/unauthenticated, so a networked run really does fetch real sections and
> write a real workbook. **But the live data carries ZERO enrollment and BLANK
> prerequisites by design**, so three detectors stay **inert** on any bare live
> run. That is expected, not a failure.

**Why detectors stay inert on a bare live build (no enrollment join; eLumen opt-in off):**

- The LACCD schedule API returns **no** `Cap Enrl` / `Tot Enrl` / `Wait Tot` (all 0), so **`modality_mismatch`** (fires only when `Cap Enrl` sum > 0) and **`under_supply`** (fires only when `Wait Tot` sum > 15) cannot fire. They would activate only once a **real IR PeopleSoft enrollment export** is joined — but **there is no real IR export** (still blocked / out of scope), and the synthetic-fixture join is NOT validated on real data (today's committed fixtures match zero live sections, so even a `--enrollment` run with them stays inert). `files/lamc_sample_enrollment.xlsx` proves the detectors *can* fire on a self-consistent **synthetic** fixture only.
- Program Mapper exposes **no** prerequisites, and the live eLumen prereq fetch is **opt-in and off by default** (the `--elumen-live` flag, §3b), so on a bare live build **`prerequisite_ordering`** is inert and the solver runs without ordering constraints. (The offline `--elumen-fixture` flag is a separate synthetic stand-in.)

### 3a. In-app live build

- [ ] **Action:** In the **`Build from live LACCD data`** card, confirm the defaults — Campus **`LAMC`**, Terms **`2264,2266,2268`**, Program **`Biology`** — then click **`Build from live LACCD data`**.
  - **Expected:** the button briefly shows **`Fetching…`**; the live status shows **`pulling live LACCD data`** and the main status shows **`fetching + solving`**; the picked line becomes **`live LACCD data · LAMC · Biology`**. Results then include a **`Live data reconciliation`** card and a **`Detectors inert on live data`** card. The inert card lists **three** detectors (`modality_mismatch`, `under_supply`, `prerequisite_ordering`), each with a reason + remedy — three rows is correct, not a bug.
  - **Fail signs:** **`Could not fetch live LACCD data: ...`** (network/source issue); a spinner that never resolves; neither live card appears.
  - **Evidence:** screenshot of the `Live data reconciliation` and `Detectors inert on live data` cards, showing the three inert-detector rows.
- [ ] **Action:** (Bad-input check) Clear the Terms field, type **`abc`**, and click **`Build from live LACCD data`**.
  - **Expected:** an error card whose text begins **`No valid term codes in 'abc'.`** and tells you to enter positive numeric codes like `2264,2266,2268`. No fetch is attempted.
  - **Fail signs:** the app tries to fetch anyway; a crash; a generic/blank error.
  - **Evidence:** screenshot of the `No valid term codes` error card.

### 3b. CLI live build + engine

- [ ] **Action:** Run `python3 build_live_workbook.py --campus LAMC --program "Biology" --terms 2264,2266,2268 --out data/live_LAMC.xlsx`
  - **Expected:** the command completes without error and writes the workbook to `data/live_LAMC.xlsx` (the file persists on disk — `--out` defaults to that path if omitted). It prints a human banner plus a JSON report with keys `campus`, `terms`, `program`, `section_count`, `reconciliation`, `inert_detectors`, `results`.
  - **Fail signs:** an HTTP 403 / `SourceHTTPError` (Program Mapper rejected the request — it needs the browser User-Agent + campus Origin, which the client sends automatically); **`No program matched 'Biology' at LAMC.`** with a non-zero exit (the program query did not resolve — try a different `--program`); no file produced; a traceback.
  - **Evidence:** terminal tail + confirm the file exists at `data/live_LAMC.xlsx`.
- [ ] **Action:** Run `python3 engine.py data/live_LAMC.xlsx`
  - **Expected:** prints JSON analysis whose top-level keys include `terms_in_data`, `analysis`, `programs`. The JSON is real, but with zero enrollment and blank prereqs by design: `analysis.modality_mismatch` and `analysis.under_supply` are **empty** — expected on live data, not a failure.
  - **Fail signs:** `InputDataError` about missing sheets/columns; empty output; a traceback.
  - **Evidence:** terminal tail of the JSON output.
- [ ] **Action:** (Opt-in, approval-gated) Live eLumen prerequisite enrichment — `python3 build_live_workbook.py --campus LAMC --program "Biology" --terms 2264,2266,2268 --elumen-live --out data/live_LAMC.xlsx`
  - **Expected:** the build additionally fetches **published course prerequisites** from the public eLumen catalog API for **only the selected campus + the subjects the program's sections cover** (no all-subjects sweep), and attaches a coverage report at `report["elumen_coverage"]` (`courses_fetched`, `courses_with_prereqs`, `unmatched_prereq_targets`, `requested_courses_without_record`). The path is **opt-in** (default off) and bounded by guardrails — a throttle (≥ 1 s between requests), a per-build **in-memory cache** (no disk persistence), page caps (`pageSize ≤ 25`, ≤ 20 pages/subject), and **bounded exponential backoff** on 429/5xx/timeout (≤ 3 retries, capped at 30 s) with a clean `SourceError` on give-up. Only leaf `Prerequisite` requisites become ordering constraints (corequisites/advisories excluded). **Inspect the coverage report before trusting results** — the eLumen↔schedule course-id join is validated only via that report.
  - **Fail signs:** an HTTP 403/4xx or `SourceDataError` naming the eLumen source/URL (bad campus/query or response-shape drift); a raw `httpx` traceback (should never happen — failures surface as a `SourceError` subclass).
  - **Evidence:** terminal tail showing the `elumen_coverage` block; note whether the coverage join looks sound.

> **Approval gate — do not represent live eLumen data as production-ready.** Until
> written institutional/eLumen Terms-of-Use review, a rate-limit / acceptable-use
> agreement, and data-handling sign-off are recorded, `--elumen-live` is **opt-in
> admin/testing only**. See `docs/eLUMEN_LIVE_USAGE.md` for the full guardrails and
> the approval gate.

> **Deterministic offline stand-in:** the network-independent equivalent of this
> category is `python3 -m pytest -q tests/test_live_offline_pipeline.py` (green
> regardless of network). A recorded real-network transcript lives in
> `docs/live_smoke_2026.md`; refresh it with `./scripts/live_smoke.sh` on a
> networked host.

---

## 4. Packaged app QA — macOS

> Scope: the macOS `.app` only. Windows/Linux bundles are a **separate, deferred**
> category (§8). The macOS bundle is **unsigned and un-notarized** (internal testers
> only); signing/notarization is verified separately in §7. Do not claim signing
> here.

- [ ] **Action:** Set up the build venv (one time):
  ```
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  python -m pip install pyinstaller
  ```
  - **Expected:** all installs complete without error. PyInstaller is intentionally **not** in `requirements.txt` (it is build-only) — install it separately as the last step.
  - **Fail signs:** pip errors; a later `error: PyInstaller is not installed for ...` from the build script.
  - **Evidence:** terminal tail of the last install.
- [ ] **Action:** Run `./scripts/build_macos.sh`
  - **Expected:** the build succeeds and prints, in order, `Build complete: <repo-root>/dist/SchedulePlanner.app`, then `Launch with:    open dist/SchedulePlanner.app`, then the unsigned-build note `(Unsigned build — see BUILD.md 'macOS Gatekeeper bypass' for first launch.)`. The artifact exists at **`dist/SchedulePlanner.app`**.
  - **Fail signs:** `error: expected dist/SchedulePlanner.app was not produced.`; `error: PyInstaller is not installed for ...`; PyInstaller errors; no `.app`.
  - **Evidence:** terminal tail showing the `Build complete: ...` line.
- [ ] **Action:** Run the headless verifiers:
  ```
  ./scripts/verify_macos_build.sh
  ./scripts/verify_build_resources.sh dist/SchedulePlanner.app
  ```
  - **Expected:** `verify_macos_build.sh` prints `PASS:` lines (bundle exists, Mach-O executable), a `--- negative control ---` block ending `OK: resource check correctly reported FAIL (exit non-zero) on missing lib`, then `=== verify_macos_build: ALL HEADLESS CHECKS PASSED ===` and a trailing `(GUI behaviour is manual — see BUILD.md 'Manual GUI checklist'.)` line. The standalone resource check prints `PASS: ui.html bundled`, `PASS: lamc_data.xlsx bundled (...)`, `PASS: OR-Tools native lib bundled (...)`, ending `RESULT: all required resources present in dist/SchedulePlanner.app`.
  - **Fail signs:** any `FAIL:` line; an `UNEXPECTED PASS` from the negative control; a non-zero exit; a `RESULT: <n> missing resource(s) ...` line. (The negative-control `OK:` line is **expected** — it proves the checker bites.)
  - **Evidence:** terminal tail showing both the `ALL HEADLESS CHECKS PASSED` banner and the `RESULT: all required resources present ...` line.
- [ ] **Action:** (Optional but recommended) Run the frozen native-stack smoke harness: `./scripts/build_macos_console_smoke.sh --run`
  - **Expected:** it builds a separate `--console` exe `dist/SchedulePlannerSmoke`, runs it, prints a `SMOKE PASS:` line (cp_model imported, rows read from the bundled xlsx, `engine.run()` returned program(s)/term(s)), then `=== console smoke: PASS ===`. This proves the frozen OR-Tools + pandas + `engine.run` stack loads end to end, independent of the GUI.
  - **Fail signs:** `SMOKE FAIL: ...`; `=== console smoke: FAIL ===`; `error: expected dist/SchedulePlannerSmoke was not produced.`
  - **Evidence:** terminal tail showing `SMOKE PASS:` and `=== console smoke: PASS ===`.
- [ ] **Action:** First launch is blocked by Gatekeeper because the app is unsigned/un-notarized. Bypass it once using any of: Finder right-click (or Control-click) the app → **Open** → confirm **Open**; or System Settings → **Privacy & Security** → **Open Anyway**; or Terminal `xattr -dr com.apple.quarantine /path/to/SchedulePlanner.app`. Then run `open dist/SchedulePlanner.app`.
  - **Expected:** the app launches in a native window after the bypass.
  - **Fail signs:** "cannot be opened because the developer cannot be verified" with no way past; the app never opens.
  - **Evidence:** screenshot of the launched packaged app.
- [ ] **Action:** Run the BUILD.md manual GUI checklist on the packaged app. Confirm each:
  - **Expected:**
    - Launches with **no terminal window** and shows the native window.
    - `ui.html` renders (buttons **`Choose data file`** and **`Load demo data`** visible).
    - **`Load demo data`** runs analysis on the bundled workbook and renders results for **Full-time** and **Part-time** cohorts — with no file picking.
    - **`Choose data file`** opens the macOS file picker and accepts an `.xlsx`.
    - Missing Ollama does **not** block analysis (results still render, just without the AI explanation).
    - The app **exits cleanly**.
  - **Fail signs:** any item above fails; a terminal window pops up alongside the app.
  - **Evidence:** screenshots of demo results and the file picker in the packaged app.

> The bundle is large (~800 MB) by design (OR-Tools + pandas + numpy + the WebKit
> bridge) — size is not a correctness signal.

---

## 5. Optional Ollama / AI QA — OPTIONAL path (never blocks analysis)

> **AI is strictly optional.** Analysis (demo, file-pick, and live) MUST already
> work with no Ollama and no model, as verified in categories 2–3. This category
> only adds the optional Gemma 4 layer. A clean "offline" reading is a **PASS**, not
> a FAIL.
>
> **Scope:** the Gemma 4 layer does only two things — (a) parse messy
> *unstructured* prerequisite text in **file/demo** workbooks, and (b) write the
> admin **Briefing**. It never decides the schedule. The **live** path has no
> Gemma 4 step (its status line is always `regex parse (offline)` by design). Live
> prerequisite *ordering*, when enabled, comes from the opt-in `--elumen-live`
> catalog fetch (§3b) — **not** from Gemma 4; real **IR/PeopleSoft** enrollment
> remains **deferred** (no real export).

- [ ] **Action:** Pull the model: `ollama pull gemma4:e2b`
  - **Expected:** the pull completes. The configured tag is **exactly** `gemma4:e2b`; a different tag of the same family (`gemma4:e4b`, `gemma4:31b`) does **not** satisfy presence — matching is tag-exact.
  - **Fail signs:** Ollama not installed; pull errors. (The first-run pull is the documented **~7 GB** on-disk size — the `e2b` / "effective-2B" label is the parameter count, not the download size — so it is large and slow.)
  - **Evidence:** terminal tail of the completed pull.
- [ ] **Action:** Quick detection check: `python3 llm_assist.py`
  - **Expected:** prints three boolean lines — `ollama installed: True`, `ollama running  : True`, `model present   : True` — followed by a templated-fallback explanation demo. Because matching is tag-exact, only a pulled `gemma4:e2b` yields `model present   : True`.
  - **Fail signs:** `model present   : False` despite a successful `gemma4:e2b` pull; any traceback.
  - **Evidence:** terminal tail showing the three boolean lines.
- [ ] **Action:** With the Ollama **daemon running** and the model pulled, relaunch `python3 app.py` and check the AI pill.
  - **Expected:** the pill shows **`Gemma 4 ready (gemma4:e2b)`**.
  - **Fail signs:** `Offline mode (no Gemma 4)` or `Gemma 4 not downloaded — get it` while the daemon is up and the model is pulled. (Note: the "ready" pill requires the **daemon running** as well — if the model is on disk but the daemon is stopped, `Offline mode (no Gemma 4)` is the **correct** reading, not a bug.)
  - **Evidence:** screenshot of the `Gemma 4 ready (gemma4:e2b)` pill.
- [ ] **Action:** Run an analysis (demo or file-pick), then click **`Explain for admin`**.
  - **Expected:** a card briefly shows the spinner + **`writing summary…`**, then renders a **`Briefing`** card with the summary text. With the AI layer available, the run's status line reads **`Gemma 4 enabled`**.
  - **Fail signs:** no `Briefing` card; a crash; results blocked.
  - **Evidence:** screenshot of the `Briefing` card with the `Gemma 4 enabled` status line.
- [ ] **Action:** Click **`Explain for admin`** with **no analysis run yet**.
  - **Expected:** the Briefing card text is exactly **`Run an analysis first.`** — no crash, no traceback.
  - **Fail signs:** any error card; a frozen window.
  - **Evidence:** screenshot of the `Run an analysis first.` Briefing.
- [ ] **Action:** (Fallback / never-blocks check) Stop or uninstall Ollama, relaunch, run an analysis, and click **`Explain for admin`** again.
  - **Expected:** a `Briefing` card still appears (templated summary), and the run's status line reads **`regex parse (offline)`**. The status line reflects **AI availability for that analysis** (set at analysis time) — it does not track each individual Explain click. Analysis is never blocked by missing AI.
  - **Fail signs:** no Briefing at all; an error blocks the rendered results; the missing AI prevents analysis from running.
  - **Evidence:** screenshot of the `regex parse (offline)` status with results still fully rendered.

---

## 6. Privacy / local-data QA

> These checks prove the app processes only aggregate, section-level data, performs
> no network IO outside the explicit live-build paths, and persists nothing it was
> not told to. They map to PRD N1–N4. Keep pytest expectations count-agnostic — only
> failures/errors matter.

- [ ] **Action:** Turn **Wi-Fi OFF**, relaunch `python3 app.py`, and click **`Load demo data`**.
  - **Expected:** demo analysis still runs and renders fully — the demo and file-pick paths require no network.
  - **Fail signs:** the demo fails or hangs waiting on network.
  - **Evidence:** screenshot of demo results with Wi-Fi off. (Backed by `test_engine_run_does_no_network_io`, which sabotages `httpx.Client` / `urllib.request.urlopen` / `socket.socket` / `socket.create_connection` and still completes.)
- [ ] **Action:** Run an **in-app** live build (category 3, network back ON), then list your working directory and `~/Downloads` before and after.
  - **Expected:** the in-app live fetch leaves **no** workbook behind — `app.py` `Api.fetch_live` writes the workbook into a `tempfile.TemporaryDirectory()` that is deleted on exit. Only the explicit CLI `--out` from §3b (default `data/live_LAMC.xlsx`) should produce a persisted file.
  - **Fail signs:** a `*.xlsx` appears in the working directory or Downloads after an in-app fetch.
  - **Evidence:** directory listing before/after showing no new file.
- [ ] **Action:** Run the privacy gate: `python3 -m pytest tests/test_privacy_invariants.py -q`
  - **Expected:** ends with `<N> passed` and no `failed`/`error` (the passed count grows as invariants are added — do not freeze it). Confirms: PII columns (`Instructor`/`ID`/`Name`/`Email`) and their values never reach the results JSON; a live-source instructor name is dropped at mapping and is absent from the frames, the written workbook, and engine results; `engine.run` completes with every network primitive sabotaged and byte-identically to a normal run.
  - **Fail signs:** any `failed`/`error`; a PII column name or value asserted into the results JSON.
  - **Evidence:** terminal tail showing the pass line.
- [ ] **Action:** (No-telemetry spot check) Confirm the engine carries no network client: `python3 -c "import inspect, engine; s=inspect.getsource(engine); print('httpx' in s, 'urllib' in s, 'requests' in s)"`
  - **Expected:** prints `False False False` — the engine has no network/telemetry surface (also enforced by `test_engine_module_does_not_import_network_libs`).
  - **Fail signs:** any `True`.
  - **Evidence:** terminal line showing `False False False`.

---

## 7. Signing / notarization QA — DEFERRED (out of scope for this RC)

> **Status (read first):** Apple code-signing and notarization are **out of scope
> for this RC.** Signing/notarization **scaffolding** now exists
> (`scripts/sign_notarize_macos.sh` — opt-in Developer ID signing + notarization;
> `scripts/sign_windows.ps1`; `packaging/entitlements.mac.plist`;
> `docs/PRODUCTION_RELEASE_CHECKLIST.md`), but it is **not run for this RC** and is
> blocked on external Developer ID / signing credentials (`MAC_SIGN_IDENTITY` +
> `MAC_NOTARY_PROFILE`). The default build (`scripts/build_macos.sh`) does **not**
> sign, so `dist/SchedulePlanner.app` ships **UNSIGNED and UN-NOTARIZED**, for
> **internal testers only**. The checks below therefore **confirm the documented
> unsigned state** rather than confirm a signature: for an unsigned RC, a
> *missing/ad-hoc signature* and a *Gatekeeper rejection* are the **PASS** outcomes.
> Run them on the packaged `.app` from §4.

- [ ] **Action:** Confirm the build advertises its unsigned state — in the `./scripts/build_macos.sh` output (§4), find the line printed after `Build complete: ...`.
  - **Expected:** the final line reads exactly `(Unsigned build — see BUILD.md 'macOS Gatekeeper bypass' for first launch.)`
  - **Fail signs:** that note is missing, or the script claims the app is signed/notarized (it must not — `build_macos.sh` does not sign).
  - **Evidence:** terminal tail showing the `(Unsigned build — ...)` note under the `Build complete:` line.
- [ ] **Action:** Inspect the bundle's signature: `codesign -dv --verbose=4 dist/SchedulePlanner.app`
  - **Expected (the PASS for an unsigned RC):** `code object is not signed at all`, OR an **ad-hoc** signature only (no `Authority=Developer ID Application` line, no `TeamIdentifier`). Either confirms the documented unsigned state.
  - **Fail signs (for THIS RC):** output shows a real `Authority=Developer ID Application: ...` chain or a `TeamIdentifier` — that would mean the bundle was signed, which is **not** part of this RC; investigate where the signature came from.
  - **Evidence:** terminal output of `codesign -dv` showing no Developer ID authority.
- [ ] **Action:** Confirm Gatekeeper rejects the unsigned bundle: `spctl -a -t exec -vv dist/SchedulePlanner.app`
  - **Expected (rejection IS the PASS here):** `rejected` with a `source=no usable signature` / unsigned / "not notarized" style reason. An unsigned, un-notarized app is supposed to be rejected.
  - **Fail signs (for THIS RC):** `accepted` `source=Notarized Developer ID` — that would mean the app is notarized, which is out of scope for this RC; do not expect or claim it.
  - **Evidence:** terminal output showing the `rejected` verdict and reason.
- [ ] **Action:** Confirm the sanctioned first-launch path is the Gatekeeper bypass, not a signature. Cross-check that §4 (and `BUILD.md` "macOS Gatekeeper bypass") is the only documented way to launch.
  - **Expected:** first launch is unblocked only via the §4 bypass (Finder right-click → **Open**, System Settings → **Privacy & Security** → **Open Anyway**, or `xattr -dr com.apple.quarantine /path/to/SchedulePlanner.app`). No signing step is run for this RC.
  - **Fail signs:** the runbook or build implies a signature/notarization makes the bypass unnecessary (it does not — the default build does not sign; the signing scripts are not run for this RC).
  - **Evidence:** a note in the QA log that the app launched via the §4 bypass, consistent with the unsigned state above.

> **What flips this category once signing is actually run (NOT done for this RC):**
> when `scripts/sign_notarize_macos.sh` is run with real **Apple Developer ID**
> credentials (it wraps `codesign --options runtime` + `packaging/entitlements.mac.plist`,
> then `xcrun notarytool submit ... --wait` and `xcrun stapler staple`, and reports
> success only when notarization is *Accepted* and `stapler`/`spctl` pass), the
> expected outcomes invert — `codesign -dv` would show the Developer ID `Authority`
> chain and `spctl` would report `accepted` `source=Notarized Developer ID` with no
> bypass needed. That requires credentials this RC does not have, so the unsigned
> outcomes above remain the PASS. See `docs/PRODUCTION_RELEASE_CHECKLIST.md`.

---

## 8. Windows / Linux QA — DEFERRED (not run on the macOS RC host)

> **Out of scope for the macOS RC sign-off.** PyInstaller is **not** a
> cross-compiler, so each OS bundle must be built **on that OS**.
> `scripts/build_windows.ps1` and `scripts/build_linux.sh` were authored but **NOT
> executed or verified on the macOS dev host**, and no `dist/` artifact exists here.
> These bundles must be built and QA'd **on Windows / Linux respectively by a per-OS
> tester**. Like the macOS `.app`, any Windows/Linux bundle is also **unsigned /
> un-notarized**. Every item below is marked deferred so it is unmistakable that
> none of it gates the macOS RC.
>
> **Source of truth:** `docs/CROSS_PLATFORM_BUILD.md` (per-OS build command,
> backend/runtime requirements, and the manual GUI checklist).

**Per-OS gotchas to brief the tester on (all from `docs/CROSS_PLATFORM_BUILD.md`):**

- **`--add-data` separator differs:** `;` on **Windows** (`--add-data "ui.html;."`) vs `:` on **Linux/macOS** (`--add-data 'ui.html:.'`). The wrong separator silently fails to bundle `ui.html` / the demo workbook, so they go missing at runtime.
- **Windows runtime:** pywebview uses the EdgeChromium / **WebView2** backend; the target needs the **WebView2 Runtime** (ships with Win11 / current Win10; else install the Evergreen WebView2 Runtime). Without it the window stays blank.
- **Linux runtime:** pywebview needs a **GTK (WebKit2GTK)** *or* **Qt (QtWebEngine)** backend installed **before** building, or it raises at startup.

### 8a. Windows

- [ ] **(deferred — not run on macOS RC host) Action:** On a Windows host, set up the venv (per the script header) and run `.\scripts\build_windows.ps1`.
  - **Expected:** the build finishes and prints `Build complete: <RepoRoot>\dist\SchedulePlanner\SchedulePlanner.exe`. The one-dir artifact exists at **`dist\SchedulePlanner\SchedulePlanner.exe`**.
  - **Fail signs:** `expected dist\SchedulePlanner\SchedulePlanner.exe was not produced.`; a PyInstaller error; a `PyInstaller is not installed` message.
  - **Evidence:** terminal tail showing `Build complete: ...`.
- [ ] **(deferred — not run on macOS RC host) Action:** In Git Bash / WSL on the Windows host, run `./scripts/verify_build_resources.sh dist/SchedulePlanner` (note: the one-dir path, not a `.app`).
  - **Expected:** `PASS:` lines for `ui.html`, `lamc_data.xlsx`, and the OR-Tools native lib (`ortools*.dll`), ending with `RESULT: all required resources present in dist/SchedulePlanner`.
  - **Fail signs:** any `FAIL:` line; `RESULT: <n> missing resource(s) ...`; non-zero exit.
  - **Evidence:** terminal tail showing the PASS result.
- [ ] **(deferred — not run on macOS RC host) Action:** Launch `.\dist\SchedulePlanner\SchedulePlanner.exe` and run the per-OS MANUAL GUI checklist from `docs/CROSS_PLATFORM_BUILD.md`.
  - **Expected:** no console window + a native window; `ui.html` renders the **Choose data file** and **Load demo data** buttons; **Load demo data** runs analysis on the bundled workbook and renders full-time + part-time cohort results with no file picking; **Choose data file** opens the OS-native picker and accepts `.xlsx`; missing Ollama does not block analysis; the app exits cleanly.
  - **Fail signs:** a **blank window** (almost always the WebView2 Runtime is missing — install the Evergreen WebView2 Runtime and relaunch); any checklist item fails; a console window pops up alongside the app.
  - **Evidence:** screenshots of the demo results and the file picker on Windows.

### 8b. Linux

- [ ] **(deferred — not run on macOS RC host) Action:** On a Linux host, install a pywebview backend first (GTK: `gir1.2-webkit2-4.1` + `python3-gi` + `pip install 'pywebview[gtk]'`, **or** Qt: `python3-pyqt5.qtwebengine` + `pip install 'pywebview[qt]'`; package names vary by distro), set up the venv, then run `./scripts/build_linux.sh`.
  - **Expected:** the build finishes and prints `Build complete: <RepoRoot>/dist/SchedulePlanner/SchedulePlanner`. The one-dir artifact exists at **`dist/SchedulePlanner/SchedulePlanner`**.
  - **Fail signs:** `error: expected dist/SchedulePlanner/SchedulePlanner was not produced.`; a PyInstaller error; a `PyInstaller is not installed` message.
  - **Evidence:** terminal tail showing `Build complete: ...`.
- [ ] **(deferred — not run on macOS RC host) Action:** Run `./scripts/verify_build_resources.sh dist/SchedulePlanner`.
  - **Expected:** `PASS:` lines for `ui.html`, `lamc_data.xlsx`, and the OR-Tools native lib (`libortools*.so*`), ending with `RESULT: all required resources present in dist/SchedulePlanner`.
  - **Fail signs:** any `FAIL:` line; `RESULT: <n> missing resource(s) ...`; non-zero exit.
  - **Evidence:** terminal tail showing the PASS result.
- [ ] **(deferred — not run on macOS RC host) Action:** Launch `./dist/SchedulePlanner/SchedulePlanner` and run the per-OS MANUAL GUI checklist from `docs/CROSS_PLATFORM_BUILD.md`.
  - **Expected:** no terminal window + a native window; `ui.html` renders the **Choose data file** and **Load demo data** buttons; **Load demo data** renders full-time + part-time cohort results with no file picking; **Choose data file** opens the OS-native picker and accepts `.xlsx`; missing Ollama does not block analysis; the app exits cleanly.
  - **Fail signs:** a **startup crash about a missing web view** (no GTK/Qt backend installed — install one and relaunch); any checklist item fails; a terminal window pops up alongside the app.
  - **Evidence:** screenshots of the demo results and the file picker on Linux.

---

## Known limitations

- **Live enrollment is zero.** The live LACCD schedule API returns `Cap Enrl = Tot Enrl = Wait Tot = 0`, so the **`Modality mismatch`** and **`Under-supply`** detectors are **inert on live data**. They activate only when the IR PeopleSoft enrollment export arrives (real-data enrichment — deferred; not a shipped engineering milestone; see `docs/M8_QA_REPORT.md`). The activation is fixture-scoped: the live-schedule ↔ IR `(term, CRN)` join is **not validated on real data**, and today's committed fixtures match zero live sections, so even a real `--enrollment` run stays inert. The committed **synthetic** sample `files/lamc_sample_enrollment.xlsx` (not real IR data) proves the detectors *can* fire on a self-consistent fixture.
- **Prerequisites blank on a bare live build.** A default live build runs without prerequisite ordering. Live prereq enrichment is available **opt-in** via `--elumen-live` (real, public eLumen catalog API), but it is **approval-gated** — not relied on for production until Terms-of-Use + rate-limit + data-handling sign-off (`docs/eLUMEN_LIVE_USAGE.md`), and bounded by cache/throttle/page-cap/backoff guardrails. The offline `--elumen-fixture` flag remains a separate **synthetic** stand-in (a committed self-defined DNF→CNF map).
- **Program Mapper:** one program per run (no bulk export); it requires a browser User-Agent + campus Origin or it returns HTTP 403.
- **N7 — synchronous solve.** The solver runs synchronously inside the app's bridge calls, so a very large dataset can briefly freeze the window while it runs.
- **Unsigned app.** `dist/SchedulePlanner.app` is unsigned and un-notarized (internal testers only); first launch needs the Gatekeeper bypass (§4), and the unsigned state is verified in §7. Opt-in signing/notarization **scaffolding** exists (`scripts/sign_notarize_macos.sh`, `packaging/entitlements.mac.plist`, `docs/PRODUCTION_RELEASE_CHECKLIST.md`) but is **not run** for this RC — it is blocked on external Developer ID credentials. The bundle is large (~800 MB) by design.
- **Windows / Linux bundles** are prepared (recipes/scripts exist — see `docs/CROSS_PLATFORM_BUILD.md`) but were **not built or verified** on the macOS host — PyInstaller is not a cross-compiler. They are QA'd separately in §8.
