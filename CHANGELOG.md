# Changelog

All notable changes to SchedulePlanner are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] — 2026-06-23

Code-review remediation from a full-codebase CodeRabbit review, plus a
regenerated demo baseline. Bug fixes and internal hardening — no new features.
The bundled demo workbook was regenerated, so demo plans differ from 0.2.1.

### Fixed
- **Conflict detection (M1):** secondary / lab meeting blocks were dropped by
  several detectors (off-grid, room-capacity, lab-pool, room/time-block status)
  and by `buildability.offered_by_course`, blinding them to clashes on a
  section's non-primary block. They now read every meeting block.
- **Cross-term false conflicts:** two courses offered in *different* terms are no
  longer flagged as a hard / mutually-exclusive time conflict (new
  `timeblocks.pairwise_hard_conflict_termed`; applied in
  `buildability.time_conflict` and `grid_pressure.mutual_exclusions`).
- **Subject-alias joins:** aliased spellings (e.g. `ENGL` vs `ENGLISH`) are now
  matched at the cross-program-bottleneck and demand-vs-supply joins instead of
  being undercounted (`_offered_match` merge; canonical-space demand matching).
- **Engine:** `official_map_issues` checks recommended semesters against the
  data-derived planning cadence (Summer/Winter-aware), not the static Fall/Spring
  default; a malformed `ge_requirements` row now raises a contextual
  `InputDataError`; `fetch_sections` guards against `raise None`.
- **Offline contract:** `analyze_import` rejects a transfer GE goal that needs a
  network ASSIST fetch unless a pre-fetched map is supplied; the eLumen fetch no
  longer clobbers the schedule fetch's skipped-terms status.
- **LLM safety:** the admin-briefing summary is fenced as untrusted data
  (prompt-injection hardening); blank prerequisite tokens are filtered.
- **UI:** the analyze / demo / explain bridge calls always restore button state
  on failure (no stuck spinner); room double-booking / over-capacity sections
  render only when actually assessed, never a misleading "none".
- **Misc:** `chat` tolerates `None` results; inert evidence is framed as general
  context, not a flagged finding; online-archetype computability keys off
  modality *presence*; invalid term tokens are rejected, not silently dropped;
  the bundled JRE launcher resolves `java.exe` on Windows.

### Changed
- **Synthetic generator / demo data:** recommended-sequence-only courses
  (`PSYC 1` / `HIST 11` / `COMM 101`) are now part of the affected programs and
  scheduled; TBA/async rows no longer set a Sunday flag; each section gets one
  consistent room across its aliases; `Nbr Mtgs` = (meeting days) × 16. The demo
  `files/lamc_data.xlsx` and enrollment sample were regenerated and the
  determinism plan-hash golden re-pinned (all programs verified still feasible).
- **Refactor:** `_norm` / `_to_units` moved to a shared `sources/textnorm`
  module (re-exported by `mapping`, so all existing call sites are unchanged),
  resolving an offline-reader → workbook-assembly layering dependency.

## [0.2.1] — 2026-06-15

Ship-review hardening (PR #85). No behavior change on the happy path; closes the
actionable Security and Tests findings from the 4-pillar ship-readiness review.

### Security
- **program_mapper:** an unknown/typo'd campus now raises a named `SourceDataError`
  listing the valid campuses (via `_config_for`) instead of a raw `KeyError`, before
  any network call — mirroring the `assist` / `elumen` clients.
- **SBOM license gate:** the no-copyleft gate now **fails on any unverifiable
  (`UNKNOWN`) license** instead of silently passing it — a package whose license
  metadata is unreadable on the gate runner could otherwise have hidden a copyleft
  dependency. Adds a vetted-license fallback map (`VETTED_LICENSE_FALLBACKS`) for
  platform-conditional / metadata-less packages (clr-loader, colorama, pythonnet,
  qtpy, tzdata, cffi, pycparser).

### Tests
- ~40 new offline regression tests: SSRF input-sanitization clamp, a cross-version
  determinism plan-hash golden, full `analyze_live` raw-socket network isolation,
  render-registry order + cross-surface no-silent-drop, malformed-input hardening,
  ui.html output-escaping guard, an adversarial prompt-injection corpus, and
  supply-chain (JRE checksum) pinning. Offline suite: 1006 → 1046 passing.

## [0.2.0] — 2026-06-15

Enhancement release: the F8/F9 + E2–E19 detector / infrastructure / chat-hardening
stack (PRs #65–#84) plus 8 adversarial-review bug fixes (#57–#64). Tagged but not
published as a release artifact; superseded by 0.2.1.

### Added
- First-year gateway-momentum (F8) and AB1705 corequisite co-availability (F9)
  detectors; E11 infeasibility explainer (CP-SAT MUS); E9 course-success adapter;
  E13 equity course-success gap; E14 minimal-perturbation recommender; E15 Title 5
  §55002.5 contact-hour conformance.
- Supply chain: E4 pip-audit CVE gate, E16 CycloneDX SBOM + no-copyleft license
  gate, E5 SLSA build-provenance attestation.
- Chat hardening: E17 schema-constrained JSON routing, E18 indirect-prompt-injection
  content fence, E19 RAGAS-style groundedness guard.

### Fixed
- Full multi-block meeting footprint (H1+M1); cadence-derived "~N years" (M2);
  windowed GE schedulability (M3); per-group + element-level schema-drift guards
  (M4/M5); engine hot-path conflict-scan scoping (M6/M7); derived QA-gate live
  count (M8); source-agnostic 403 hint (L1). Determinism preserved (engine plans
  byte-identical to 0.1.x on the bundled data).

## [0.1.0] — 2026-06-03

First signed, notarized, and stapled macOS release (Developer ID; arm64 `.dmg`).
Deterministic LACCD schedule + completion-feasibility analyzer: pywebview desktop
app, OR-Tools CP-SAT engine, optional local Gemma via Ollama, bundled Temurin JRE
for the catalog PDF feature.

[0.2.1]: https://github.com/johnnyrobot/SchedulePlanner/releases/tag/v0.2.1
[0.2.0]: https://github.com/johnnyrobot/SchedulePlanner/releases/tag/v0.2.0
[0.1.0]: https://github.com/johnnyrobot/SchedulePlanner/releases/tag/v0.1.0
