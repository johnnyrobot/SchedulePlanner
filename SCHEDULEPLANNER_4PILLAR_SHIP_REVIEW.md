# SchedulePlanner v0.2.0 — 4-Pillar Ship-Readiness Review

## Executive summary

**Verdict: SHIP.**

v0.2.0 is ship-ready. The adversarial 4-pillar review surfaced **0 blockers and 0 major findings** across Efficiency, Security, Regression, and Tests — only 17 minor and 3 nit items, none of which gate the release (4 additional serious claims were raised by skeptics and refuted/removed). The release lands the five load-bearing doctrines with evidence: determinism holds byte-identically across runs and input surfaces (10/10 e2e tests, work-based CP-SAT budget with `random_seed=42`/`num_search_workers=1`), the honesty/no-silent-drops doctrine is enforced by byte-pinned golden tests over ordered render registries (proxies labeled on all three surfaces), privacy is structurally guaranteed (`engine.run` proven network- and PII-free), and the supply chain is gated end-to-end (E4 pip-audit, E16 CycloneDX + no-copyleft license gate, E5 SLSA provenance) with a clean, hash-pinned lock and current non-EOL runtimes. The residual minors are hardening and test-debt items — most valuably an unvalidated-campus `KeyError` in `program_mapper.py` (degrades a user-facing error message, does not crash the app or breach a boundary) and the absence of an automated UI/JS XSS regression suite (the `escapeHtml()` mitigation is present and static-auditable). These should be tracked as fast-follows, not ship blockers.

### Counts by pillar

| Pillar | Blocker | Major | Minor | Nit | Total |
|---|---|---|---|---|---|
| Efficiency | 0 | 0 | 2 | 1 | 3 |
| Security | 0 | 0 | 5 | 0 | 5 |
| Regression risk | 0 | 0 | 0 | 0 | 0 |
| Tests | 0 | 0 | 10 | 2 | 12 |
| **Total** | **0** | **0** | **17** | **3** | **20** |

> Serious claims raised by adversarial skeptics and refuted (removed): **4**.

---

## 1) Efficiency

**Posture:** Strong algorithmic and build efficiency. The CP-SAT solver uses work-based (not wall-clock) deterministic budgets, O(N²) conflict detection is scoped to schedulable courses only, and the 365 MB bundle is aggressively de-bloated. The only findings are redundant section re-walks in the live-data detector pipeline — all minor/nit, none on the hot path.

| Severity | Title | Evidence | Recommendation |
|---|---|---|---|
| minor | Redundant section walks in time-block detector | `build_live_workbook.py:595-596` re-walks `sections` for `has_meeting` after `_time_block_collisions:1546` already parsed all meetings via `_section_meetings_by_course:524` | Pass a boolean flag (or the parsed `by_course` dict) from `_time_block_collisions` to `_time_block_detector_entry`, or infer `has_meeting` from the collisions result — eliminates one O(sections) walk |
| minor | Redundant section walks in room-conflict detector | `build_live_workbook.py:766-775` re-iterates `sections` for `has_room`, duplicating the room check already done in `_room_collisions:645-667` | Refactor `_room_collisions` to return `(findings, has_room)` and thread `has_room` into `_room_detector_entry` |
| nit | Repeated `sections_with_counts` filter across branches | `build_live_workbook.py:1331, 1340, 1347` each run `sum(1 for r in sections if 'Cap Enrl' in r)` in mutually-exclusive branches | Compute `sections_with_counts` once after all enrichment completes |

**Already solid:** `engine.run` loads/builds the model once; O(N²) hard-conflict detection is scoped via `relevant_items` (engine.py:519-525) to only program-schedulable courses; CP-SAT uses `max_deterministic_time=30.0` (engine.py:444) not wall-clock; network I/O runs outside `engine.run`; `test_performance.py` confirms N5 (~1,000 sections × 8 terms × 20+ programs under 30s).

---

## 2) Security

**Posture:** No exploitable security defect. Endpoints are all public/unauthenticated, no secrets are hardcoded, the chat/LLM surface is hardened (schema-constrained routing, prompt-injection fencing, groundedness guard, SSRF input clamp), and the supply chain is gated and provenance-attested. Five minor items remain — hardening and gap-closure, not live vulnerabilities — organized below by Aikido category.

### SCA — Dependencies & EOL runtimes
No findings. Hash-pinned lock (356 sha256 over 39 packages, `--require-hashes`), E4 pip-audit gating on every push/PR + weekly (`.pip-audit-ignore` empty), all runtimes current/non-EOL (Python 3.12/3.13, Temurin 17 LTS, ortools 9.15.6755).

### Secrets & credential exposure
No findings. All endpoints public/unauthed; signing/notary creds read only from env with fail-closed preflight (`sign_notarize_macos.sh:125-160`); no API keys in lock or code; HTTP transport sends no `Authorization` headers.

### Licenses — no-copyleft bundle gate (E16)

| Severity | Aikido | Title | Evidence | Recommendation |
|---|---|---|---|---|
| minor | LICENSES | Platform-conditional GPL could silently bypass the SBOM gate | `scripts/generate_sbom.py:173-176` (`UNKNOWN`→not copyleft); `.github/workflows/ci.yml:103-104` comment acknowledges the gap; 5 win32/openbsd6/emscripten pkgs resolve to `UNKNOWN` on macOS CI | Run the SBOM gate a second time on a Windows runner (or cross-validate against PyPI), OR allowlist known-permissive platform-conditional pkgs and reject unlisted `UNKNOWN` on the Windows build |
| nit | n/a | SBOM lists platform-conditional pkgs as if installed on macOS | `generate_sbom.py` reports 41 components on macOS though `clr-loader`/`colorama`/`pythonnet`/`qtpy`/`tzdata` raise `PackageNotFoundError` | Annotate platform-conditional entries with a `platform` marker (e.g. `SchedulePlanner:platform-conditional: win32`) so SBOM readers see they don't ship on macOS |

### SAST — Injection / SSRF / Deserialization / Subprocess

| Severity | Aikido | Title | Evidence | Recommendation |
|---|---|---|---|---|
| minor | SAST | Unvalidated campus code in `program_mapper` `COLLEGE_CONFIGS` access raises `KeyError` | `sources/program_mapper.py:49-56` — `_headers`/`_site_url` do `COLLEGE_CONFIGS[campus]` with no membership check; free-text UI field (`ui.html:271`, JS only trims at `:713`) flows unsanitized via `app.fetch_live → analyze_live → build → fetch_program`. Repro: `program_mapper.fetch_program('INVALID','Biology')` raises `KeyError: 'INVALID'`. Caught at `app.fetch_live:229-231` → user sees a raw `KeyError` string, not a named-options message | Wrap the dict accesses in `try/except KeyError` and raise `SourceDataError` listing valid campuses, matching `assist.py:institution_id_for():48-55` and `elumen_client.py:tenant_for():~85-92` |

*Note:* this is a degraded error message, not a boundary breach — campus enters the URL **path** (not host/scheme), `term` is an int, and httpx normalizes path-traversal; the other clients already validate campus with a named error. Not ship-blocking.

### IaC / CI-CD + LLM/AI attack surface
No findings. Actions pinned to major versions; least-privilege permissions (`contents:read` default; `id-token:write`/`attestations:write` only in the release job); chat hardening verified — E17 `ROUTER_SCHEMA` + `_validate_intent`, E18 `_segregate` fence with anti-breakout test, E19 `groundedness_review` caveat, `_clean_campus` SSRF clamp, and UI model output sinks to `.textContent` (ui.html:1471) with `escapeHtml()` on data-derived `innerHTML`.

### Malware / supply-chain & artifact provenance

| Severity | Aikido | Title | Evidence | Recommendation |
|---|---|---|---|---|
| minor | SUPPLY-CHAIN | PyInstaller build tool not hash-pinned before release | `release-build.yml:51-52` installs `pyinstaller==6.20.0` with no `--require-hashes` (vs line 50 which uses it for the lock); not a runtime dep | Add PyInstaller to a hash-pinned manifest, or verify the wheel hash + installed-version/location before the build step |
| minor | SUPPLY-CHAIN | No GPG signature verification for the Temurin release asset | `scripts/fetch_jre.sh:38,44` downloads the tarball via `curl -fSL` with sha256-only verification (lines 46-52); Adoptium's published GPG release signature is never checked | Add an optional `gpg --verify` of the Adoptium `.asc` before extraction (proves origin, not just integrity), or document in BUILD.md that checksum-only is deemed sufficient for this local-desktop path |

**Already solid:** SLSA Build L2 keyless provenance (E5, `actions/attest-build-provenance@v2`, verifiable via `gh attestation verify`); CycloneDX SBOM + no-copyleft gate passes on v0.2.0 (41 components: 40 PyPI permissive/LGPL + Temurin JRE gate-exempt + CPython); JRE sha256-pinned and idempotent; subprocess calls are list-form (no `shell=True`, no user input); JSON deserialization guarded (no pickle/yaml/eval); zip handling is namelist-only (no zip-slip); no `--force-push`/`--no-verify` in history.

---

## 3) Regression risk

**Posture:** No regressions. The v0.2.0 merge of 8 adversarial bug-fix PRs (#57–#64) preserved determinism, data-pipeline integrity, and the honesty doctrine; all 1005 QA tests pass and golden byte-currency holds. **Zero findings.**

**Already solid (three independently-reviewed regression surfaces, all clean):**

- **Determinism & engine-core:** all 10 determinism e2e tests pass; `max_deterministic_time=30.0` (work-based) replaces wall-clock; `random_seed=42`/`num_search_workers=1` source-guarded; `proven_optimal` computed from solver status (not hardcoded) and surfaced with caveat when `False`; `terms_per_year` derived from offered seasons so surfaces never re-hardcode "2 terms/year".
- **Merge-resolution & data pipeline:** multi-block `meetings` field emitted by both live (`schedule.py:104-122`) and offline (`schedule_import.py:215`) paths; element-level `_require_dict` guards (M5) name errors by JSON path; `_encode_meetings` round-trips canonically through xlsx; per-term fail-open (E7) skips one term's schema drift with honest `status['skipped']` rather than silent-drop or fatal-raise.
- **Render-surface & honesty doctrine:** `ANALYSIS_DETECTORS` pinned to exactly 8 ordered entries (test-guarded); `SECTION_RENDERERS` (18, evidence last) and `GROUNDERS` (20, evidence last) byte-pinned by golden tests; every proxy labeled across report/UI/chat; evidence framed as "sector-wide findings from OTHER institutions… NOT a measurement of this campus."

---

## 4) Tests needed before ship

**Posture:** Backend coverage is strong (1005/1011 passing; 6 live-marked deselected) with comprehensive determinism, privacy, schema-drift, and chat-routing tests. The gaps are concentrated in the **un-tested UI/JS layer**, **security-regression fuzz/corpus depth**, and **cross-machine determinism** — all minor/nit test-debt, none blocking. The most valuable adds are listed first.

| Severity | Title | Evidence | Recommendation |
|---|---|---|---|
| minor | No automated webview/DOM XSS regression suite | `tests/` has zero `html/ui/xss/dom` files; `ui.html:462-465` defines `escapeHtml`, called at `:479,488,525,565,586,601,616,636` — but nothing catches a dropped call | Add `test_ui_xss.py` (Playwright/jsdom) injecting `<img onerror=…>` via `sendData()`, OR a static source-guard asserting every `innerHTML=` is paired with `escapeHtml()` (mirror the `inspect.getsource` guards in `test_determinism_e2e.py`) |
| nit | No automated tests for the ui.html JS layer at all | ~106 `innerHTML`/`addEventListener`/`document.*` ops; `test_app.py` covers only the Python API | Add a Playwright/jsdom suite for view switching, theme persistence, file-input validation, and the `showResult()` JSON→DOM pipeline with adversarial payloads |
| minor | No SSRF regression test for `_clean_campus`/`_clean_terms` | `grep _clean_campus\|_clean_terms tests/` → no results; `chat_assist.py:156-158` is the SSRF guard | Add parameterized tests: reject `../LAMC`, `http://LAMC`, `LAMC:9000`, `'A'*9`, null byte, emoji; accept `LAMC`; `_clean_terms` rejects `0`/negatives; add a source-guard that `route()` always cleans campus before building a fetch URL |
| minor | Prompt-injection fence/SSRF tested as unit cases, not corpus/fuzz | `test_chat_assist.py:179-220` covers forged-fence + malformed JSON; no `_clean_campus` edge/fuzz table | Add a parameterized edge table + a `hypothesis` fuzz asserting `_clean_campus` returns `None` for non-alphanumeric input |
| minor | No cross-machine/cross-version CP-SAT determinism probe | `test_determinism_e2e.py:79-100` checks **source** for `random_seed`/`workers`; same-process byte-identity only; CI lacks an OS/arch matrix | Add `test_solver_version_matches_pinned_lock()` (assert `ortools.__version__=='9.15.6755'` vs lock) + per-version goldens (3.12/3.13), or a post-release Linux+macOS(arm64/Intel) canonical-JSON compare |
| minor | No network-sabotage test for full `analyze_live` sweep | `test_privacy_invariants.py` sabotages `engine.run` only; `analyze_live` is tested via `FakeClient`, never mid-sweep network failure | Add `test_network_sabotage_analyze_live`: `FakeClient` raising `ConnectionError` on the 2nd call → assert a graceful error dict, not a traceback |
| minor | Java PDF subprocess: no malformed-PDF integration test | `test_pdf_loader.py` tests Java detection; real extraction is `@pytest.mark.live`/deselected | Add a non-live test feeding a truncated PDF with a subprocess timeout, asserting `PdfLoadError` (no hang) |
| minor | Detector registry order has structural pin but no end-to-end slot test | `test_analysis_detectors_registry.py:37-46,55-64` zips against a **hardcoded** `EXPECTED`; a mid-list insert passes if `EXPECTED` is kept in sync manually | Add `test_full_detector_pipeline_inert_detectors_order()` zipping `results['inert_detectors']` against expected `(analysis_key, detector_key)`; or derive the source-of-truth via `ast.parse` instead of a hardcoded list |
| minor | No JRE/jar checksum re-verification at bundle runtime | `verify_macos_build.sh:89-119` checks existence + `java -version` but no checksum; ODL jar unverified post-build | Add `test_build_integrity.py`: assert bundled `java` sha256 matches `build/jre/.SchedulePlanner-jre-sha256` and the ODL jar is a valid archive |
| minor | No CSV/XLSX injection test for catalog/programs sheets | `test_schedule_import.py`/`test_mapping.py` cover schema violations but not `=cmd`/null-byte/2 MB-string content | Add `test_csv_injection.py`: formula-prefixed course IDs and U+0000 titles load without becoming formulas or crashing |
| minor | No registry-order regression test beyond the hardcoded pin | `test_analysis_detectors_registry.py:37-46` passes only if `EXPECTED` is hand-synced | Compare `ANALYSIS_DETECTORS` against a source-derived truth (`ast.parse` of `build_live_workbook.py`), or a build-time CI check |

**Already solid:** `test_determinism_e2e.py` (byte-identity, CSV/XLSX parity, solver pinning, LLM non-interference); `test_privacy_invariants.py` (zero network IO + zero PII from `engine.run`, real-key instructor-drop regression); 22 `test_source_contracts.py` schema-drift guards across all four live sources with named negative contracts; 46-test `test_live_offline_pipeline.py` fixture replay; chat injection/breakout/groundedness unit tests; corrupt-`.xlsx` and malformed-JSON tolerance tested.

---

## Prioritized action list (must-fix before ship)

**None.** There are 0 blocker and 0 major findings — nothing gates the v0.2.0 release.

Recommended **fast-follow** punch-list (highest-value minors, in priority order — track post-ship, not ship-blocking):

1. **[Security · SAST]** Wrap `COLLEGE_CONFIGS[campus]` in `program_mapper.py:49-56` with `try/except KeyError → SourceDataError` listing valid campuses (matches `assist.py`/`elumen_client.py`). Restores a readable error for an invalid free-text campus.
2. **[Tests · SAST]** Add the UI XSS regression guard — at minimum a static source-check that every `innerHTML=` in `ui.html` is paired with `escapeHtml()`; ideally a Playwright/jsdom injection test.
3. **[Tests · SAST]** Add `_clean_campus`/`_clean_terms` SSRF regression tests (path-traversal/scheme/port/over-length/null-byte) + a `route()`-cleans-campus source-guard.
4. **[Security · LICENSES]** Close the platform-conditional SBOM-gate gap (Windows-runner second pass or `UNKNOWN`-rejecting allowlist) so a Windows-only GPL dep can't slip the no-copyleft gate.
5. **[Security · SUPPLY-CHAIN]** Hash-pin (or hash-verify) the `pyinstaller==6.20.0` build-tool install in `release-build.yml`.
6. **[Tests]** Add a cross-version solver determinism probe (`ortools.__version__` pin assertion + per-version goldens) and a network-sabotage test for the full `analyze_live` sweep.

---

## What's already done well

- **Supply chain, gated end-to-end:** E4 pip-audit CVE gate (every push/PR + weekly, empty ignore-list), E16 CycloneDX SBOM + no-GPL/AGPL license gate (Temurin JRE correctly gate-exempt via Classpath Exception; CPython disclosed), E5 SLSA Build L2 keyless Sigstore provenance verifiable with `gh attestation verify`. Hash-pinned lock (`--require-hashes`), all runtimes current/non-EOL.
- **Determinism doctrine, proven:** byte-identical `engine.run` across runs and input surfaces (CSV/XLSX parity); CP-SAT pinned (`random_seed=42`, `num_search_workers=1`, **work-based** `max_deterministic_time`, not wall-clock); advisory detectors run outside `engine.run`; `proven_optimal` computed, not constant.
- **Honesty / no-silent-drops, enforced:** ordered render registries (`SECTION_RENDERERS`, `GROUNDERS`, `ANALYSIS_DETECTORS`) byte-pinned by golden tests; every finding surfaces on report + chat + UI; every proxy explicitly labeled (offering proxy ≠ student outcome).
- **Evidence-honesty:** sector figures framed as "from OTHER institutions… NOT a measurement of this campus"; unvetted campus numbers forbidden at load time.
- **Privacy ceiling held (#17):** `test_privacy_invariants.py` proves zero network IO and zero PII out of `engine.run`; analysis is structural only — no student-level data exists or is read.
- **LLM/AI surface hardened:** schema-constrained routing (E17), indirect-prompt-injection content fence with anti-breakout test (E18), RAGAS-style groundedness guard (E19), `_clean_campus` SSRF clamp, and `.textContent`/`escapeHtml()` XSS sinks in the webview.
- **Build efficiency:** 365 MB bundle via surgical `--collect-data` + `--exclude-module` guards (verified no-torch by `verify_macos_build.sh`), deterministic sha256-pinned Temurin JRE, truly-optional stdlib-only Ollama path.
