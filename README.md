# EdgeSched

A deterministic scheduling engine that finds why community-college cohorts
can't finish 2-year programs on time and recommends the minimum schedule
changes to fix it. Ships as a desktop app (pywebview) backed by an OR-Tools
CP-SAT solver. Works on real LACCD data or a bundled synthetic demo.

## Quick Start

### Install

```bash
pip install -r requirements.txt
```

### Run the headless analysis

```bash
# Uses the bundled demo workbook (files/lamc_data.xlsx):
python3 engine.py

# Or point at your own workbook or a folder of three CSVs:
python3 engine.py path/to/workbook.xlsx
python3 engine.py path/to/csv-folder/
```

Prints JSON: bottleneck analysis + per-program, per-cohort completion plans
and minimum fixes.

### Run the desktop app

```bash
python3 app.py
```

Opens a native window (pywebview) with the full interactive UI. Three ways to
get data in, all routed through the same `engine.run`:

- **Choose data file** — pick a `.xlsx` workbook (or a folder of the three
  CSVs) with the OS-native file dialog.
- **Load demo data** — one click; analyzes the bundled
  `files/lamc_data.xlsx` with no file picking.
- **Build from live LACCD** — fetches the public schedule + Program Mapper
  APIs in-app (campus / terms / program), writes a throwaway workbook to a temp
  dir, analyzes it, and renders the engine results plus the live-only
  reconciliation and inert-detector panels. Nothing is persisted to disk.

Data-derived strings are HTML-escaped before render (`escapeHtml` in
`ui.html`), and the optional AI status is probed without ever blocking
analysis.

### Build a workbook from live LACCD data

```bash
python3 build_live_workbook.py --campus LAMC --program "Biology" \
    --terms 2264,2266,2268 --out data/live_LAMC.xlsx
```

Pulls from two **public, unauthenticated** LACCD APIs — the class schedule
(`services.laccd.edu/apps/api/classschedule`) and Program Mapper
(`b.api.programmapper.com`) — and writes a ready-to-use three-sheet workbook
(`--out` defaults to `data/live_LAMC.xlsx`). Use the resulting `.xlsx` with
`engine.py` or drag it into the desktop app.

The command above is also the exact recipe for a **representative multi-term
sample**: Biology AS-T at Mission College across the three currently-published
terms (`2264, 2266, 2268`). It prints a structured JSON report (campus, terms,
program, `section_count`, reconciliation, inert detectors, engine results)
after the human banner, so the output is machine-readable.

**Live reality — what one run actually produces:**

- **One program per run.** Program Mapper has no bulk export; the tool resolves
  the *first* program whose title or award matches `--program` and returns its
  default-pathway course list. Run it once per program you care about.
- **Term set.** `--terms` defaults to the three currently-published terms
  (`schedule.DEFAULT_TERMS = 2264, 2266, 2268`). Each term is a separate
  schedule API call; widen or narrow with a comma list.
- **Honest gaps (surfaced, not hidden).** The schedule API has **no
  enrollment/capacity/waitlist counts** and **no prerequisites**, so two
  detectors are **inert** on live data: `modality_mismatch` and `under_supply`
  never fire, and the solver runs without prerequisite ordering. The report's
  `inert_detectors` field names each one with its `reason` and the `remedy`
  that would activate it (IR PeopleSoft enrollment export for counts; eLumen
  for prerequisites).
- **Program Mapper requires a browser User-Agent** (and the campus Origin), or
  it returns HTTP 403. `sources/http.py` always sends one and wraps a 403 in a
  clear `SourceHTTPError` that names the likely cause.

The live network path is exercised by `tests/test_live_roundtrip.py`
(`pytest -m live`, deselected by default). The same chain is proven **offline**
in `tests/test_live_offline_pipeline.py`, which replays real API responses
captured once into `tests/fixtures/` — so the most fragile dependency is
testable without a network and fails loudly on schema drift.

### Transfer GE (Cal-GETC / IGETC / CSU GE)

Add a lower-division **transfer GE pattern** to the plan with `--transfer-goal`
(also a dropdown in the desktop app's live form):

```bash
python3 build_live_workbook.py --campus LAMC --program "Biology" \
    --terms 2264,2266,2268 --transfer-goal igetc --out data/live_LAMC.xlsx
```

Choices: `none` (default, major courses only), `cal-getc`, `igetc`, `csu-ge`.
The pattern's per-area requirements are intersected with the live sections and
with ASSIST's transferable-course lists
(`/api/transferability/courses`, public + unauthenticated) for the campus, so
each GE area resolves to a concrete offered course, a "pick one from this area"
reserve, or — honestly — a flag (`no ASSIST data` / `not offered this window`).
Areas already satisfied by a major requirement are shown as **met by your
major**, not double-scheduled.

> [!IMPORTANT]
> **The shipped GE patterns are DRAFTS pending a content review.** The
> per-area counts/units in `data/ge_patterns/*.json` are best-effort
> placeholders, **not** verified policy — so every build prints a *“Draft —
> unverified”* notice (CLI banner and the desktop GE panel) until a qualified
> reviewer signs each file off. The banner self-clears the moment a pattern's
> `reviewed_by` field is filled in. **Three things a qualified reviewer (e.g. an
> LACCD articulation officer / counselor) must address before these plans can be
> trusted as authoritative:**
>
> 1. **Fill in the sign-off fields.** Each file ships with
>    `"source": "REPLACE WITH OFFICIAL … STANDARDS URL"` and blank
>    `"reviewed_by"` / `"reviewed_on"`. A blank `reviewed_by` is the gate flag
>    meaning *no human has verified this pattern*. The review fills in the real
>    standards URL, the reviewer's name, and the date.
> 2. **Verify the area rules against the official standard.** The counts, unit
>    minimums, subarea splits, and lab/discipline attributes (e.g. IGETC Area 3
>    = 3 courses / 9 units, 3A & 3B each ≥ 1; Area 5 lab required in 5C; Area 4
>    ≥ 2 disciplines) are plausible but must match the official
>    IGETC / CSU GE-Breadth / Cal-GETC policy exactly. Note that **Cal-GETC
>    (2025-26) is replacing IGETC + CSU GE-Breadth** for most students — part of
>    the review is deciding which patterns are still valid for the target catalog
>    year vs. legacy (e.g. IGETC Area 6 is *Languages Other Than English*, while
>    Cal-GETC Area 6 is *Ethnic Studies* — don't conflate them).
> 3. **Confirm the area codes still match ASSIST.** ASSIST tags courses at
>    *subarea* granularity (`2A`, `4B`, `6A`, `5C`), so the resolver reconciles a
>    pattern's parent code (`2`, `6`) onto ASSIST's finer codes automatically
>    (see `sources/ge.py:_assist_courses_by_area`). If a future ASSIST coding
>    change breaks that mapping, an area will surface `no ASSIST data` — the
>    signal to re-check the codes.

### Regenerate the bundled synthetic demo

```bash
python3 generate_synthetic.py --out files/lamc_data.xlsx
```

Recreates the three-sheet demo workbook with planted bottlenecks so the
engine has something interesting to find.

### The committed enrollment sample (`files/lamc_sample_enrollment.xlsx`)

A second committed workbook, `files/lamc_sample_enrollment.xlsx`, is a
**synthetic stand-in that mirrors the column shape of the real IR PeopleSoft
enrollment export** (populated `Cap Enrl` / `Tot Enrl` / `Wait Tot`). It exists
because the **live** public LACCD schedule API returns those counts as `0`, so
the `modality_mismatch` and `under_supply` detectors are **inert on live data**
until the real IR export arrives (PRD M4). This sample lets those two detectors
actually fire — proving they work end to end — without waiting on the IR
delivery and without any student-level data (it carries no PII). It is
regression-tested in `tests/test_sample_enrollment_fixture.py` (IR shape,
planted-bottleneck snapshot, the detectors firing, deterministic regeneration
with a content-matched committed fixture) and feeds the e2e determinism check.
Run it through the engine like any workbook:

```bash
python3 engine.py files/lamc_sample_enrollment.xlsx
```

**Production-candidate status (synthetic path).** This synthetic IR-shaped path
is validated end to end: the five required IR columns are present, there is no
student-level data and no instructor PII, both enrollment-driven detectors
(`modality_mismatch`, `under_supply`) fire and are *count-driven*, and the
fixture regenerates deterministically. It remains a **synthetic stand-in, not a
real PeopleSoft export.** Validating the live-schedule ↔ real-IR `(term, CRN)`
join needs a real export with overlapping terms/CRNs, which is **unavailable and
not required for this pilot** (tracked in
[#17](https://github.com/johnnyrobot/edgesched/issues/17)). Today's committed
schedule (term `2268`) and enrollment (terms `{2248, 2252}`) fixtures share no
`(term, CRN)`, so a real `--enrollment` run matches zero sections and the two
detectors stay inert — by design, asserted by the zero-match guard in
`tests/test_detector_activation.py`.

### Run the test suite

```bash
python3 -m pytest -q                 # 148 passed, 3 deselected — fast, no network
python3 -m pytest -m live            # the 3 network-gated integration tests
./scripts/run_qa.sh                  # green-suite gate (asserts the 3 live tests stay deselected)
```

`scripts/run_qa.sh` is the single QA gate: it runs the offline suite and only
exits 0 when pytest passes *and* exactly the three `live`-marked tests were
deselected. The headless coverage (perf N5/N6, privacy N1–N4, determinism N11,
dead-path sweep, live-source error paths) is exercised by that gate.

## Optional integrations

### AI briefings / prereq parsing (Ollama + Gemma)

`llm_assist.py` uses a local Gemma model via Ollama to parse messy
prerequisite text and write plain-English schedule briefings. The engine
falls back gracefully to a rule-based template if Ollama is absent — no
setup required for core functionality. The AI layer talks to Ollama over
HTTP (no extra Python dependency) and the schedule itself is always produced
by the deterministic solver, never the model.

The model is `llm_assist.MODEL`, set to the published edge tag `gemma4:e2b` —
the on-device model this tool is built around. Note that despite the "e2b"
label the first-run download is **~7 GB**, so the initial pull is sizable and
slow. Install Ollama and pull it if you want the AI layer:

```bash
ollama pull gemma4:e2b
```

Heavier swaps such as `gemma4:e4b` or `gemma4:31b` are valid where more RAM
is available — set `MODEL` accordingly and pull the matching tag. Tag
matching is exact, so the configured tag must itself be installed
(`ollama list`); a different tag of the same family (e.g. an installed
`gemma4:31b` when `MODEL` is `gemma4:e2b`) does **not** count as present.

Whether the model is found is detected automatically:

- **model present** — prerequisite parsing and the dean briefing use Gemma.
- **Ollama or model absent / un-pulled** — both degrade silently: prereq
  parsing uses the regex parser in `engine.py`, and `explain()` returns a
  templated summary. An un-pulled model is correctly treated as absent
  (thanks to tag-exact matching), so it never errors — it just falls back.

The `ai_status()` / `setup_ai()` hooks in `app.py` surface this state in the
desktop UI; `setup_ai()` pulls the configured model with a one-time
`ollama pull` on first run.

### Neo4j graph layer (reference prototype, not wired in)

`legacy/load_neo4j.py` is an early reference prototype that sketches loading
the scheduling data into a Neo4j graph for advanced graph queries and
dashboards. It is not wired into the engine, app, or live
pipeline, and is not runnable as-is (it carries hardcoded sandbox paths). It
is retained for reference only — treat it as design notes, not an installable
feature.

## Architecture

```
live LACCD data ──► build_live_workbook.py
                          │
                          ▼
    files/lamc_data.xlsx (or user-supplied file)
                          │
                  engine.py  (OR-Tools CP-SAT solver)
                          │
            ┌─────────────┼─────────────┐
        app.py         JSON out      llm_assist.py
      (desktop UI)   (headless)   (optional AI layer)
```

The schedule is always produced by the deterministic solver. The LLM layer
only parses messy text and writes explanations — it never decides the
schedule.

## Packaging (desktop binary)

The app ships as a single PyInstaller bundle (no Python install on the target
machine). **macOS is built and verified**; Windows and Linux are *prepared* but
not produced on the macOS dev host.

```bash
./scripts/build_macos.sh                          # -> dist/SchedulePlanner.app
./scripts/verify_macos_build.sh                   # headless resource/Mach-O checks
```

`--collect-all ortools` is the load-bearing flag (it bundles the OR-Tools
native libs). Full flag-by-flag rationale, the frozen native-stack smoke test,
and the macOS Gatekeeper bypass are in `BUILD.md`.

## Docs index

- `BUILD.md` — verified macOS PyInstaller build.

## Demo data

The bundled demo (`files/lamc_data.xlsx`) has deliberately planted problems:

- **Business AS-T** — clean; official map valid, completes in 4 terms.
- **CS AS-T / Biology AS-T** — official Program Mapper sequence is broken
  (a required course mapped to a term it is never offered), but the solver
  finds a corrected 4-term path.
- **Engineering AS-T** — genuinely impossible: a 3-deep prerequisite chain
  locked to Fall can't fit in two years. The solver reports the single change
  that unblocks it: add one ENGR 102 section in Spring.

## Notes

- No student-level data anywhere. Instructor fields returned by the live
  schedule API are dropped during mapping — they never reach the workbook,
  results, or AI layer.
- Term season is derived from the term code (ends in 8 = Fall, 2 = Spring,
  matching Fall 2024 = 2248). Adjust in `engine.py` if your coding differs.
- `legacy/` contains early prototype scripts (`analyze_bottlenecks.py`,
  `solve_schedule.py`, `load_neo4j.py`) retained for reference; they are not
  part of the product.
