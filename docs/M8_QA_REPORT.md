# M8 QA Report — EdgeSched

Final m8 reconciliation report. It records **what was verified headlessly**
(with the exact command to reproduce each claim), **what can only be verified
manually** (and why), and a **PRD F/N coverage matrix** flagging which
requirements are tested vs. untested-headlessly.

- Scope: the merged `main` including engineering milestones `m1`–`m6` and all of
  `m8`.
- Suite baseline at time of writing: **148 passed, 3 deselected** (offline);
  the 3 deselected are the `live`-marked network tests.
- Environment observed: macOS (Darwin arm64), Python 3.13, `python3` on PATH
  (no bare `python`). Network was reachable for the live run captured in
  `docs/live_smoke_2026.md`; the offline suite needs no network.

Every command below was run before being listed here. Where a command prints a
measured number, the number shown is what was observed on the dev host; solver
wall-clock varies by machine but the budgets (N5 < 30 s, N6 < 10 s) hold with
large margin.

---

## A. Verified headlessly (automated, reproducible)

### A1. Full offline suite — 148 passed / 3 deselected

```bash
python3 -m pytest -q
```

Observed: `148 passed, 3 deselected in ~9.5s`. The 3 deselected are the
`live`-marked tests in `tests/test_live_roundtrip.py` (deselected by default via
`pytest.ini`).

The single green gate wraps this and additionally asserts the deselected count
is exactly 3 (so a silently-added/removed live test trips it):

```bash
./scripts/run_qa.sh
```

Observed tail: `148 passed, 3 deselected` then `QA gate PASS (live deselected: 3)`.

### A2. Performance — N5 (full analysis < 30 s) and N6 (per-program solve < 10 s)

```bash
python3 -m pytest tests/test_performance.py -s -q
```

Observed prints:

```text
[N5] engine.run on 1044 sections x 8 terms, 22 programs: 0.191s (budget 30.0s)
[N6] worst single-program (DEEP) cohort wall clock: 0.017s over 18 courses (budget 10.0s); double_solve_fired=True
```

N5 runs at ~1,000 sections × 8 terms (PRD's "typical LAMC volumes"); N6 measures
the worst single-program solve including the minfix double-solve. Both pass with
large margin. The fixtures are built by `tests/perf_fixture_builder.py`; the
test also asserts the N5 fixture is genuinely at scale (so the budget cannot be
met by a trivially small input).

### A3. Determinism — N11 (byte-identical output for identical input)

```bash
python3 -m pytest tests/test_determinism_e2e.py -q
```

Observed: `5 passed`. Covers: live-pipeline analysis byte-identical across runs
(via injected fixture client), `engine.run` on the enrollment sample
byte-identical, CSV-folder input matching the equivalent `.xlsx`, the solver
pinning its deterministic parameters (`random_seed=42`,
`num_search_workers=1` — confirmed in `engine.solve_cohort`), and the LLM
parser not changing the produced plan. The per-FR engine backbone
(`tests/test_engine_features.py`) also asserts `engine.run` is deterministic.

### A4. Privacy — N1–N4 (no student data, no PII, no network in the engine, no telemetry)

```bash
python3 -m pytest tests/test_privacy_invariants.py -q
```

Observed: `8 passed`. Covers: PII columns absent from results (workbook and
CSV-dir inputs), the bundled-demo results carrying no PII tokens, `engine.run`
completing with **every network primitive sabotaged** (`httpx.Client`,
`urllib.request.urlopen`, `socket.socket`, `socket.create_connection` all
monkeypatched to raise) — proving N3/N4 — and that the sabotaged offline run is
byte-identical to a normal run. Structural guards assert the `engine` module's
source references no `httpx`/`urllib`/`requests`, and that the live pipeline
keeps all network behind an injected client.

### A5. Dead-path / sandbox sweep — zero hits

```bash
python3 -m pytest tests/test_no_dead_paths.py -q
```

Observed: `1 passed`. Scans active modules (`engine.py`, `app.py`,
`llm_assist.py`, `generate_synthetic.py`, `build_live_workbook.py`),
`sources/*.py`, `tests/*.py`, `legacy/*.py`, and **all committed Markdown**
(root + `docs/` recursively + `legacy/`) for a leaked sandbox home path; asserts
zero offenders. (This is why this report avoids that literal token.)

### A6. Live-source error paths + offline live pipeline

```bash
python3 -m pytest tests/test_live_offline_pipeline.py tests/test_live_source_errors.py -q
```

Observed: `19 passed`. The offline pipeline replays real API responses captured
once into `tests/fixtures/` (schedule subjects/listings + Program Mapper
home/group/program/map JSON), so the most fragile dependency is testable without
a network and fails loudly on schema drift. The error-path tests cover the
`SourceHTTPError` hierarchy (incl. the Program Mapper 403-without-browser-UA
case) and the app's `fetch_live` returning a readable `{"error": ...}` card
rather than raising — for HTTP 500, non-JSON bodies, source errors, no-match
programs, and blank/non-positive term codes (`tests/test_app.py`).

### A7. Engine runnable with zero arguments + clear errors on bad input

```bash
python3 engine.py
```

Observed: prints JSON with keys `terms_in_data`, `analysis`, `programs` for the
4 bundled programs (`AS-T-CSCI`, `AS-T-BUS`, `AS-T-BIOL`, `AS-T-ENGR`) — i.e.
the engine runs with no args against `files/lamc_data.xlsx`. The clear
`InputDataError` paths (non-workbook input, missing required column, missing CSV
in a folder) are asserted in `tests/test_engine_features.py`
(`test_non_workbook_input_raises_clear_error`, etc.).

### A8. macOS bundle resources + frozen native-stack smoke (build harness)

The PyInstaller build itself was run and verified on the dev host (PyInstaller
6.20.0, Python 3.13, ortools 9.15, pandas 3.0, macOS arm64) per `BUILD.md`; it
produces `dist/SchedulePlanner.app`. The headless **resource/Mach-O verifier**
and its **negative control** (which proves the check actually bites) run without
needing a desktop session:

```bash
./scripts/verify_macos_build.sh --self-test     # negative control only; no bundle needed
```

Observed: `OK: resource check correctly reported FAIL (exit non-zero) on missing lib`.

After a build, the full check (bundle exists, Mach-O executable, `ui.html` +
`files/lamc_data.xlsx` + `libortools*.dylib` bundled) is:

```bash
./scripts/build_macos.sh                 # produces dist/SchedulePlanner.app (needs venv + pyinstaller)
./scripts/verify_macos_build.sh          # headless checks 1-3 from BUILD.md
./scripts/verify_build_resources.sh dist/SchedulePlanner.app
```

> NOTE: this worktree has no `dist/` (the bundle is not committed — `dist/` /
> `build/` are gitignored artifacts). Re-run `scripts/build_macos.sh` on a macOS
> host to regenerate it before `verify_macos_build.sh`'s full (non-self-test)
> mode. The build + verifier were green on the dev host that produced `m5`.

A separate **`--console` smoke harness** (not the shipped windowed app) proves
the frozen OR-Tools + pandas + `engine.run` stack loads end to end inside a
bundle, independent of the GUI:

```bash
./scripts/build_macos_console_smoke.sh
```

It builds a console bundle with the same `--collect-all ortools` +
`--add-data files` flags and runs `from ortools.sat.python import cp_model`,
`pandas.read_excel` on the bundled workbook, and `engine.run(<bundled xlsx>)`
inside the frozen process. (Requires PyInstaller in the venv; same as the build.)

### A9. Captured live smoke transcript

The live LACCD network path (real schedule + Program Mapper APIs → workbook →
`engine.run`) was captured to `docs/live_smoke_2026.md` (2026-05-29, Darwin
arm64). It reached the live sources, wrote a workbook of 2,659 sections across
3 terms for Biology AS-T (7 courses, 7/7 reconciled), and ran the engine. The
**network-independent equivalent** that is green regardless of connectivity:

```bash
python3 -m pytest -q tests/test_live_offline_pipeline.py
```

The live-marked round-trip itself (network required) is:

```bash
python3 -m pytest -m live -q -rs
```

Observed on the dev host with network up: `2 passed, 1 skipped, 148 deselected`
— the skip is the AI round-trip (`test_live_explain_against_real_ollama`) which
skips when Ollama/the model is absent (see B3). With no network the two LACCD
round-trips will error/skip; the offline pipeline (above) is the durable guard.

---

## B. Manual-only (cannot be verified headlessly)

### B1. GUI window pixels — dev app and packaged `.app`

**Why not headless:** `app.py` is a pywebview window backed by the system
WebKit/Cocoa view; clicking buttons and seeing rendered cards requires an
interactive desktop session. The JS-bridge methods are unit-tested
(`tests/test_app.py`, 20 tests), but the actual rendered window is not.

**Manual steps (dev):**
```bash
python3 app.py
```
Then confirm: window opens; **Load demo data** renders full-time + part-time
cohort cards with no file picking; **Choose data file** opens the native picker
and accepts an `.xlsx`; **Build from live LACCD** (with network) fetches and
renders the reconciliation + inert-detector panels; missing Ollama does not
block analysis.

**Manual steps (packaged):**
```bash
open dist/SchedulePlanner.app
# or, for logs: ./dist/SchedulePlanner.app/Contents/MacOS/SchedulePlanner
```
Then run the GUI checklist in `BUILD.md` ("Manual GUI checklist"): no terminal
window, `ui.html` renders, demo + file-pick work, clean exit. First launch of
the unsigned bundle is blocked by Gatekeeper — use the bypass in `BUILD.md`.

### B2. Real Windows / Linux builds

**Why not headless:** PyInstaller is **not a cross-compiler** — each OS artifact
must be built on that OS. The dev host is macOS, so the Windows/Linux bundles
were **not produced or verified** here; only their recipes/scripts exist.

**Manual steps:** on a Windows host run `scripts\build_windows.ps1` (needs the
WebView2 Runtime); on a Linux host run `scripts/build_linux.sh` (needs a GTK or
Qt WebKit backend). Then verify with `scripts/verify_build_resources.sh
dist/SchedulePlanner` and the per-OS GUI checklist — both in
`docs/CROSS_PLATFORM_BUILD.md`. The `--add-data` separator is `;` on Windows,
`:` on Linux.

### B3. Live Ollama AI (F19–F22)

**Why not headless on this host:** these require a running Ollama daemon with the
configured model (`llm_assist.MODEL = "gemma4:e2b"`) pulled. The presence
detection, tag-exact matching, and **templated fallback when absent** are fully
unit-tested with mocks (`tests/test_llm_assist.py`, 27 tests); but a real Gemma
parse/briefing is gated. The live AI round-trip skips cleanly when the model is
absent:

```bash
python3 -m pytest -m live -q -rs
# -> SKIPPED tests/test_llm_assist.py: "Ollama or the configured model is not available"
```

**Manual steps:** install Ollama, `ollama pull gemma4:e2b`, start the daemon,
then re-run the line above (the skip becomes a pass) and exercise **Build/Load →
explain** in the app to see a Gemma-written briefing instead of the templated
fallback. One-click download (F20) is `Api.setup_ai` → `llm_assist.ensure_model`
(`ollama pull`); the pull path is mocked in tests but the real download needs
Ollama installed.

### B4. Apple code-signing / notarization

**Why not headless / out of scope for v1:** the shipped `.app` is **unsigned and
un-notarized** (internal testers only). Signing needs an Apple Developer ID
certificate in a real Keychain and Apple's notarization service.

**Manual steps:** `codesign` with a Developer ID, then `notarytool submit`
+ `stapler staple`. See `BUILD.md` "macOS Gatekeeper bypass" for the
internal-tester workaround (`xattr -dr com.apple.quarantine ...` or Finder
right-click → Open). Out of scope for internal v1.

---

## C. PRD F/N coverage matrix

Legend: **H** = verified headlessly (automated test/command above) · **M** =
manual-only (see §B) · **mock-H** = logic verified headlessly with mocks, the
real external dependency is manual.

### Functional requirements

| FR | Requirement | Status | Where |
|---|---|---|---|
| F1 | Accept single `.xlsx` (3 sheets) | H | `test_f1_xlsx_loads` |
| F2 | Accept directory of 3 CSVs | H | `test_f2_csv_folder_loads` |
| F3 | Validate schema, clear errors | H | `test_non_workbook_input_raises_clear_error` + missing-column/CSV tests |
| F4 | Rotation-gap detection | H | `test_f4_rotation_gaps_fires` |
| F5 | Single-section risk | H | `test_f5_single_section_fires` |
| F6 | Modality mismatch (low fill) | H | `test_f6_modality_mismatch_fires`; **inert on LIVE data** (counts=0) — driven by `files/lamc_sample_enrollment.xlsx` |
| F7 | Under-supply (waitlist) | H | `test_f7_under_supply_fires`; **inert on LIVE data** — same sample drives it |
| F8 | Validate official map, report violations | H | `test_f8_official_map_violation` |
| F9 | Fastest feasible plan per program/cohort | H | `test_f9_f10_both_cohorts_all_programs` |
| F10 | Multiple cohort profiles (FT/PT) | H | `test_f9_f10_both_cohorts_all_programs` |
| F11 | Minimum new offerings to unblock (minfix) | H | `test_f11_engr_minfix` |
| F12 | Prerequisite logic (AND of OR-groups) | H | `test_f12_prereq_ordering` |
| F13 | Unit caps per term | H | `test_f13_unit_caps_respected` |
| F14 | Season-specific availability | H | `test_f14_season_constraints_respected` |
| F15 | Per-program cards, both cohorts side by side | M | data produced & tested (F9/F10); the **rendered cards** are GUI (§B1) |
| F16 | Bottleneck diagnostic panel | M | data produced & tested (F4–F7); the **rendered panel** is GUI (§B1) |
| F17 | Plain-language admin briefing | H | `test_f17_briefing_fallback` (templated path); LLM-written variant = mock-H / §B3 |
| F18 | Show official-map violations w/ corrected plan | H | `test_f18_corrected_plan_solves` + F8 |
| F19 | Detect Gemma via Ollama | mock-H | `test_model_present_*`, `test_ollama_running_*`; real daemon §B3 |
| F20 | One-click model download on first run | mock-H | `test_ensure_model_*` (pull mocked); real `ollama pull` §B3 |
| F21 | Gemma parses unstructured prereq text | mock-H | `test_make_prereq_parser_*`, `test_parse_prereq_text_*`; real model §B3 |
| F22 | Gemma writes the admin briefing | mock-H | `test_explain_uses_llm_when_available` + fallback tests; real model §B3 |
| F23 | Graceful degrade: regex prereq + templated briefing | H | `test_parse_prereq_forms`, `test_f17_briefing_fallback`, `test_explain_falls_back_*` |

### Non-functional requirements

| NFR | Requirement | Status | Where |
|---|---|---|---|
| N1 | No student-level data | H | `tests/test_privacy_invariants.py` (A4) |
| N2 | No PII; instructor IDs stripped | H | `tests/test_privacy_invariants.py` (A4) |
| N3 | No network except optional localhost Ollama | H | network-sabotage test (A4) + engine import guards |
| N4 | No telemetry/analytics/external logging | H | network-sabotage + no-network-libs guards (A4) |
| N5 | Full analysis < 30 s (~1,000 sections × 8 terms) | H | `test_n5_full_analysis_under_budget` (~0.19 s observed) |
| N6 | Per-program solve < 10 s | H | `test_n6_single_program_solve_under_budget` (~0.017 s observed) |
| N7 | **UI remains responsive during analysis** | **NOT verified headlessly — likely violated at scale** | see note below |
| N8 | Single-binary distribution per platform | macOS H (resources/Mach-O, A8) / Win+Linux M (§B2) | `verify_macos_build.sh`; cross-platform unbuilt |
| N9 | No databases/servers required | H | runs from `python3 engine.py` / bundle; no service deps |
| N10 | Offline operation after setup | H (engine) / M (packaged GUI) | network-sabotage test (A4); packaged offline run §B1 |
| N11 | Deterministic solver output | H | `tests/test_determinism_e2e.py` (A3) |
| N12 | Bundled synthetic dataset | H | `files/lamc_data.xlsx` + byte-identical regeneration tests |

#### N7 — UI responsiveness: flagged

`app.py` runs `engine.run` **synchronously inside the JS-bridge calls**
(`Api.analyze` / `Api.load_demo` / `Api.fetch_live` all call `engine.run`
directly, not on a worker thread). At the N5 scale (~1,000 sections) the solve
is fast (~0.2 s measured), so in practice the window is unlikely to feel frozen
on typical data — but there is **no asynchronous offload**, so a slow solve (a
much larger dataset, or a worst-case minfix double-solve) **will block the
window** while it runs. N7 is therefore **not satisfied as an architectural
guarantee** and is not headlessly testable (it is a perceived-latency property
of the live GUI event loop). Remedy if it becomes a problem: run `engine.run` on
a background thread and post results back to the bridge.

### Other PRD items needing the real IR export or a human

- **M4 / live data gap:** on **live** public-LACCD data the schedule API returns
  `Cap Enrl = Tot Enrl = Wait Tot = 0`, so **F6 (`modality_mismatch`) and F7
  (`under_supply`) are inert**, and prerequisites are blank (eLumen not wired),
  so the solver runs without ordering. Both detectors *are* proven to fire via
  the committed IR-shaped sample `files/lamc_sample_enrollment.xlsx`. Activating
  them on real data is gated on the **IR PeopleSoft enrollment export** —
  external, pending. `build_live_workbook.analyze_live` surfaces this honestly in
  its `inert_detectors` report field rather than presenting an empty result as a
  clean bill of health.
- **M5–M7 (PRD §12):** published bottleneck report, a schedule change traceable
  to the tool, and cross-department adoption are **human/adoption deliverables**,
  not code — open by definition until the college acts.

---

## Reproduce everything

```bash
python3 -m pytest -q                                   # A1: 148 passed / 3 deselected
./scripts/run_qa.sh                                    # A1: green gate
python3 -m pytest tests/test_performance.py -s -q      # A2: N5/N6 with measured times
python3 -m pytest tests/test_determinism_e2e.py -q     # A3: N11
python3 -m pytest tests/test_privacy_invariants.py -q  # A4: N1-N4
python3 -m pytest tests/test_no_dead_paths.py -q       # A5: sandbox sweep
python3 -m pytest tests/test_live_offline_pipeline.py tests/test_live_source_errors.py -q  # A6
python3 engine.py                                      # A7: zero-arg engine run
./scripts/verify_macos_build.sh --self-test            # A8: verifier negative control
python3 -m pytest -m live -q -rs                        # A9/B3: live (network/Ollama gated)
```
