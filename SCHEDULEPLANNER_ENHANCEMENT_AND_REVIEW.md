# SchedulePlanner — Adversarial Code Review + Enhancement Roadmap

_Synthesis deliverable for the repo owner. Date: 2026-06-12. Branch: `main`._

This report merges (A) an adversarial code review whose findings were independently
verified (3/3 or 2/3 reviewer votes shown per item) and (B) web-grounded enhancement
research whose every source claim was fact-checked. Where a fact-check marked a claim
`partly` / refuted / unverifiable, the affected idea is **downgraded and the reason is
stated** (see §5 Honesty Ledger). All recommendations are filtered against the project's
five non-negotiable doctrines (determinism, honesty/no-silent-drops, evidence-honesty,
privacy, no-copyleft-in-bundle).

---

## 1. Executive summary

SchedulePlanner is a deterministic LACCD schedule/completion-feasibility analyzer: an OR-Tools
CP-SAT core (`engine.run`, byte-identical, single-worker, seed 42) with every advisory
signal attached **outside** the engine in `build_live_workbook.analyze_live/analyze_import`.
The architecture is sound and the honesty culture is real. The review surfaced **one
high-severity honesty regression**, several **medium correctness/resilience** gaps, and
two **O(N²) hotspots** that only bite on full-college imports.

**Most important risks (fix first).**
1. **Silent drops break the honesty doctrine on three surfaces.** Room double-bookings /
   over-capacity render in the UI but vanish from the exported HTML report and the chat
   assistant (HIGH). Separately, every section keeps only its **first** meeting block, so a
   course meeting on two day/time patterns silently loses the rest — corrupting the
   conflict, grid, and equity detectors and blocking honest contact-hour math.
2. **A determinism hole the doctrine rests on but does not enforce.** The solver uses a
   **wall-clock** 10 s budget (`engine.py:373`), so a slow machine could return a different
   (FEASIBLE-not-OPTIMAL) plan; and the in-`engine.run` LLM prereq parser runs at Ollama's
   default temperature with no seed, so the derived CNF can vary run-to-run.
3. **Schema drift on the four undocumented LACCD endpoints fails opaquely**, and a notarized
   desktop binary currently ships with **no dependency CVE scan, no SBOM, and no build
   provenance**.

**Headline opportunities (high leverage, buildable, doctrine-clean).**
- **F8 First-Year Gateway-Momentum detector** — the single most-cited early-completion
  lever, computable from data already ingested, as a labeled structural proxy.
- **CCCCO Data Mart course success/retention adapter** — the closest thing to a *measured*
  course outcome SchedulePlanner can get without a student-record export, public and aggregate.
- **Deterministic-time solver budget + non-optimal flag** and **pinned LLM parse** — close
  the two byte-identity holes above and *strengthen* doctrine 1.
- **Supply-chain hardening** (pip-audit gate, SLSA provenance, SBOM) — near-zero-config fits
  over the existing hash-pinned lock.
- **Tolerant-reader schema-drift guard + shared retry/backoff** — turn silent mis-parse into
  a loud, named failure and fix the wrong-API 403 remedy.

Nothing here pretends to a student-level completion label; the **#17 hard ceiling** (no
outcome label exists in any LACCD source) stands, and every proxy stays labeled as one.

---

## 2. Adversarial code review (by severity)

### HIGH

**H1 — Room double-booking / over-capacity findings are silently dropped from the HTML report and chat grounding (shown only in the UI).** `report_export.py:647-681, 765-777` (and `chat_assist.GROUNDERS`). _Votes: 3/3._
- **Problem.** `build_live_workbook.py:1265-1267` injects `analysis['room_conflicts']` (and
  `analysis['room_capacity']` when a facility table is supplied); `ui.html:1195-1196`
  renders both. But `report_export.py` has **zero** reference to either key and `chat_assist`
  has no grounder for them, so an imported schedule's room conflicts vanish from two of three
  surfaces with no note — a direct violation of doctrine 2 ("no silent drops on **any**
  surface").
- **Fix.** Add a `_room` renderer to `report_export.SECTION_RENDERERS` listing
  `room_conflicts[].summary` / `room_capacity[].summary` (escaped, truncation surfaced like
  the other sections) and the `found` / `over_capacity_found` counts in `_detectors`; add a
  `_ground_room` grounder to `chat_assist.GROUNDERS`.

### MEDIUM

**M1 — Secondary meeting patterns are silently dropped on both ingestion paths.** `sources/schedule.py:75` (live) and `sources/schedule_import.py:190` (import). _Votes: 3/3._
- **Problem.** Each section is reduced to a **single** meeting (`meeting = meetings[0] if
  meetings else {}`); `meetings[1:]` vanish with no counter. A course that meets MW 10:00 **+**
  F 14:00 loses every block but the first. This produces **false negatives** in `time_conflict`
  (F1), `mutual_exclusions`/grid conformance (F3), equity windows (F6), and
  `engine._hard_conflict_pairs` (a real overlap on a dropped block is never seen), and
  grid/morning-compression evaluate only the first block's start time. The import path dedups
  on `(term, crn)` with `if key in seen: continue` while asserting "counts/room constant within
  a section" — but **days/times are not**.
- **Fix.** Emit one section RECORD per meeting block (or fold all blocks into the meeting list
  `timeblocks` consumes). If collapsing is kept, surface a count of merged secondary rows in
  the ingest summary so the drop is honest. _(This is also enhancement E1 — the same change
  unblocks the contact-hour detector; see §4.)_
- **STATUS (2026-06-13) — RESOLVED, including the gated engine wiring.** All blocks are now
  **captured** at ingest: `schedule.py`/`schedule_import.py` carry a `meetings` list and the
  import summary counts `multi_block_sections` (the drop is honest). The **outside-engine**
  detectors read the full footprint via `timeblocks.section_meeting` / `iter_section_blocks` /
  `section_days` — room conflicts, F1 buildability, F3 grid_pressure, F6 equity_exposure. The
  last consumer, the **inside-engine** `engine._hard_conflict_pairs`, is now wired too:
  `mapping.build_sections_df` writes an optional **`Meetings`** column (JSON list of
  `{days,times}` blocks — **days/times ONLY**, never instructor/room, per the privacy doctrine;
  empty for single-block sections), and `_hard_conflict_pairs` decodes it via `section_meeting`
  when the cell is present and non-empty, else falls back to `Days`/`Times`.
  - **Gated/disclosed output change.** On a workbook carrying the `Meetings` column, two required
    courses that clash **only on a secondary block** are now placed in different terms — so the
    canonical `engine.run` plan **can differ** from the pre-feature (block[0]-only) plan. This is
    the sanctioned deviation from the byte-identical-`engine.run` doctrine for this feature.
  - **Determinism preserved.** Byte-identical for any workbook **without** the column
    (demo / IR / CSV / hand-built) and for **single-block** sections even with the column; the
    JSON encoding is fully canonical — **blocks AND keys sorted**, compact separators — so a
    section's blocks arriving in a different source order from the live API still yield a
    byte-identical cell.
  - **Fail-open robustness.** `engine.run` accepts any user-openable `.xlsx`, so the decode is
    guarded: a corrupt / non-list / hand-edited `Meetings` cell degrades to the visible
    `Days/Times` (block[0]) instead of crashing the solve — never silently-wrong data (block[0]
    is exactly what the Days/Times columns already show), mirroring the fail-open tolerance of
    `parse_times` / `on_grid`. _(Both hardenings came out of the post-implementation adversarial
    review; the malformed-cell crash was a 3/3-confirmed finding.)_
  - **Guards.** `test_determinism_e2e.test_multiblock_conflict_is_deterministic_and_moves_the_plan`
    (deterministic **and** non-vacuous — stripping the column changes the plan) and
    `test_live_offline_pipeline.test_live_workbook_carries_multiblock_meetings_no_instructor`
    (end-to-end through `write_workbook` + structural privacy). The committed **LAMC Biology
    golden is unchanged** — its 5 multi-block sections do not flip a required-course pair, so the
    real fixture's plan is identical; the change only moves a plan where a secondary block truly
    clashes. 730 tests pass (QA gate: 5 live deselected).

**M2 — "~N years" estimate hardcodes 2 terms/year, contradicting the engine's cadence-scaled horizon.** `report_export.py:224` and `ui.html:1157`. _Votes: 3/3._
- **Problem.** Both surfaces compute `years = ceil(terms_used / 2)`. But `engine.solve_cohort`
  sizes the horizon as `H = round((horizon/2) * len(cadence))` where `len(cadence)` can be 3 or
  4. The default live fetch `schedule.DEFAULT_TERMS = [2264, 2266, 2268]` decodes to {Spring,
  Summer, Fall} → a 3-season cadence, so a 2-academic-year full-time plan can use up to 6 terms
  and the report/UI mislabel it "~3 years." Triggers on the ordinary live path whenever
  Summer/Winter is present.
- **Fix.** Surface `terms_per_year` (= `len(cadence)`) from `solve_cohort` into the result and
  divide by it: `ceil(terms_used / terms_per_year)`.
- **STATUS (2026-06-13) — RESOLVED.** `engine.solve_cohort` now adds
  `"terms_per_year": len(cadence)` to every cohort result — the EXACT divisor the solver already
  uses to scale the horizon (`H = round((horizon/2) * len(cadence))`), so the surfaces no longer
  re-derive (and mis-derive) it. `report_export._cohort` and `ui.html cohortCard` compute
  `ceil(terms_used / (terms_per_year or 2))`; the `or 2` default keeps every pre-feature /
  hand-built dict and the existing 2-season goldens **byte-identical** (which is why the new
  `engine.run` field produced **zero golden churn**). The field is deterministic (a pure function
  of the offered seasons) and **changes no plan**, so `engine.run` stays reproducible run-to-run.
  Guarded by `test_engine_features.test_solve_cohort_surfaces_terms_per_year` (3-season → 3,
  2-season → 2) and two `test_report_export` tests (3-season 6 terms → "~2 years", not "~3";
  absent field → legacy 2-season default). 733 tests pass (QA gate: 5 live deselected). A focused
  adversarial review (semantics / determinism / completeness) returned **0 confirmed findings**.

**M3 — F6 equity score counts GE as fully schedulable regardless of the constrained window, while EQUITY_LABEL claims the opposite.** `equity_exposure.py:46-55, 169-171, 197-204`. _Votes: 2/3._
- **Problem.** `EQUITY_LABEL` states the audit "re-runs the buildability audit using ONLY the
  offered sections that fit a constrained window," but the GE contribution rides the
  **unfiltered** pre-sweep `ge_coverage`: lines 168-171 filter the major sections (`kept = [...]`)
  yet pass `ge_coverage=ge_coverage` unchanged into `buildability_report`; the fallback at
  197-199 does the same. `buildability.ge_denominator` then reads `offered_eligible` (the
  pre-sweep count over **all** sections), so a GE-goal program's evening/online/two-day score is
  overstated and GE collapse is invisible — an undisclosed false-method claim on a proxy number.
- **Fix.** Recompute a window-filtered `ge_coverage` against `kept` before the constrained run,
  **or** pass `ge_coverage=None` and score major-only; and amend `EQUITY_LABEL` to disclose
  that GE-area schedulability is not re-evaluated per window. _(2/3 vote: one reviewer read the
  windowed-GE intent as out of scope; the label/behavior mismatch is the load-bearing part
  either way.)_
- **STATUS (2026-06-13) — RESOLVED via the complete (window-the-GE) option.** `sources/ge.py
  resolve()` now carries `offered_eligible_ids` (the PRE-sweep list of offered articulating
  course ids) on every coverage area — additive, ignored by `ge_denominator`/F1. New
  `equity_exposure._window_ge_coverage(coverage, kept)` re-counts each area's `offered_eligible`
  = the ids whose `mapping._norm` is in the windowed `kept` courses (exact match — `offered` is
  `mapping._norm`'d on both sides; no mutation; fail-open for None / legacy / id-less coverage).
  `_assess_archetype` passes the **windowed** coverage to both `buildability_report` and the
  per-program `audit_program` fallback, so the constrained score now reflects GE collapse under
  the window; `EQUITY_LABEL` updated so the claim is true by construction (scoped to the GE
  schedulability *folded into the score*).
  - **Baseline unchanged:** `equity_exposure_report`'s baseline still uses the UNWINDOWED
    coverage, so baseline F6 still equals the F1 score. Only the per-archetype constrained runs
    window the GE.
  - **No regression / no churn:** the new `area_cov` field is additive and never rendered, so
    F1 / GE / report / chat goldens are byte-identical; F6 + `ge.resolve` run OUTSIDE `engine.run`,
    so determinism is untouched.
  - **Guards:** `test_ge_resolver.test_resolve_exposes_pre_sweep_offered_eligible_ids`;
    `test_equity_exposure.test_window_ge_coverage_recounts_offered_eligible_against_kept`
    (re-count / zero-on-empty / no-mutate / legacy pass-through); and
    `test_equity_exposure.test_constrained_run_windows_ge_coverage` (a GE area whose only course
    is morning-only collapses the EVENING score below baseline — `assert 100 < 100` before the
    fix). 705 tests pass (QA gate: 5 live deselected). A focused adversarial review
    (windowing / baseline-regression / honesty-label / determinism) returned **0 confirmed
    findings**; the dismissed label-scope nits are covered by the "folded into the score" qualifier.

**M4 — `program_mapper.get_all_programs`: per-group payload is unguarded — schema drift raises a bare `AttributeError` instead of a named `SourceDataError`.** `sources/program_mapper.py:65-68`. _Votes: 3/3._
- **Problem.** The home-page payload is guarded (54-59), but the per-group
  `/program-groups/{gid}` `data` is used as `data.get("programs", [])` with no type check. If
  that endpoint drifts to a list/string/null, `.get` raises a bare `AttributeError` that escapes
  `get_json`'s named-error discipline — the exact loudness pattern every sibling guard was
  written to prevent.
- **Fix.** After the `get_json` call: `if not isinstance(data, dict): raise
  SourceDataError(...expected a JSON object, got {type}...)`, mirroring the existing
  program-maps guard at lines 98-102.
- **STATUS (2026-06-13) — RESOLVED.** Added the `isinstance(data, dict)` guard after the
  per-group `get_json` in `get_all_programs`, naming the endpoint + offending type
  (`program-groups/{gid} ({campus}): expected a JSON object, got {type}`), so a drifting
  per-group payload fails with a named `SourceDataError` instead of a bare `AttributeError` —
  the same loudness discipline as the home-page and program-map guards. **Type-only** by design:
  a dict that merely omits `programs` stays tolerated (the `.get("programs", [])` default → that
  group contributes nothing), preserving the legitimate empty-group response that
  `test_get_all_programs_empty_when_groups_present_but_empty` pins. Guards:
  `test_get_all_programs_raises_on_non_dict_program_group` (list payload → named error) and
  `test_get_all_programs_tolerates_group_without_programs_key` (key-less dict → `[]`, no raise).
  704 tests pass (QA gate: 5 live deselected). Isolated to `sources/program_mapper.py` — no
  overlap with the M1/M2/M3 branches; trivial mechanical guard, no adversarial review run.

**M5 — `schedule.fetch_sections`: subjects/courses element-level drift raises a bare `AttributeError` despite the list-type guard.** `sources/schedule.py:68-69`. _Votes: 2/3._
- **Problem.** `listing['subjects']` is guarded to be a list (60-67) but elements are not
  guarded to be dicts. A string/null/number subject or course element makes
  `subject.get('courses', [])` / `course.get('subject')` raise a bare `AttributeError`, the same
  opaque failure the list-type guard exists to avoid, one level deeper.
- **Fix.** `if not isinstance(subject, dict): raise SourceDataError(...subjects[i] is {type}...)`
  and likewise for `course`.
- **STATUS — RESOLVED** (branch `fix/schedule-element-drift-guard`, off `main` 21b0f7f; TDD;
  709 tests pass / 5 live deselected; ruff clean; 3-lens adversarial review run). Added
  `schedule._require_dict(value, where, path)` — raises a NAMED `SourceDataError` carrying the
  endpoint (`LACCD schedule listing endpoint (LAMC 2268)`), a dotted JSON breadcrumb
  (`subjects[3].courses[1].sections[0].meetings[0]`) and the offending `type().__name__`.
  Guards wired at **every** nesting level the function iterates — not just the named
  subject/course but `section`, `relsection`, and `meeting` elements too (the identical
  bare-`AttributeError` class lived one level deeper). `_iter_sections` now yields
  `(section, path)` tuples and validates section/relsection elements; `fetch_sections`
  validates subject, course and the first meeting. Determinism: for every well-formed payload
  the record list is byte-identical pre/post (adversarially diffed old-vs-new over the committed
  81-record LAMC fixture + 7 edge payloads — equal, key-order preserved); source IO already runs
  outside `engine.run`. **Review caught one should-fix (3/3 lenses), now fixed:** the meeting
  guard was gated on `if meeting:` (element truthiness), so a FALSY non-dict meeting element
  (`null`/`0`/`""`) slipped the guard and still bare-crashed at `meeting.get(...)` — asymmetric
  with the sibling levels that catch `None`. Changed to gate on `if meetings:` (the list) and
  pinned with `test_fetch_sections_raises_on_null_meeting_element`. Tests: 6 RED→GREEN drift
  cases (subject/course/section/relsection/truthy-meeting/null-meeting) + a non-overfire test
  (empty `meetings` → empty day/time record, no false raise). **Scope boundary (documented):**
  CONTAINER-type drift (a level being a non-list scalar, e.g. `{"courses": 5}` → bare
  `TypeError` from `enumerate`, or `meetings` a dict → bare `KeyError` on `[0]`) is intentionally
  left to **E6** (the tolerant-reader contract guard) — verified pre-existing on `main` and
  NOT worsened by this change (the fix only ever improves or matches: a list-of-non-dict meeting
  that bare-crashed before now raises a named error). Isolated to `sources/schedule.py` +
  `tests/test_schedule_client.py` — no overlap with the M1/M2/M3/M4 branches.

**M6 — `_hard_conflict_pairs` is O(N²) over every active course plus a per-row `iterrows`, unscoped to program-required courses.** `engine.py:183-206` (computed once at 428). _Votes: 2/3._
- **Problem.** It walks the full workbook: `active.iterrows()` (slow per-row Series build) to
  build `by_course`, then a pure O(N²) double loop over all distinct courses calling
  `pairwise_hard_conflict`. On the institutional "ALL" import that is N²/2 pair checks over
  thousands of courses, and `solve_cohort` later **discards** every pair where both endpoints are
  not program-required (334-337) — most of the work is computed then thrown away.
- **Fix.** Scope the pair computation to the union of program-required (closure) courses before
  the O(N²) loop, and replace `iterrows()` with a vectorized `groupby`/`itertuples` build.
  **Preserve** the empty-when-no-Days/Times default (191-192) for byte-identity.

**M7 — `engine.analyze` re-filters the full sections frame once per required course (O(courses × sections)) instead of a single groupby.** `engine.py:230-251`. _Votes: 2/3._
- **Problem.** The loop does `d = active[active['CLASS'] == cid]` plus per-course `groupby`/sums
  for every required course; for the "ALL" pseudo-program that is every offered course, i.e.
  repeated full-frame scans. `build_model` already demonstrates the groupby-once pattern at
  `engine.py:176`.
- **Fix.** Precompute `groups = dict(tuple(active.groupby('CLASS')))` (or aggregate
  Term-counts/Cap/Tot/Wait in one groupby) and index per course. Pure refactor, output unchanged.

- **STATUS — M6 + M7 RESOLVED TOGETHER** (one branch `perf/engine-hotpath-scoping-groupby`, off
  `main` 21b0f7f; both are `engine.py` hot-path perf; TDD; QA gate 705 pass / 5 live deselected;
  zero new lint vs main baseline; 3-lens adversarial byte-identity review run). **Both are PURE
  perf refactors — `engine.run` output is byte-identical**, proven by an old-vs-new differential
  (`json.dumps(old.run, sort_keys=True) == new.run`) over DEMO + conflict-with-irrelevant +
  GE-candidate + absent-course + 13 adversarial workbooks, plus 300 randomized `analyze` frames
  and 400 random `_hard_conflict_pairs` subsets — 0 divergences.
  - **M6** `_hard_conflict_pairs(sec, relevant=None)`: a new `relevant` param scopes the O(N²)
    pair scan to the union of every program's schedulable items (`closure(major) | concrete
    GE candidates`, computed once in `run()`), and `iterrows` → a column-`zip` build. `solve_cohort`
    already discards any pair not both-endpoints-in that program's `item_ids`, so skipping
    pairs touching a course no program schedules changes **no applied constraint**;
    `pairwise_hard_conflict` is a symmetric AND so list order is irrelevant. **Benchmark: 3001 ms
    → 3 ms (~1119×)** on a 1500-course institutional frame. Soundness proof (lens 3): for every
    program `item_ids ⊆ relevant_items` (0 violations observed), so no applied pair is dropped;
    extra pairs the union computes are filtered by the unchanged guard.
  - **M7** `analyze`: replaced the per-required-course full-frame boolean filter with a single
    groupby — but **filtered to `required` FIRST**: `groups = dict(tuple(active[active['CLASS']
    .isin(required)].groupby('CLASS')))`, `d = groups.get(cid, active.iloc[0:0])`. The review
    finding's literal `dict(tuple(active.groupby('CLASS')))` materializes a sub-frame per OFFERED
    course and was measured **2× SLOWER** for the common small-program case (a program needs ~40
    of thousands of offered courses); the filter-first variant is **2.0–2.1× FASTER in BOTH the
    small-program and "ALL" regimes** while staying byte-identical (non-required groups are never
    read; absent required course → empty same-columns frame). This deviation from the literal
    prescription is intentional and benchmark-justified.
  - **Tests:** TDD throughout — `test_hard_conflict_pairs_scoping_preserves_applied_pairs`
    (RED→GREEN on the new `relevant` param: `scoped ⊆ full`, relevant pair kept, irrelevant
    dropped), `test_run_scopes_conflicts_but_still_separates_program_courses` (e2e: irrelevant
    conflicting course doesn't change the plan), `test_analyze_handles_required_course_absent_from_sections`
    (empty-frame path). Existing `test_solver_separates_hard_time_conflict` /
    `test_solver_byte_identical_without_meeting_data` / F4–F7 analysis tests stay green.
  - **Review = 0 must-fix / 0 should-fix / 0 byte-identity breaks; 1 nit** (latent coupling, not a
    current break): M6's numeric-id equivalence relies on `_hard_conflict_pairs` comparing
    `str(CLASS)` against `relevant_items` on the SAME str-vs-raw basis `solve_cohort`'s `item_ids`
    guard uses. Sound today (relevant_items is built from the same raw Course IDs the guard uses);
    addressed with an INVARIANT comment in `run()` so a future typing change can't silently
    desync the two. Declined a numeric-id regression test — it would enshrine a pre-existing,
    out-of-scope quirk (numeric-only course-id conflicts are never enforced because `item_ids`
    stay raw-typed while endpoints are str-cast — unchanged from main).
  - **Overlap note:** both touch `engine.py`; M6 edits `_hard_conflict_pairs`, which the
    `feat/multiblock-meeting-conflicts` branch (PR #57) ALSO edits (its `_row_meeting`/Meetings-
    column read). Whichever merges second needs a small manual rebase combining the
    `relevant`-scoping + column-zip with the multi-block meeting read. M7 (`analyze`) does not
    overlap #57.

**M8 — QA-gate live-test count is dual-sourced and its locking test is stale-named ("three") while asserting five.** `tests/test_qa_gate.py:86-93, 126-137`; `scripts/run_qa.sh:27`. _Votes: 3/3._
- **Problem.** The deselected-live-test count is hard-coded in two unlinked places —
  `run_qa.sh`'s `EXPECTED_DESELECTED=5` and `KNOWN_LIVE_NODES` (5 entries) — and the meta-test
  meant to make that coupling explicit is named `test_live_set_is_exactly_the_three_known_nodes`
  with a docstring asserting "precisely the documented three." Both are stale (the set is 5), so
  the one signpost a maintainer reads while adding a live test actively misleads, and the two
  magic counts can drift.
- **Fix.** Rename the test (drop the count) and fix the docstring; derive the shell expectation
  from the source of truth: `EXPECTED_DESELECTED=$(python3 -c 'from tests.test_qa_gate import
  KNOWN_LIVE_NODES; print(len(KNOWN_LIVE_NODES))')`.
- **STATUS — RESOLVED** (branch `fix/qa-gate-derive-live-count`, off `main` 21b0f7f; TDD; ruff
  clean; gate re-run end-to-end → `QA gate PASS (live deselected: 5)`). Implemented exactly the
  prescribed fix plus a regression guard: (1) `scripts/run_qa.sh` now DERIVES
  `EXPECTED_DESELECTED="$(python3 -c 'from tests.test_qa_gate import KNOWN_LIVE_NODES;
  print(len(KNOWN_LIVE_NODES))')"` with a `[[ =~ ^[0-9]+$ ]]` sanity guard (fails loud, exit 2,
  if the import/derivation breaks); the hard-coded `EXPECTED_DESELECTED=5` literal is gone. The
  derivation runs after the python3/pytest availability checks (so a missing-pytest env gets its
  own clear message first), with `cwd` already `cd`'d to `REPO_ROOT` so the `tests` namespace
  package imports (no `tests/__init__.py` needed — verified). (2) Renamed
  `test_live_set_is_exactly_the_three_known_nodes` → `test_live_set_is_exactly_the_known_nodes`
  and rewrote the docstring to drop the stale "three" and name `KNOWN_LIVE_NODES` as the single
  source of truth; refreshed the set's header comment likewise. (3) Added
  `test_run_qa_derives_deselected_count_from_known_live_nodes` (RED→GREEN) pinning that the
  script imports `KNOWN_LIVE_NODES`, computes `len(...)`, and carries NO hard-coded
  `EXPECTED_DESELECTED=<digit>` literal — so the dual-source hazard cannot return. **Drift still
  doubly-caught** (verified by reasoning): the set-equality meta-test pins `KNOWN_LIVE_NODES ==`
  the real live collection while the script derives the count from `len(...)`, so an
  undocumented added/removed live test fails BOTH the meta-test (set mismatch) and the gate
  (count mismatch); even a count-preserving swap is caught by the set-equality assertion. No
  adversarial workflow run (test/infra hygiene, end-to-end-verified — same bar as M4). Isolated
  to `tests/test_qa_gate.py` + `scripts/run_qa.sh` — no overlap with the M1–M5 branches.

### LOW

**L1 — `get_json` hardcodes a Program-Mapper-specific 403 remedy that is emitted for every source.** `sources/http.py:59-64`. _Votes: 3/3._
- **Problem.** On any 403, `get_json` appends "Program Mapper requires a browser UA and the
  campus Origin" — but it is the shared transport for schedule, eLumen and ASSIST too. A 403 from
  eLumen, ASSIST (CSRF) or schedule names the wrong API and points at an Origin header those
  endpoints do not use.
- **Fix.** Thread an optional `forbidden_hint`/`origin_required` flag through `get_json` so each
  client supplies its own accurate 403 remedy; default to a generic UA hint. _(Folded into
  enhancement E7's per-source hint work; see §4.)_
- **STATUS — RESOLVED** (branch `fix/http-403-per-source-hint`, off `main` 21b0f7f; TDD; QA gate
  704 pass / 5 live deselected; ruff clean). Added a `forbidden_hint=None` param to `get_json`:
  the 403 message is now `"… the API rejected the request. {hint}"` where `hint` defaults to a
  **source-agnostic** generic browser-User-Agent note and is replaced verbatim when a caller
  passes its own. `sources/program_mapper.py` threads a module-level `_FORBIDDEN_HINT`
  ("Program Mapper requires a browser User-Agent and the campus Origin/Referer header for this
  site-content id.") through all four of its `get_json` calls, so PM keeps its accurate remedy
  while **schedule / eLumen / ASSIST no longer get the wrong Origin advice**. Notable: ASSIST's
  CSRF failure is HTTP **400** (refreshed+retried once), not 403, so a 403 there is a generic
  UA/block — the generic default is correct for it (no CSRF-specific 403 hint added). Smoke-tested
  the rendered message for all four sources. Tests (TDD): `test_get_json_403_default_hint_is_source_agnostic`
  (RED→GREEN: a `LACCD schedule` 403 names `User-Agent`, NOT `Program Mapper`/`Origin`),
  `test_get_json_403_uses_caller_supplied_forbidden_hint` (RED→GREEN: caller hint used verbatim),
  and `test_get_all_programs_403_names_program_mapper_origin_remedy` (PM 403 still names the Origin
  remedy). The shared-transport docstring was updated. This is the **bug** portion of enhancement
  **E7**; E7's retry/backoff + Retry-After + per-term fail-open remain as future work. Isolated to
  `sources/http.py` + `sources/program_mapper.py` (+ their tests) — no overlap with the M-branches.

---

## 3. Enhancement roadmap

### 3.1 Prioritized table

Type legend: **NEW** feature · **IMP** improve existing · **DATA** new data source · **INFRA**.
Effort S/M/L. Ranks group into P0 (do first) → P2; **G** = gated / disclosed-output-change.

| # | Idea | Type | Maps to | Effort | Impact | Honesty fit |
|---|------|------|---------|--------|--------|-------------|
| **P0 — highest leverage / closes a doctrine or review gap** |
| 1 | E1 Capture **all** meeting blocks (retire `meetings[0]` drop) | IMP | `sources/schedule.py`, `schedule_import.py`, `mapping.py` | S | Med→High | Removes a silent drop (D2); engine reads same columns → byte-identical |
| 2 | E2 Deterministic-time solver budget + non-optimal flag | INFRA | `engine.py:372-375`; flag in `analyze_live` | S | High | Strengthens D1 (machine-reproducible); advisory flag outside `engine.run` |
| 3 | E3 Pin in-engine LLM prereq parse (temp 0 + fixed seed) | IMP | `llm_assist._chat` / `parse_prereq_text` | S | High | Serves D1; structured pre-pass stays the byte-identity guarantee |
| 4 | E4 pip-audit OSV/PyPI CVE gate in CI over the hash-pinned lock | INFRA | `.github/workflows/ci.yml`, `release-build.yml` | S | High | Build-time only; **report/fail, never `--fix`** (ortools bump breaks goldens) |
| 5 | E5 SLSA build provenance via GitHub artifact attestation | INFRA | `release-build.yml` | S | High | Provenance metadata; proves origin not safety (honest framing) |
| 6 | E6 Tolerant-reader schema-drift contract guard + live canary | INFRA | new `tests/test_source_contracts.py`, fixtures, `ci.yml` | M | High | Makes silent drift loud (D2); covers M4/M5 root cause |
| 7 | E7 Shared retry/backoff + Retry-After + per-source 403 hint + per-term fail-open | IMP | `sources/http.py`, `schedule.py`, `program_mapper.py`, `assist.py` | M | High | Network edge; partial failure surfaced (D2); fixes L1 |
| 8 | E8 **F8** First-Year Gateway-Momentum detector | NEW | new detector in `ANALYSIS_DETECTORS`; reuses `buildability.py` | M | High | Labeled offering proxy; inert+remedy when gateway unidentifiable |
| 9 | E9 CCCCO Data Mart course success/retention adapter | DATA | new `sources/course_success.py` → F2/F5 detector | M | High | Aggregate, public; labeled MEASURED course outcome ≠ completion |
| **P1 — strong, buildable, mostly independent** |
| 10 | E10 Room conflicts → HTML report + chat (this **is** fix H1) | IMP | `report_export.py`, `chat_assist.py` | S | High | Closes a silent drop (D2) |
| 11 | E11 Infeasibility explainer (CP-SAT assumptions / MUS) | NEW | re-solve in `analyze_live`; `report_export`, `chat_assist` | M | High | Deterministic re-solve outside `engine.run`; structural only |
| 12 | E12 **F9** AB1705 corequisite co-availability detector | NEW | `sources/elumen.py` Co-Req + per-(course,term); new detector | M | High | Same-term co-offering structure only; inert+remedy |
| 13 | E13 Equity-disaggregated course-success **gap** → F6 + F7 | IMP/DATA | `course_success.py`, `equity_exposure.py`, `evidence.py` | M | High | Cell-suppressed aggregate; MEASURED gap ≠ student-level (see ledger) |
| 14 | E14 Minimal-perturbation recommender ("fewest changes to buildable") | NEW | extends `demand_supply.py` (F5), `buildability.py` | L | High | Deterministic lexicographic re-solve outside `engine.run`; recommends offerings |
| 15 | E15 **F10** Title 5 §55002.5 contact-hour conformance detector | NEW | new `contact_hours.py`; flips `grid_pressure` inert check | L | High | Conformance proxy (observed→implied hrs); ambiguous cases stay inert. **Depends on E1** |
| 16 | E16 CycloneDX SBOM at release + no-copyleft license gate | INFRA | build/release scripts | M | Med→High | Operationalizes D5; **CRA "obligation" framing dropped** (see ledger) |
| 17 | E17 Schema-constrained JSON for chat router + prereq parse (`format`) | IMP | `chat_assist.route`, `llm_assist._chat` | M | Med | Keeps `_validate_intent` trust gate; outside `engine.run` |
| 18 | E18 Indirect-prompt-injection hardening of grounded chat (OWASP LLM01) | IMP | `chat_assist.ANSWER_SYS`, context assembly | S | Med | Read-only, 4-lookup least-privilege preserved |
| **P2 — worthwhile, lower urgency** |
| 19 | E19 Answer-time groundedness guard (RAGAS-style, stdlib) | NEW | `chat_assist.chat` post-process | M | Med | Reinforces D2; ungrounded tokens surfaced with caveat |
| 20 | E20 IPEDS (Urban Institute) / Scorecard institution-context banner | DATA | new `sources/ir_context.py` | S | Med | Institution-aggregate, "not this schedule"; never feeds a score |
| 21 | E21 Tag gateway gatekeeper courses in F2 leaderboard | IMP | `cross_program_bottleneck.py` | S | Med | Structural flag on existing proxy counts |
| 22 | E22 Wire momentum/AB1705 into F7; give F6 its missing equity grounding | IMP | `evidence.py` `_CONDITIONS`/`CLAIMS` | S | Med | Vetted-only trust root; severity-graded firing |
| 23 | E23 Year-over-year rotation predictability index | IMP | `engine.analyze` rotation + `grid_pressure.py` | M | Med | Offering-pattern proxy; inert on single-year fetch |
| 24 | E24 Excess-units / terms-to-complete structural proxy (Vision 2030) | IMP | engine makespan (read-only) + `buildability` unit tallies | M | Med | Exact-for-map proxy; discloses integer unit truncation |
| 25 | E25 ASSIST public Agreements API (major-prep lists) | IMP | `sources/assist.py` → F1/F4 | M | Med | Structural articulation input; fail-open |
| 26 | E26 CCCCO REST API canonical college/TOP-code picker | INFRA | `program_lists.py`, `live_demand.py`, `app.py` | S | Med | Reference metadata; fail-open to current behavior |
| 27 | E27 What-if scenario harness (warm-started re-solve) | NEW | new `Api` method; deterministic re-solve outside `engine.run` | M | Med | Canonical plan never changes; structural deltas only |
| 28 | E28 Hash-pin build tools (PyInstaller, ruff) build-lock | IMP | `ci.yml`, `release-build.yml`, new `build-tools.lock` | S | Med | Toolchain integrity; version pins unchanged |
| 29 | E29 Reproducible-build `PYTHONHASHSEED` + frozen-tree hash | IMP | `build_macos.sh`, `verify_macos_build.sh` | M | Med | Packaging determinism; "reproducible tree" not "signed bundle" |
| 30 | E30 Harden pywebview JS↔Python bridge (CSP, local-only, escape) | IMP | `ui.html`, `app.py` | M | Med | UI/bridge only; reinforces D4 |
| 31 | E31 Conditional-request caching + VCR record/replay | INFRA | `sources/http.py`, fixtures | M | Med | Politer client; offline-reproducible; zero PII |
| 32 | E32 Academic-calendar / holiday set from LACCD PDF (draft-gated) | DATA | `sources/pdf_loader.py`, `grid_pressure.py` | L | Med | Draft-gated (`reviewed_by=''`); calendar-conformance proxy |
| 33 | E33 Offline grounded-answer eval/regression harness | INFRA | `tests/`, `run_qa.sh` opt-in marker | M | Low | Pure offline test infra; proxy-labeled fixtures |
| **Gated / disclosed-output-change (read §5 before adopting)** |
| G1 | E-G1 Lexicographic objective (retire the magic `100×last` weight) | IMP | `engine.py:367` | M | Med | **Changes canonical `engine.run` output** → ship only as disclosed golden regen |
| G2 | E-G2 `interleave_search` parallelism for institution-scale solves | INFRA | `engine.py:372-375` | M | Med | **Adopt only if it reproduces the committed golden bit-for-bit**; reject otherwise |
| G3 | E-G3 Model-availability ladder + active-model surfacing | IMP | `llm_assist.MODEL`/`ensure_model`, `refreshAI` | S | Med | Good idea; **tag names unverified** (likely `gemma3n:e2b/e4b`, not `gemma4`) |
| — | E-X Cal-PASS Plus / DataVista aggregate reader | DATA | new reader; context surface only | L | Med | MOU-gated; honest **partial** #17 path, never into a score |

### 3.2 Top ideas, with citations

**E1 / M1 — Capture all meeting blocks.** The live wire returns `meetings` as an **array** per
section (verified on [the LAMC listing endpoint](https://services.laccd.edu/apps/api/classschedule/listing/LAMC/2266),
which also carries `woi`/`dates`/`classType` the parser drops). Keeping only `meetings[0]` is a
silent truncation that biases F3 and structurally blocks honest contact-minute math. Carry a
`meetings[]` list (or a `meetings_extra` count) and surface how many sections had >1 block; the
engine keeps reading its single-meeting columns so byte-identity holds.

**E2 — Deterministic-time solver budget.** OR-Tools' proto states `max_deterministic_time` is
work-based and "correlated with the real time used by the solver," while `max_time_in_seconds`
is wall-clock and "starts at the Solve() call," and `random_seed` "only" reinitializes the RNG —
so seed 42 alone does **not** guarantee cross-machine identity under a wall-clock budget
([sat_parameters.proto](https://raw.githubusercontent.com/google/or-tools/stable/ortools/sat/sat_parameters.proto)).
Swap to `max_deterministic_time`, capture per-cohort status, and attach a `plan_not_proven_optimal`
advisory in `analyze_live` when any model returns FEASIBLE/UNKNOWN. Pin with a golden plan-hash.

**E3 — Pin the in-engine LLM parse.** `app.py:160-161` injects `llm_assist.make_prereq_parser()`
*into* `engine.run`, and `_chat` sets no options, so Ollama uses its default sampling temperature —
meaning the derived CNF (and thus solver constraints) can change run-to-run, in tension with D1.
Ollama's structured-output guidance is to "set the temperature to 0 for more deterministic" output
([docs](https://docs.ollama.com/capabilities/structured-outputs)), and its reproducible example
sets both `seed` and `temperature:0` ([api.md](https://github.com/ollama/ollama/blob/main/docs/api.md)).
Scope temp 0 + fixed seed to `parse_prereq_text` only. _(Honest residual: cross-machine GPU float
nondeterminism remains — the regex/`prereq_cnf` structured pre-pass, not the LLM, stays the
byte-identity guarantee.)_

**E4 — pip-audit CVE gate.** A notarized binary built from dozens of pinned deps currently has no
CVE scan. `pip-audit` is the PyPA-ecosystem auditor (Trail of Bits, with Google support), queries
the PyPI Advisory DB + OSV, and natively understands a `--generate-hashes` lock
([pip-audit](https://github.com/pypa/pip-audit)); running it in CI on every change is baseline
supply-chain practice ([Gabor](https://bernat.tech/posts/securing-python-supply-chain/)). Configure
it to **report/fail, never `--fix`** — a forced `ortools` bump would change the CP-SAT search path
and break the determinism goldens; any ortools advisory must go through a human-reviewed lock regen
+ golden re-pin.

**E5 — SLSA provenance.** `release-build.yml` runs with only `contents: read` and uploads an
unattested bundle; nothing cryptographically binds the published `.app` to this repo/commit.
`actions/attest-build-provenance` (with `id-token: write` + `attestations: write`) gives a
Sigstore-signed, keyless SLSA Build L2 statement, verifiable with `gh attestation verify`
([GitHub docs](https://docs.github.com/en/actions/concepts/security/artifact-attestations)). Honest
framing for release notes: attestation proves **origin**, not safety, and complements (does not
replace) Apple notarization.

**E6 — Schema-drift contract guard.** The repo already commits the perfect corpus
(`schedule_listing_LAMC_2268.json`, `pm_home_page_content_LAMC.json`,
`elumen_courses_LAMC_response.json`, `assist_*.json`) but no test asserts the parser-bound shape
against it. Best practice for APIs you do not own is the **tolerant reader** — read only the fields
you use against the actual wire, not the spec ([pattern](https://java-design-patterns.com/patterns/tolerant-reader/)) —
and **structural drift detection** comparing shape/type/nullability with severity grading
([drift](https://totalshiftleft.ai/blog/api-schema-validation-catching-drift)). Add a stdlib golden
test plus a schedule-only `continue-on-error` canary. This directly attacks the M4/M5 root cause.

**E7 — Shared retry/backoff + accurate 403 hints.** Today only `elumen_client` retries;
`schedule.fetch_sections` loops terms with no retry, so one transient 5xx nukes the whole fetch,
and `http.py` reuses the Program-Mapper 403 message for every caller (review L1). Lift
`_get_json_retrying`/`_RateLimiter` into `sources/http.py`: **honor `Retry-After`** when present,
else jittered exponential backoff to avoid retry storms
([Google Cloud retry strategy](https://docs.cloud.google.com/storage/docs/retry-strategy);
[MDN Retry-After](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Retry-After)).
Add a per-term `skipped[]` list (surfaced in `fetch_status`, never silently dropped) and a
per-source `forbidden_hint`.

**E8 — F8 Gateway-Momentum detector.** Add an `ANALYSIS_DETECTORS` entry testing whether a new
student *could* take a gateway college-level math **and** English within the first-year terms
(identifiable in the required/early set, offered on-season in year-1, conflict-free, non-dead).
CCRC's Early Momentum Metrics names gateway college-math+English in year one a leading indicator of
long-term success ([CCRC](https://ccrc.tc.columbia.edu/publications/early-momentum-metrics-college-improvement.html)),
and the 15/30 credit-momentum benchmark is associated with higher completion
([CCRC](https://ccrc.tc.columbia.edu/publications/momentum-15-credit-course-load.html)). Emit a
labeled structural proxy ("not a measured pass rate"); when gateways cannot be identified, go
**inert with reason + remedy**. _(Honesty caveat, per fact-check: these are **correlational** leading
indicators with selection effects, and CCRC's 2024 guided-pathways analysis found no gain in course
**success/pass** rates — frame momentum as an early predictor, not a proven causal lever; see ledger.)_

- **STATUS — SHIPPED** (branch `feat/gateway-momentum-detector`, off `main` 21b0f7f; TDD; QA gate
  723 pass / 5 live deselected; zero new lint; 3-lens adversarial review run). New `gateway_momentum.py`
  + `gateway_momentum_report(sections, *, program, horizon_terms)`, wired as the **6th** (append-only)
  `ANALYSIS_DETECTORS` entry with `_gateway_momentum_detector_entry`; runs OUTSIDE `engine.run`
  (determinism gate green, no golden regen — F8 isn't rendered in report/chat/ui yet). **Identification**:
  (1) `ge_requirements` recommended_course for Area 1A (English) / 2 (Math) → `via: ge_area_1A`,
  `transfer_level: area-defined`; (2) fallback to a required ENGL/MATH major course → `via: major_subject`,
  `transfer_level: unverified`. **Schedulability**: earliest-2-terms "year-1" window via
  `buildability.offered_by_course` + `_in_horizon` + `single_section_required`; obstructions =
  not-offered / offered-only-after-year-1 / single-section-in-window. Inert + remedy when no sections or
  neither gateway identifiable. Honest envelope: `GATEWAY_MOMENTUM_LABEL` (OFFERING proxy, not a measured
  completion rate) on every block; `not_assessed` lists placement/prereq blocking, seat/time-conflict
  feasibility, and student completion. **Tests**: 19 unit + a registry compute-threading pin (5→6) + an
  end-to-end `analyze_live` integration test (16 new + 3 updated detector-set assertions). **Adversarial
  review = 0 must-fix, 0 crashes, determinism PASS; 4 should-fix (all honesty) + 2 nits — should-fix all
  FIXED**: (a) the `major_subject` fallback was biased toward the BELOW-TRANSFER prereq (earliest-semester
  tiebreak) — changed to prefer the highest course number + `transfer_level: unverified` marker; (b) the
  registry entry reason no longer asserts unqualified "transfer-level" (carries the discipline-level
  caveat — it is the only prose surface until F8 is rendered); (c) `not_assessed` now discloses that
  `schedulable_year1` ignores seat status + first-year time-conflict feasibility; (d) tolerates
  `ge_requirements`/`courses` = None (was a `.get(k, [])`-only-on-missing-key TypeError, not live-reachable).
  Nit: a single-term fetch collapses the window → surfaced via `window_note`. Isolated to `gateway_momentum.py`
  + `build_live_workbook.py` (registry) + tests — no overlap with the M-branches. Follow-ups (deferred):
  render F8 in report_export/chat/ui; fold seat/time-conflict into the assessment; the E12/F9 AB1705
  corequisite co-availability check builds on this.

**E9 — CCCCO Data Mart course success/retention adapter.** The closest thing to a *measured* outcome
SchedulePlanner can get without a student-record export. The Credit Course Retention/Success report is
**aggregate** (no student rows), filterable by college/term/subject(TOP)/distance-ed, returns
Enrollment/Retention/Success Count + Rate, and exports CSV/Excel/Text — verified directly on
[the report page](https://datamart.cccco.edu/outcomes/course_ret_success.aspx) — and is
[public, no password](https://datamart.cccco.edu/). Build an offline reader (`sources/course_success.py`,
reusing `enrollment_ir._read_frame`) so F2/F5 escalate a course that is **both** supply-constrained
**and** historically low-success. Label it a MEASURED course outcome — explicitly **not** a
program-completion label and **not** this schedule's outcome; inert with remedy when no file is
provided.

**E11 — Infeasibility explainer (MUS).** Today the architecture flags only *that* a program needs
fixes, not *which* minimal set of requirements blocks it. CP-SAT ships an assumptions mechanism to
root-cause infeasibility, and a true minimal unsatisfiable set requires minimizing the weighted sum
of assumption literals
([troubleshooting.md](https://github.com/google/or-tools/blob/stable/ortools/sat/docs/troubleshooting.md));
SchedulePlanner already runs single-worker (the precondition). Guard each hard requirement with an
enforcement literal and re-solve **deterministically in `analyze_live`**, surfacing "these N
requirements together make the path unbuildable; relaxing any one restores feasibility."

**E12 — F9 AB1705 corequisite co-availability.** For transfer-level gateway courses, check whether a
corequisite **support** section is co-offered in the **same term** (read from eLumen leaf
`itemType=Co-Requisite`, already discriminated). AB1705 requires colleges to provide access to
academic support such as corequisites for transfer-level math/English, and transfer-level completion
rose sharply as access universalized
([CCCCO transfer-level dashboard](https://www.cccco.edu/About-Us/Chancellors-Office/Divisions/Educational-Services-and-Support/transfer-level-dashboard);
[implementation guide](https://www.cccco.edu/-/media/CCCCO-Website/Files/Educational-Services-and-Support/ab-1705-implementation-guide-3-14-23-a11y.pdf)).
Measure same-term **co-offering structure only**; inert+remedy when corequisite linkage is unknown.
_(Caveat, per fact-check: in California **direct placement** was the dominant lever, with corequisite
as one supported form — do not imply corequisite alone drove the gains, and the "must add sections/caps"
corollary is contingent, not asserted; see ledger.)_

- **STATUS — SHIPPED** (branch `feat/corequisite-coavailability`, stacked off the F8 branch
  `feat/gateway-momentum-detector` dd5b67a; TDD; QA gate **764 passed / 5 deselected**; ruff clean on all new code;
  determinism gate green, no golden regen — F9 isn't rendered in report/chat/ui yet). The 7th append-only
  `ANALYSIS_DETECTORS` entry (`corequisite_availability`). **eLumen coreq capture**: a SEPARATE walk
  (`elumen_client.corequisites_of` / `corequisite_map`; `course_record` now also emits `coreqs`) keeps the
  `itemType=Co-Requisite` leaves the prereq filter drops — the prereq dnf/CNF/coverage path is proven
  **byte-identical** old-vs-new (3-lens adversarial differential over all 7 fixture wrappers + the engine.py
  diff is empty). **Shared identification**: F8's gateway-id primitives were extracted to `gateway_common.py`
  (imported by both detectors; F8 behavior byte-identical across 72 cases — a pure move, no fork). **Detector**:
  reuses gateway ID + checks same-term co-offering of the catalog corequisite in the first-year window; inert by
  default (coreqs excluded from the default fetch → remedy names `--elumen-live`), active under `--elumen-live`
  or an injected `analyze_live(elumen_coreq=...)` map. **Honesty**: a co-OFFERING STRUCTURE proxy — NOT a
  measured/causal outcome; carries the ledger's "direct placement dominant; corequisite one supported form"
  caveat; `not_assessed` discloses placement/prereq blocking, registration-linkage-vs-catalog-co-offering, seat
  availability + same-term time-conflict, and no student outcome. **3-lens adversarial review** (determinism /
  honesty / correctness): DETERMINISM PASS, HONESTY PASS, and the correctness lens caught **1 must-fix** + 1
  should-fix + 1 nit — ALL FIXED via TDD: (must-fix) the eLumen↔schedule join was keyed by `mapping._norm`
  (preserves leading zeros) while coreq ids are `normalize_course_code` (strips zeros) → real zero-padded LACCD
  live codes ("MATH 0238" vs "MATH 238") silently missed → false inert / false "not offered"; now joins through
  `normalize_course_code` on BOTH sides (the same identity `_program_subjects` already uses), with a padded-data
  regression test; (should-fix) obstruction wording blamed the corequisite when the GATEWAY had no first-year
  section → now a single gateway-level obstruction; (should-fix) latent int/str term-type mismatch → terms
  coerced to `str` at the intersection boundary; (nit) `both_gateways_supported_year1` → renamed
  `both_gateways_coreq_co_offered_year1` (avoids the "supported"/causal lean). Residual accepted gap (system-wide,
  shared with the prereq join): subject-spelling variance (ENGL vs ENGLISH) is surfaced as "no corequisite for
  this gateway", never silently wrong. Isolated to `corequisite_availability.py` + `gateway_common.py` +
  `sources/elumen_client.py` + `build_live_workbook.py` (registry/threading) + 6 test files. Follow-ups (deferred):
  render F9 in report_export/chat/ui; fold seat + same-term time-conflict feasibility into the assessment
  (depends on the F8 follow-up); E13 equity-disaggregated success gap is the next data-source item.

**E14 — Minimal-perturbation recommender.** The inverse of E11: find the minimum-cardinality set of
section adds/moves that flips a not-buildable program to buildable, via lexicographic optimization
(minimize #changes, then a tie-break cost). Minimal-perturbation timetabling is an established IP
framing — the closest feasible schedule to the current one, minimizing changes
([arXiv:2008.12342](https://arxiv.org/abs/2008.12342)) — and CP-SAT supports lexicographic rounds by
fixing the prior objective as a constraint
([cpsat-primer](https://d-krupke.github.io/cpsat-primer/06_coding_patterns.html)). Deterministic
re-solve outside `engine.run`; recommends **offerings**, never student outcomes.

**E15 — F10 Title 5 §55002.5 contact-hour conformance.** A live fetch confirms the schedule exposes
per-meeting `times`, `woi`, and the LEC/LAB token, so total scheduled contact time is directly
**observable**. Title 5 §55002.5 gives a citable expected band (≈18 lecture / ≈54 lab contact hours
per unit, semester) ([§55002.5](https://www.law.cornell.edu/regulations/california/5-CCR-55002.5);
[standardized attendance accounting](https://www.cccco.edu/-/media/CCCCO-Website/docs/regulatory-action/final-reg-text-for-standardized-attendance-accountingf-a11y.pdf)).
Going observed→implied-hours→consistency-check is the **honest reverse** of the ambiguous
units→duration map (which is why `grid_pressure` marks this inert), flagging implausibly low/high
scheduled time. Activity (2:1) vs Lab (3:1) inside the LAB token stays a **wide, low-confidence
band**; TBA/arranged-hours sections excluded and surfaced. **Depends on E1** to avoid first-meeting
undercount.

**E16 — SBOM + license gate.** Generate a CycloneDX SBOM from `requirements.lock` at release and
fail if any GPL/AGPL component enters the bundle, operationalizing doctrine 5 (honored today only by
reviewer memory). `cyclonedx-python` generates SBOMs directly from a requirements file
([repo](https://github.com/CycloneDX/cyclonedx-python)) and `pip-audit` can emit CycloneDX JSON so
the audit gate and SBOM share one tool. Carry the bundled Temurin JRE and any system dylibs as
**explicitly-noted external components** (no silent drops, even in the SBOM). _(Per fact-check: the
**Cyber Resilience Act "obligation"** rationale is dropped — CRA is EU-market-scoped,
on-request-to-authorities, and phases in Dec 2027; an SBOM is good practice here, not a legal
obligation for a US-only LACCD tool — [CRA](https://digital-strategy.ec.europa.eu/en/policies/cyber-resilience-act).)_

_(E17–E33 follow the same posture — all outside `engine.run`, all proxy/aggregate-labeled. E17/E18
use Ollama's `format` JSON-schema constraint ([blog](https://ollama.com/blog/structured-outputs)) and
OWASP LLM01 content-segregation ([OWASP](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)).
E20 uses the keyless [Urban Institute IPEDS API](https://educationdata.urban.org/documentation/).
E24 anchors on [Vision 2030 §III](https://vision2030.cccco.edu/section-iii/). E25 uses the keyless
[ASSIST Agreements API](https://prod.assistng.org/apidocs/docs/articulation/listAgreements). E26 uses
the [CCCCO REST API](https://api.cccco.edu/). E29 uses PyInstaller's
[`PYTHONHASHSEED` reproducibility](https://pyinstaller.org/en/stable/advanced-topics.html). E30 uses
[pywebview's session-token/JSON-serialize guidance](https://pywebview.flowrl.com/guide/security.html).
E32 reuses the bundled OpenDataLoader PDF pipeline on the
[PDF-only LACCD calendar](https://www.laccd.edu/sites/laccd.edu/files/2025-10/2026%20Spring%20Calendar.pdf).)_

---

## 4. Cross-links: enhancements that also fix a review finding

| Enhancement | Fixes / subsumes review finding |
|-------------|---------------------------------|
| **E1** Capture all meeting blocks | **M1** (the silent `meetings[0]` drop) — the enhancement *is* the fix |
| **E10** Room conflicts → HTML report + chat | **H1** (the silent-drop on two surfaces) — same change |
| **E6** Schema-drift contract guard | Root cause of **M4** + **M5** (unguarded program-groups / element-level drift) |
| **E7** Shared retry/backoff + per-source 403 hint | **L1** (wrong-API 403 remedy); per-term fail-open mitigates the brittle multi-term fetch behind **M5** |
| **E2** Deterministic-time budget | Closes the wall-clock byte-identity hole the determinism doctrine implicitly rests on |
| **E3** Pin LLM parse (temp 0 + seed) | Closes the in-`engine.run` LLM nondeterminism hole (same doctrine 1 surface) |
| **E15** F10 contact-hour conformance | Consumes E1's recovered blocks; flips `grid_pressure`'s inert end-time/duration check to active, honestly bounded |
| **E22 / E13** Evidence + equity wiring | Closes the documented F7 gap (no F6 hook) flagged in the review's honesty theme |

Recommended sequencing: ship **E1 + E10** together (both pure no-silent-drop fixes), then **E2 + E3**
(determinism), then **E4 + E5 + E6 + E7** (CI/resilience), before the new detectors
(**E8/E9/E11/E12**). E15 must follow E1.

---

## 5. Honesty ledger — what I downgraded, corrected, or rejected

**Source claims downgraded / corrected by fact-check (idea kept, scope tightened):**

- **Gateway/guided-pathways "improvement" (E8).** The claim that GP-leading colleges "improved on
  early momentum metrics" is **partly** supported only: the cited EAB page is vendor marketing, and
  CCRC's 2024 analysis found **no** gain in college-level course **success/pass** rates and **no**
  persistence rise. E8 cites only the supported *gateway-as-leading-indicator* and
  *credit-momentum-association* claims, framed as **correlational** (real selection effects), never as
  a proven completion gain.
- **Corequisite "must increase offerings/caps" (E12).** The originally-cited PMC article **does not**
  make this claim (misattribution); replaced with CCRC's Tennessee cost-effectiveness work. In
  California, **direct placement** was the dominant lever with corequisite as a *support* — E12
  measures co-offering structure and does not imply corequisite alone drove statewide gains.
- **Cal-PASS / small-cell suppression threshold (E13, E-X).** The "size 6 or greater" threshold is
  **wrong and inverted**: the published rule suppresses counts **below 10**
  ([suppression page](https://www.calpassplus.org/Launchboard/Suppression)). The originally cited MOU
  print URL **could not be verified** — do not cite it; use the suppression page. E13's equity-gap
  reader must apply the correct (<10) suppression and stay labeled a **MEASURED course-success gap**,
  not a completion gap and not student-level.
- **CRA SBOM "obligation" (E16).** Overstated — EU-market-scoped, authorities-on-request, Dec 2027
  phase-in; the cited PyInstaller issue is a community thread. SBOM is recommended practice for the
  signed binary, not a current legal obligation for a US-only tool.
- **Ollama "temp 0 alone is not deterministic" rationale (E3).** Setting both `seed` and
  `temperature:0` is the **documented** reproducibility pattern, but the **causal rationale** is not
  stated in the docs and `seed` is the primary lever. Kept (set both) without asserting the unverified
  rationale.
- **Retry-After citation (E7).** The originally cited Better Stack guide does **not** mention
  `Retry-After`; swapped to Google Cloud's retry-strategy doc + MDN for the header semantics.
- **`cyclonedx-python` "most complete" (E16)** is the project's own marketing, not an independent
  benchmark — stated as positioning only.

**Doctrine-conflict items — flagged, not silently adopted:**

- **G1 Lexicographic objective.** Each round is deterministic, but it **changes the canonical
  `engine.run` plan**. Allowed **only** as a *disclosed* golden regeneration with a re-pinned
  `test_determinism_e2e.py` — never a silent output change. Presented as optional, not recommended for
  near-term.
- **G2 `interleave_search` parallelism.** The proto says it is deterministic regardless of worker
  count, contradicting the stricter `engine.py:375` single-worker comment — but the feature is flagged
  Experimental upstream. **Adopt only if it reproduces the committed golden plan-hash bit-for-bit;
  reject on any divergence.**
- **G3 Model-availability ladder — tag names unverified.** The proposal asserts "`gemma4:e2b` is a
  real Ollama tag," but **no fact-check in the provided set verifies it**, and as of this writing
  Gemma 4 is not a known release — the real edge variants are almost certainly **`gemma3n:e2b` /
  `gemma3n:e4b`** (effective-parameter Gemma 3n). The *idea* (probe `/api/tags`, surface the active
  model, floor on a smaller tag, fix the reachable-but-not-installed mislabel) is sound and worth
  doing; **verify the actual tag strings against `ollama.com/library` before coding.**
- **E13 equity subgroup figures.** The idea names "Kilgore Black 9→25%, BCTC URM 16.2→27%." Doctrine 3's
  explicitly-vetted equity figures are the **headline** "Kilgore 26→33%" and "Bluegrass BCTC
  23.7→34.8%." The disaggregated subgroup numbers are **not** in the vetted list I was given —
  re-confirm them against the primary source before wiring into `evidence.py`, and never let them fire
  on mere presence (severity-grade the trigger).
- **#17 hard ceiling stands.** Every data-source idea (E9, E13, E20, E-X) is an
  **aggregate/measured course/cohort/institution** signal — **none** is a student-level completion
  label, none may be folded into `_score`, and Cal-PASS Plus (E-X) is MOU-gated and only ever an honest
  **partial** path toward #17, surfaced as context.

**Things I did not independently re-verify (transparency):** As lead synthesizer I relied on the
provided adversarial verification (vote counts shown per finding; **2/3** items — M3, M5, M6, M7 —
carry slightly lower reviewer consensus) and on the provided source fact-checks; I did not re-execute
the test suite or re-fetch every live endpoint. The file:line references are reproduced from the
verified findings. The **F-number collision** in the raw research (two ideas both labeled "F8") is
resolved here as **F8 = Gateway Momentum, F9 = AB1705 corequisite, F10 = Title 5 contact-hour
conformance**.

**Debunked evidence deliberately excluded** (doctrine 3): no "CCD block scheduling +40%", no "Austin
Peay +23%", no "UCF −45% conflicts", no "Civitas 5–7%" appears anywhere in this report or in any
proposed grounding.
