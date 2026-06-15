# SchedulePlanner — Completion-Feature Roadmap

*Synthesis of two research efforts into a buildable feature plan. Prepared 2026-06-04.*

> **What this combines.** (1) The **Tongyi-DeepResearch** reports — three web-grounded studies on
> which scheduling reforms measurably improve community-college completion (the *evidence* lens;
> rendered reports + claim→source maps at
> `~/code/DeepResearch/inference/output/tongyi-dr_sglang/reports/`). (2) The **"All Courses Data"
> analysis** — the study of 14 real LAMC files (timeblocks, the 2-Year Completion scaffold,
> enrollment/facility/course masters) that mapped what SchedulePlanner can actually compute (the
> *data-readiness* lens; full report `~/Desktop/all-courses-schedule-analysis-2026-06-03.md`).

## The convergent insight

SchedulePlanner today is mostly a *single-student term planner* plus a set of *whole-schedule detectors*.
But the strongest completion evidence points at **program- and institution-level** levers —
guided-pathway course-map fidelity, bottleneck relief, standardized time-block conformance,
demand-driven scheduling, and equity segmentation. This roadmap pivots SchedulePlanner toward an
**institutional schedule-quality / completion-feasibility analyzer** — which is exactly the
*"structural-feasibility + seat-supply score"* the data report named (§4.3) as the **only honest
completion target** (no student-level outcome exists in any LACCD source, so every signal here is a
*proxy*, never a measured completion rate).

## Evidence grading (from the claim→source map — keep this honesty intact)

- ✅ **Well-grounded** (cite freely): Guided Pathways — Central Arizona 30%→43%, Philadelphia
  9%→~18%, Bluegrass (BCTC) 23.7%→34.8%; 8-week / standardized blocks — Odessa +12pp retention /
  +13% credentials, Kilgore 26%→33%; *57% of students report required courses unavailable when
  needed*; *course shutout → +2.3–2.8pp stop-out (22–28% relative)*; *conflict-aware scheduling
  tools → +5.41pp persistence (+7.39pp for new students)*.
- ⚠️ **Misattributed** (do not cite as-is): "Austin Peay Degree Maps → +23% on-time grad" actually
  traces to Complete College America's generic "Smart Schedules."
- ❌ **Unsupported** (do not cite): "Community College of Denver block scheduling → +40% completion"
  — no matching source.
- ❓ **Stated, unverified**: "UCF 45% fewer conflicts," "Civitas 5–7% persistence" — directional
  only; need a primary source before use in product copy.

## Research-lever → feature → status map

| Research lever (source) | SchedulePlanner feature | Status |
|---|---|---|
| Guided-pathway course maps ✅; "57% can't get required courses" ✅ + shutout ✅; "enroll in all required courses in one term" | **F1 Program-Map Buildability Audit** | ✅ **SHIPPED** (branch `feat/program-buildability-audit`) |
| Bottleneck / single-section courses ✅ | **F2 Cross-Program Bottleneck Leaderboard** | ✅ **SHIPPED** (branch `feat/cross-program-bottleneck`) |
| Standardized meeting-time blocks ✅; UCF 45% fewer conflicts ❓ | **F3 Grid-Conformance + Morning-Compression Pressure** | ✅ **SHIPPED** (branch `feat/grid-conformance-morning`) |
| Guided-pathway maps **include GE** ✅ | **F4 GE in the Program Denominator (ASSIST id 47)** | ✅ SHIPPED (branch feat/ge-program-denominator) |
| Demand-driven / predictive scheduling (⚠️/❓) | **F5 Demand-vs-Supply Action List** | ✅ SHIPPED (branch `feat/demand-supply-action-list`) |
| Equity gains for working/parent/URM ✅ | **F6 Equity / Archetype Exposure View** | planned |
| Honesty doctrine + the claim→source map | **F7 Evidence-Cited Reporting & Chat Grounding** | planned |

**Already shipped before this roadmap** (do not re-propose — the data report's original P0/P1):
real IR enrollment adapter (`sources/enrollment_ir.py`), room-conflict + room-capacity detector,
and the schedule→workbook importer (`sources/schedule_import.py`).

---

## F1 — Program-Map Buildability Audit  ⭐ #1  ✅ shipped

**What:** For each program, an honest scorecard — *is the published required path schedulable against
this (live or imported) schedule?* — covering availability, time-conflict feasibility (timed-only),
single-section bottlenecks, choice-group slack, recommended-season match, seat pressure, and dead
(de-catalogued) requirements. Deterministic; advisory; attaches to
`results["analysis"]["buildability"]` **outside `engine.run`**.

- **Why #1 (blend):** strongest evidence (guided-pathway maps are the best-grounded gains; it *is*
  Q3's question and addresses Q2's 57%/shutout); the data report lists every sub-check as "computable
  NOW" and names this exact score as the honest target; it's the spine the other features extend; and
  it's fully deterministic — a *better* architectural fit than a non-deterministic simulation.
- **Implementation:** new pure module `buildability.py` (no network, no solver, no pandas), reusing
  `mapping.reconcile_courses`, `timeblocks.feasible_selection`/`pairwise_hard_conflict`, the engine
  season logic, and `course_master`'s active set. Surfaced in the exported report, the live UI panel,
  and the chat assistant — each carrying the label *"structural-feasibility PROXY, not a measured
  completion rate."* Verified end-to-end on the Biology fixture (score 71/100; flags PHYSICS 007 not
  offered, an unsatisfiable MATH 247/261 choice, two Spring-mapped/Fall-only courses, and seat
  pressure on CHEM 101 / BIOLOGY 006).

## F2 — Cross-Program Bottleneck Leaderboard  ✅ shipped

Institution-wide ranking of the most dangerous required courses, scored by `#programs-depending ×
#sections × seat-fill × lab-room scarcity` — "fix this one course, help N programs." Evidence: Q2/Q3
name single-section/bottleneck courses as a forced-choice driver. Data: CH DEV 001 required by 16
programs, MATH 227 by 15, CS 101 by 13; ~1.06 sections/course/term; ~47 specialized lab rooms are the
binding constraint.

- **Implementation (shipped on `feat/cross-program-bottleneck`):** the cross-program demand
  (programs-per-course) lives only in the **Program Course Lists** export, so F2 is an **offline /
  import-path** feature — inert on a bare live fetch (one program per run), active wherever a demand
  map is supplied. New tolerant reader **`sources/program_lists.py`** → a `ProgramDemand`
  (`required`/`listed`/`titles`); new pure module **`cross_program_bottleneck.py`** (no
  network/solver/pandas; reuses `buildability.offered_by_course` for section dedup +
  `facility.is_lab`). `risk_score = round(n_programs / max(1, min_sections_per_term) · lab_mult ·
  fill_mult, 1)` (lab/fill amplifiers 1.3). `bottleneck_report` returns an honest active/inert
  envelope `{leaderboard, gaps, unmatched_program_courses, truncated}`. Wired into `analyze_live`
  (`program_demand`) / `analyze_import` (`program_lists_path`) **outside `engine.run`**; surfaced in
  the exported report, the live UI panel (+ an Option-1 program-lists picker), and the chat assistant
  — each carrying the *"structural supply-vs-demand PROXY, not a measured completion rate"* label.
  **Honesty:** unmatched program-list courses are reported, never silently dropped; no silent
  truncation on any surface. Validated on real data (Fall 2025 schedule × the Oct-2024 Program Course
  Lists: CH DEV 011 ranks as a 13-program bottleneck; the `unmatched` count honestly surfaces a
  source-side `ENG`/`ENGL`/`ENGLISH` subject-encoding inconsistency). 5-lens adversarial review +
  per-finding verification; 554 tests pass; determinism gate green. *(NB: the module is named
  `cross_program_bottleneck`, not `bottleneck`, because the latter shadows the `bottleneck` accel
  library pandas probes as an optional dependency.)* The named-but-deferred fast-follows: a subject
  crosswalk to shrink `unmatched`, and a live-path fan-out for cross-program demand.

## F3 — Grid-Conformance + Morning-Compression Pressure  ✅ shipped

**What:** A time-block schedule-quality scorecard — *how standardized are this schedule's meeting
times, and where does morning compression force required-course collisions?* Deterministic; advisory;
attaches to `results["analysis"]["grid_pressure"]` **outside `engine.run`** (the determinism gate
stays green). It computes three structural signals: (1) an **on-grid START-time conformance rate**
over deduped timed sections, reusing the already-fail-open `timeblocks.on_grid`; (2) a **9 AM–1 PM
"morning-compression" time-of-day distribution** (early/prime/afternoon/evening buckets) plus a
`morning_locked` count of required courses whose every timed section starts in that window; and
(3) a structural **mutual-exclusivity what-if** — two required courses are mutually exclusive when
*every* section of each is morning-locked, so a non-morning section *would* break the conflict (room
/ instructor feasibility unverified). Every surface carries the label *"structural time-block PROXY,
not a measured completion rate."*

- **Why (blend):** standardized blocks are a ✅ well-grounded forced-choice reducer; the mutual-
  exclusivity fact is backed by the course-conflict-graph literature (arXiv 2102.06743). The morning
  window is **this institution's** measured Fall-2024 concentration (morning 1,445 vs evening 285) —
  descriptive and correlational, NOT causal; registrar "prime time" is more precisely a ~9:30 AM–2:30
  PM midday band. We deliberately do **not** cite the "UCF −45% conflicts" headline: research found it
  conflates a Miami prime-time cap with an Ad Astra section-underutilization stat (it stays ❓ in the
  evidence table).
- **Implementation (shipped on `feat/grid-conformance-morning`):** new pure module
  **`grid_pressure.py`** (stdlib only — no network, solver, or pandas; mirrors F1 `buildability.py` /
  F2 `cross_program_bottleneck.py`), reading the raw section days/times the engine workbook drops and
  reusing `timeblocks.on_grid` + `buildability.required_set`. Detector entry **`grid_pressure`**; wired
  into the live build path **outside `engine.run`**; surfaced in `report_export`, the `ui.html` live
  panel, and `chat_assist` — each carrying the PROXY label.
- **What ships INERT (deliberately not built), with honest reasons** — surfaced in a `not_assessed`
  block so the gaps are visible, not silent:
  - **End-time / duration conformance** — NOT built. Contact hours are not recoverable from `units`
    without the activity **category** (Title 5 §55002.5: Lecture 1:2, Activity 2:1, Lab 3:0
    contact:outside ratios → a 3-unit *lab* carries ~9 weekly contact hours vs ~3 for a *lecture*).
    The live LACCD API exposes only a binary LEC/LAB token (stripped on the import path) and no
    contact-hours field, so a `units×60÷meetings` duration check would systematically false-flag
    labs, clinicals, activities, and compressed-calendar sections.
  - **Holiday / session-date awareness** — NOT built. No machine-readable academic/holiday calendar
    is ingested (LACCD publishes only PDF/HTML); the live API's per-section `dates` field is dropped
    at parse time and `woi` is unused.
- **Open data dependencies (would let the inert pieces activate honestly later):** per-section contact
  category (the IR export's **`Component`** column — activate by extending
  `sources/schedule_import.py` / `sources/enrollment_ir.py` to preserve it); meetings-per-week; the
  dropped session `dates` / `woi`; a machine-readable academic-holiday calendar; standardized block
  durations; and validated external completion/conflict evidence to replace the unverified UCF claim.
- **Tests:** a new **`tests/test_grid_pressure.py`** (11 tests) plus wiring/surface assertions; the
  determinism gate stays green (F3 lives outside `engine.run`). Full suite green (571 passed, 5
  pre-existing platform-coupled fixtures deselected).

## F4 — GE in the Program Denominator (ASSIST id 47) — ✅ SHIPPED

Folds GE-area requirements into the buildability denominator so a "complete" program map isn't silently
major-only. Evidence: guided-pathway maps include GE; a GE-less map overstates buildability.

**What shipped** (branch `feat/ge-program-denominator`, stacked on the F3 branch; 8 commits): the
buildability score is now **GE-inclusive** — `buildability.ge_denominator(ge_coverage)` folds schedulable
GE areas into `_score`'s denominator (the user-chosen *rescale* model), and each scorecard carries
`score`, **`score_major_only`**, and a signed **`score_delta`** so the overstatement a GE-less map
hides is explicit and auditable on every program. Wired into `analyze_live` via one injection line
(`ge_coverage=ge_coverage`, OUTSIDE `engine.run` → determinism gate green) and surfaced in
`report_export` / `ui.html` / `chat_assist` + the `program_buildability` detector reason, all carrying
the new `GE_LABEL` ("GE-inclusive buildability — a structural-coverage PROXY, not a measured completion
rate").

**Honesty (the load-bearing design choice):** the denominator unit is **per-area, pre-sweep**. The
naïve "wire the old `ge_summary` into `_score`" was rejected as dishonest — `ge.resolve`'s `resolution`
is an *auto-schedule* decision (a >3-offered area with no recommended course stays `reserve`) and its
`offered_count` is *post* the disjoint claiming sweep (a shared area shows 0 when a sibling grabbed its
course), so both **false-flag schedulable areas**. F4 instead added a pre-sweep `offered_eligible` count
to `ge.resolve` coverage and counts an area SCHEDULABLE iff `offered_eligible >= required`. **Fail open:**
shared (`required<=0`), reserve-only-remainder (`eligible_count is None`), and no-articulation
(`no_assist_data`) areas are EXCLUDED from the denominator — never a gap. **Inert** when no GE goal is
selected (major-only, honest reason) or when articulation is unavailable (ASSIST outage / empty injected
map / all `no_assist_data` → GE never moves the score). Unreviewed GE patterns (all shipped patterns have
`reviewed_by` blank by design) still count but ride a **DRAFT** caveat on every surface. The signed delta
is genuinely signed: a fully-schedulable GE set **raises** the score when the major path has gaps
(unit-pinned), so no surface asserts a fixed direction.

Scope: scores the **single** transfer/local goal already selected for the run; area-satisfiable
(per-area, optimistic — does not assert all GE areas are jointly fillable with distinct courses). Active
on the live/injected transfer paths and the local-catalog path. **Out of scope / follow-up:** an offline
ASSIST-area route through `analyze_import` (F4 stays honestly inert on a bare import, same doctrine as
F2's "no demand map → inert"). 584 tests pass (+~13 new); subagent-driven build with per-task spec +
code-quality review + a positive-delta regression guard. Fit note: `ge.resolve` +
`assist.fetch_ge_courses` already ran in `analyze_live`; F1's `ge_summary` GE-seam was the placeholder
F4 replaced.

## F5 — Demand-vs-Supply Action List  ✅ shipped

**What:** An asymmetric seat-supply scorecard — a ranked **"add a section"** list for over-subscribed
required courses, plus a neutral **capacity-slack observation** (under-filled courses worth a review,
never a cut order). Deterministic; advisory; attaches to `results["analysis"]["demand_supply"]`
**outside `engine.run`** (determinism gate untouched).

- **Activation gate:** seat counts (`cap_total > 0`) must be present. They reach the module two honest,
  already-plumbed ways: (1) on the **live path**, an IR PeopleSoft enrollment export joined via
  `enrollment.enrich_sections` — the live class-schedule API carries no Cap/Tot/Wait, so a bare live
  fetch stays **inert** (with an honest reason); (2) on the **import path**, a schedule export that
  carries Cap/Tot/Wait natively activates F5 from the export itself.
- **Headline metric:** `demand_ratio = (Tot + Wait) / Cap`. Over-subscription gate is **defensive
  and asymmetric**: a course joins the add list on `fill ≥ 0.95` OR (`Wait > 15` AND (`fill ≥ 0.90`
  OR a Closed section)) — waitlist never qualifies alone.
- **Cross-program weight (optional):** F2's `ProgramDemand` amplifies ranking via
  `action_score = demand_ratio × (1 + 0.1 × n_programs)` — a weight, not a gate; F5 runs without it.
- **Capacity-slack observation:** courses with `fill ≤ 0.40` across ≥ 2 sections surface as a
  review note carrying an explicit disclaimer ("not a cut recommendation — this proxy cannot see
  evening / online / cohort intent").
- **Implementation (shipped on `feat/demand-supply-action-list`):** new pure module
  **`demand_supply.py`** (stdlib only — no network, solver, or pandas; mirrors F1–F3 architecture).
  Wired into `analyze_live` and `analyze_import` outside `engine.run`; surfaced in `report_export`,
  the `ui.html` live panel, and `chat_assist` — each carrying the `DEMAND_SUPPLY_LABEL` ("structural
  supply-vs-demand PROXY, not a measured completion rate or causal claim"). Honest footnotes for
  truncation and `not_assessed` courses on every surface; never a silent empty result.
- **Deferred follow-up:** `analyze_import(enrollment_path=…)` was dropped because
  `enrollment.enrich_sections` strips native counts from unmatched records on merge; the
  merge-not-strip enrichment is the documented out-of-scope follow-up.
- **Tests:** new `tests/test_demand_supply.py` (10 tests) plus wiring/surface assertions in
  `test_live_offline_pipeline.py`, `test_report_export.py`, and `test_chat_assist.py`. Full suite
  green (603 passed, 5 pre-existing platform-coupled fixtures deselected).

## F6 — Equity / Archetype Exposure View

Re-run F1 buildability under constrained availability windows (evening-only, online-only,
two-days-a-week) → which programs collapse for working/parent students. Evidence: every report's
largest gains were equity (working/parent/URM). Data: morning compression + online-first + thin
evening = the working-student squeeze. Fit: a thin wrapper over F1 (filter sections to the archetype
window, re-score).

## F7 — Evidence-Cited Reporting & Chat Grounding

A curated, source-mapped evidence appendix in the exported report + chat grounding that explains *why*
a flagged conflict matters, citing **only the ✅ vetted claims** (Odessa/Kilgore/BCTC; 57% / shutout
/ +5.41pp persistence). Makes SchedulePlanner persuasive to administrators while staying honest. Fit: a
`report_export` section + a `chat_assist._context` injection; reuses the claim→source-map methodology.

## Fast-follow precision items (cheap; sharpen F1/F2)

- **Dead-requirement detector** — required course ∉ active set from `course_master`. *(Done as part of
  F1; active on the import path.)*
- **Workbook Notes as gold by-design exclusions** — ingest the 11 hand-verified Notes (e.g. "ART 202
  not offered in fall by design") so intentional gaps aren't flagged. *(F1 supports a `by_design` set;
  the ingestion of the Notes column is the remaining work.)*

---

*Hard ceiling (both sources agree): there is no completion **label** in any LACCD file. Every signal
here is a feasibility/supply **proxy**. A real outcome model needs an external student-record/IR
export (student id, units-earned, term-to-term, awards) — supplied by none of the available data.*
