# Product Requirements Document
## 2-Year Completion Schedule Planner — LAMC

**Status:** Draft v1 (milestone table reconciled to shipped reality, §12)
**Owner:** [To be filled]
**Last updated:** Spring 2026

---

## 1. Problem

Los Angeles Mission College (LAMC) students enrolled in 2-year associate degree
programs frequently fail to complete in 2 years. Root causes are obscured by
many overlapping factors: course rotation (Fall-only vs both), modality
availability (in-person vs online async), prerequisite chains, single-section
capacity limits, and student work/life constraints. The result is an opaque
problem that Academic Affairs cannot easily diagnose or fix.

The college lacks tools to:

- Identify which specific courses block 2-year completion
- Model whether a proposed schedule will actually let cohorts finish on time
- Recommend the minimum schedule change needed to unblock students
- Distinguish *scheduling* problems (a fix would help) from *structural load*
  problems (the cohort is part-time and 2 years was never realistic)

## 2. Goals

1. Produce evidence-based bottleneck reports surfacing the specific courses,
   modalities, and rotation patterns that block 2-year completion.
2. Generate provably feasible term-by-term plans for each program, per cohort
   type (full-time, part-time).
3. Recommend the minimum schedule changes that unblock blocked programs, with
   single-section precision (e.g., "add one ENGR 103 section in Spring").
4. Run as a standalone desktop tool — no IT deployment, no server, no data
   sharing agreement.
5. Operate entirely on aggregate / section-level data. No PII, no FERPA
   exposure.

## 3. Non-Goals (v1)

- Personalized advising for individual students
- Live integration with PeopleSoft or Banner
- Faculty assignment or room scheduling
- Replacing existing institutional tools (Ad Astra, EAB Navigate, Stellic)
- Mobile app
- Web-hosted multi-user version

## 4. Target Users

| User | Role | Primary use |
|---|---|---|
| Dean of Academic Affairs | Primary | Approve schedule changes, justify budget asks |
| Department chair | Primary | Diagnose bottlenecks in own department |
| Scheduling committee | Primary | Plan term-by-term offerings |
| Counselor | Secondary | Reference realistic completion timelines when advising |
| Institutional Research | Secondary | Pull and verify data inputs |
| Student | Future (v2) | Self-service planning via existing chatbot integration |

## 5. User Stories

- *As a dean,* I want to see which of my college's programs cannot be completed
  in 2 years given current offerings, so I can prioritize where to intervene.
- *As a department chair,* I want to know which of my courses are bottlenecks
  for cross-departmental programs, so I can advocate for more sections or
  changed rotation.
- *As a scheduler,* I want to know the minimum number and type of new sections
  needed to unblock specific cohorts, so I can make the case for budget and
  faculty load changes.
- *As a counselor,* I want a realistic completion plan for both full-time and
  part-time students, so I can advise truthfully instead of citing the
  published map.
- *As an IR analyst,* I want to export the underlying data without exposing
  any PII, so the tool can be used without a data sharing review.

## 6. Functional Requirements

### 6.1 Inputs
- F1. Accept a single `.xlsx` workbook with three sheets: `sections`,
  `catalog`, `programs`
- F2. Accept a directory of three CSVs as an alternative
- F3. Validate the schema and surface clear errors for missing columns

### 6.2 Analysis
- F4. Detect rotation gaps (required courses not offered every term)
- F5. Detect single-section risk per term
- F6. Detect modality mismatch (required courses with chronic low fill)
- F7. Detect under-supply (chronic waitlisting)
- F8. Validate the official Program Mapper sequence and report violations

### 6.3 Scheduling
- F9. Generate the fastest feasible term-by-term plan for each program, per
  cohort, under current offerings
- F10. Support multiple cohort profiles (default: full-time, part-time)
- F11. When no feasible plan exists within a cohort's horizon, compute the
  minimum set of new course offerings that would unblock completion
- F12. Respect prerequisite logic (AND of OR-groups)
- F13. Respect unit caps per term
- F14. Respect season-specific course availability derived from the data

### 6.4 Output
- F15. Per-program cards showing both cohorts side by side, with terms-to-
  complete and the full term-by-term plan
- F16. Bottleneck diagnostic panel
- F17. Plain-language admin briefing
- F18. Show official-map violations alongside the corrected plan

### 6.5 AI assist (optional)
- F19. Detect presence of Gemma 4 E2B via Ollama
- F20. Offer one-click model download on first run
- F21. Use Gemma 4 to parse unstructured prerequisite text into logical form
- F22. Use Gemma 4 to write the admin briefing
- F23. Degrade gracefully when AI is absent: regex prereq parsing + templated
  briefing

## 7. Non-Functional Requirements

### 7.1 Privacy
- N1. No student-level data processed
- N2. No PII fields loaded; instructor identifiers stripped at ingestion
- N3. No network calls except optional localhost Ollama
- N4. No telemetry, analytics, or external logging

### 7.2 Performance
- N5. Full analysis completes in under 30 seconds for typical LAMC data
  volumes (~1,000 sections × 8 terms)
- N6. Per-program solve under 10 seconds
- N7. UI remains responsive during analysis

### 7.3 Portability
- N8. Single-binary distribution per platform (macOS, Windows, Linux)
- N9. No installation of databases or servers required
- N10. Offline operation after initial setup (no internet required to run
  analyses)

### 7.4 Reproducibility
- N11. Deterministic solver output given identical inputs
- N12. Bundled synthetic dataset for demos and validation

## 8. Success Metrics

| Metric | Target | Measure |
|---|---|---|
| Programs analyzed | ≥ 5 | Distinct programs with completed analyses |
| Published bottleneck reports | ≥ 1 | Reports delivered to Academic Affairs |
| Schedule changes traceable to tool | ≥ 1 | New sections added because tool surfaced bottleneck |
| Corrected 2-year maps produced | ≥ 3 | Programs where solver-produced sequence replaced official map |
| Departments using the tool | ≥ 2 | Distinct departments running their own analyses |

## 9. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Real prereq text too irregular for LLM parsing | Medium | Medium | Regex fallback always available; surface unparsed prereqs for manual review |
| OR-Tools native libraries break PyInstaller build | Medium | High | `--collect-all ortools` flag; documented in BUILD.md |
| Gemma 4 E2B too small for nuanced briefings | Low | Low | Fallback to templated summary; user can swap to E4B by changing one constant |
| Real data missing key columns (e.g., `IGETC`, `OER`) | Medium | Low | Engine treats missing columns as empty; no hard dependency |
| Adoption stalls at one department | Medium | Medium | Bundle synthetic demo so colleagues can evaluate without their own data |

## 10. Open Questions

- Will counselors actually use a desktop binary, or does a web-hosted version
  need to follow v1?
- Should the solver consider time-of-day conflicts within terms, not just
  season-level offering availability?
- What's the appetite for extending to a student-facing rollout via the
  existing LAMC chatbot?
- Will the published Program Mapper be updated to match solver-corrected
  sequences, or will the corrected plans live separately?

## 11. Out of Scope (Explicit)

- Any handling of student names, IDs, grades, transcripts, or rosters
- Section-level scheduling (room/faculty/time assignment)
- Tuition or financial aid calculations
- Transfer guarantee program (ADT) credit transfer logic beyond CCC→CSU
  articulation already in scope via Assist.org

## 12. Milestones

These are **product / adoption** milestones. They are tracked separately from
the **engineering** milestones in the git history (`m1`–`m8`, see the commit
log and `docs/M8_QA_REPORT.md`). The engineering milestones built and hardened
the *capability*; the product milestones below are reached when that capability
is *used on real data and acted on by the college* — several of those steps
depend on inputs or people outside the codebase, and stay open until then.

| Milestone | Deliverable | Status |
|---|---|---|
| M0 | Synthetic data MVP with planted bottlenecks | ✓ Complete — `generate_synthetic.py` + bundled `files/lamc_data.xlsx`; deterministic regeneration is regression-tested (eng. `m8`). |
| M1 | Solver with cohort profiles + minfix | ✓ Complete — deterministic OR-Tools CP-SAT engine, full-time/part-time cohorts, minimum-fix path (eng. `m1`). Per-FR backbone in `tests/test_engine_features.py`. |
| M2 | Desktop app shell with file upload + render | ✓ Complete — pywebview app (`app.py` + `ui.html`) with Choose-file, Load-demo, and Build-from-live-LACCD (eng. `m2`/`m4`); packaged macOS `.app` shipped & verified (eng. `m5`, see `BUILD.md`). |
| M3 | Gemma 4 integration with graceful fallback | ✓ Complete — optional Gemma via Ollama (default tag `gemma4:e2b`), tag-exact presence check, templated fallback when absent (eng. `m3`/`m6`, `llm_assist.py`). |
| M4 | First real data ingestion | ◐ Partial — the **live public-LACCD ingestion path shipped** (`build_live_workbook.py` + `sources/`, eng. `m3`/`m4`) and produces real workbooks from the schedule + Program Mapper APIs. **Still externally pending:** the **IR PeopleSoft enrollment export**. The live schedule API returns Cap/Tot/Wait = 0, so the `modality_mismatch` and `under_supply` detectors are **inert on live data**, and prerequisites are blank (eLumen not yet wired). A committed IR-shaped sample (`files/lamc_sample_enrollment.xlsx`) exercises those detectors offline; the real IR export is the gating input. |
| M5 | First published bottleneck report | ☐ Open — this is a **human deliverable**, not code. The engine produces the analysis; a report still has to be authored and delivered to Academic Affairs (success metric §8). |
| M6 | First schedule change traceable to tool | ☐ Open — depends on the college adding a section because the tool surfaced a bottleneck. Outside the codebase. |
| M7 | Cross-department adoption | ☐ Open — adoption metric (§8); the student-facing rollout in §4/§10 is explicitly **future (v2)**. |
