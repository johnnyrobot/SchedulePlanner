# edgesched — Completion-Feature Roadmap

*Synthesis of two research efforts into a buildable feature plan. Prepared 2026-06-04.*

> **What this combines.** (1) The **Tongyi-DeepResearch** reports — three web-grounded studies on
> which scheduling reforms measurably improve community-college completion (the *evidence* lens;
> rendered reports + claim→source maps at
> `~/code/DeepResearch/inference/output/tongyi-dr_sglang/reports/`). (2) The **"All Courses Data"
> analysis** — the study of 14 real LAMC files (timeblocks, the 2-Year Completion scaffold,
> enrollment/facility/course masters) that mapped what edgesched can actually compute (the
> *data-readiness* lens; full report `~/Desktop/all-courses-schedule-analysis-2026-06-03.md`).

## The convergent insight

edgesched today is mostly a *single-student term planner* plus a set of *whole-schedule detectors*.
But the strongest completion evidence points at **program- and institution-level** levers —
guided-pathway course-map fidelity, bottleneck relief, standardized time-block conformance,
demand-driven scheduling, and equity segmentation. This roadmap pivots edgesched toward an
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

| Research lever (source) | edgesched feature | Status |
|---|---|---|
| Guided-pathway course maps ✅; "57% can't get required courses" ✅ + shutout ✅; "enroll in all required courses in one term" | **F1 Program-Map Buildability Audit** | ✅ **SHIPPED** (branch `feat/program-buildability-audit`) |
| Bottleneck / single-section courses ✅ | **F2 Cross-Program Bottleneck Leaderboard** | ✅ **SHIPPED** (branch `feat/cross-program-bottleneck`) |
| Standardized meeting-time blocks ✅; UCF 45% fewer conflicts ❓ | **F3 Grid-Conformance + Morning-Compression Pressure** | ✅ **SHIPPED** (branch `feat/grid-conformance-morning`) |
| Guided-pathway maps **include GE** ✅ | **F4 GE in the Program Denominator (ASSIST id 47)** | planned |
| Demand-driven / predictive scheduling (⚠️/❓) | **F5 Demand-vs-Supply Action List** | planned (IR adapter unblocks it) |
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

## F4 — GE in the Program Denominator (ASSIST id 47)

Fold GE-area requirements into the buildability denominator so a "complete" program map isn't silently
major-only. Evidence: guided-pathway maps include GE; a GE-less map overstates buildability. Data:
Program Course Lists is major-only and `program_mapper.py` drops most CHOICE/GENERAL_EDUCATION
elements; add GE from ASSIST (LAMC institutionId 47) / IGETC strings. Fit: `ge.resolve` +
`assist.fetch_ge_courses` already run in `analyze_live`; F1 already threads `ge_rows` into a GE-seam
field — F4 deepens it.

## F5 — Demand-vs-Supply Action List

A ranked scheduling action list: required courses that are over-subscribed (→ add a section) vs
under-filled (→ consolidate), weighted by cross-program demand. Evidence: demand-driven/predictive
scheduling (⚠️/❓ — directionally strong, headline numbers soft). Data: the shipped IR adapter flips
`fill`/`under_supply` from inert→active, so demand signals finally exist offline. **Honesty:** demand
PROXY, never completion causation; waitlist is weak (pair Wait>15 with fill≥0.9/Closed).

## F6 — Equity / Archetype Exposure View

Re-run F1 buildability under constrained availability windows (evening-only, online-only,
two-days-a-week) → which programs collapse for working/parent students. Evidence: every report's
largest gains were equity (working/parent/URM). Data: morning compression + online-first + thin
evening = the working-student squeeze. Fit: a thin wrapper over F1 (filter sections to the archetype
window, re-score).

## F7 — Evidence-Cited Reporting & Chat Grounding

A curated, source-mapped evidence appendix in the exported report + chat grounding that explains *why*
a flagged conflict matters, citing **only the ✅ vetted claims** (Odessa/Kilgore/BCTC; 57% / shutout
/ +5.41pp persistence). Makes edgesched persuasive to administrators while staying honest. Fit: a
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
