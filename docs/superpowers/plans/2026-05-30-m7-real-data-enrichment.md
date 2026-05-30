# m7 Real-Data Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill the two INERT live-data gaps additively: (1) `catalog['Prerequisites (structured)']` via an exact DNF→CNF converter with a configurable guard + flagged conservative fallback, fed by a fixture-only eLumen DNF parser; (2) `sections['Cap Enrl'/'Tot Enrl'/'Wait Tot']` via an IR-shaped enrollment workbook reader joined on `(term, class_nbr)`. Flip `INERT_DETECTORS` to active only when the data is present. `engine.py` is UNTOUCHED.

**Architecture:** New pure modules `sources/prereq_cnf.py`, `sources/elumen.py`, `sources/enrollment.py`; additive changes to `sources/mapping.py` (two builders gain optional enrichment args defaulting to today's behavior) and `build_live_workbook.py` (optional enrollment/eLumen inputs; detector activation). Network IO stays outside `engine.run()`; all HTTP would route through `sources/http.py` `get_json(..., client=...)`. DNF→CNF and the eLumen parser are pure (no network, no pandas in the core fn).

**Tech Stack:** Python 3.10+, pandas, openpyxl, ortools, pytest. Offline by default (`pytest.ini addopts = -m "not live"`).

**Branch:** `feat/m7-real-data` (STAY on it — do not branch/push/merge).

**Spec:** `docs/superpowers/specs/2026-05-30-m7-real-data-enrichment-design.md`

**Commit trailer (required on every commit):**
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

**Commit hygiene:** stage ONLY the exact files each task names (`git add <paths>`). NEVER `git add -A` / `git add .` — the tree has untracked `.claude/` and `docs/MANUAL_QA_RUNBOOK.md` that must NOT be committed.

---

## Verified facts driving the code (from reading engine.py + the survey + the IR fixture)

- Engine reads only: sections `Term, CLASS, Class Status, Cap Enrl, Tot Enrl, Wait Tot`; catalog `Course ID, Units, Prerequisites (structured)`; programs `Program Code, Program Title, Course ID, Recommended Semester`. `REQUIRED_COLUMNS` (`engine.py:22-26`) is duplicated in `mapping.SECTION/CATALOG/PROGRAM_COLUMNS` (`mapping.py:20-22`) — keep both byte-identical. The enrichment adds NO columns.
- `parse_prereq` (`engine.py:89-100`): `"(A OR B) AND (C)" → [["A","B"],["C"]]`; blank/NaN → `[]`. Single clauses must be parenthesized (`"(A)"`) to force the structured branch.
- `closure` (`engine.py:113-121`) pulls every prereq literal into the scheduled set; `solve_cohort` (`engine.py:170`) filters each OR-group to `p in courses`, so literals must be `_norm`-canonical.
- Enrollment seam: `build_sections_df` hard-codes `Cap/Tot/Wait = 0` (`mapping.py:64-66`). Replace with `r.get('Cap Enrl', 0)` etc.
- Prereq seam: `build_catalog_df` hard-codes `''` (`mapping.py:86`). Add optional `prereqs` map, emit `(prereqs or {}).get(cid, '')`.
- IR fixture `files/lamc_sample_enrollment.xlsx` `sections` sheet columns include `Term, Class Nbr, ..., Cap Enrl, Tot Enrl, Wait Cap, Wait Tot, ..., CLASS`; terms `{2248, 2252}`; populated counts; no instructor PII. `schedule.fetch_sections` records already carry `term` and `class_nbr` (`schedule.py:77,83`).
- Empirically (ran `engine.solve_cohort`): for a true `(A AND B) OR D`, the exact CNF is FEASIBLE but the over-approx `(A) AND (B) AND (D)` is INFEASIBLE/fabricates fixes. Therefore the guard fallback UNDER-approximates (single union OR-clause).

---

### Task 1: DNF→CNF converter (`sources/prereq_cnf.py`) — OFFLINE, implement now

**Files:**
- Create: `sources/prereq_cnf.py`
- Test: `tests/test_prereq_cnf.py`

- [ ] **Step 1: Write the failing tests** (`tests/test_prereq_cnf.py`)

Cover (the spec's `must_test` list):
```python
import engine
from sources.prereq_cnf import dnf_to_cnf, to_catalog_string, DEFAULT_MAX_CLAUSES

def _groups(s):  # parse with the REAL engine parser
    return engine.parse_prereq(s)

def test_blank_and_empty_are_no_constraint():
    for dnf in ([], None):
        r = dnf_to_cnf(dnf)
        assert r.cnf_string == "" and r.exact is True
        assert _groups(r.cnf_string) == []

def test_single_course_is_parenthesized():
    r = dnf_to_cnf([["A"]])
    assert r.cnf_string == "(A)"          # NOT bare 'A'
    assert _groups(r.cnf_string) == [["A"]]

def test_exact_distribution_common_path():
    # (A AND B) OR D  ->  (A OR D) AND (B OR D)
    r = dnf_to_cnf([["A", "B"], ["D"]])
    assert r.exact is True
    assert r.cnf_string == "(A OR D) AND (B OR D)"
    assert _groups(r.cnf_string) == [["A", "D"], ["B", "D"]]

def test_within_clause_dedup_and_subsumption():
    # (A AND B) OR (A AND C) -> distribute -> minimize -> (A) AND (B OR C)
    r = dnf_to_cnf([["A", "B"], ["A", "C"]])
    assert r.cnf_string == "(A) AND (B OR C)"

def test_single_disjunctive_branch_collapses():
    # (A OR B OR C) expressed as DNF [[A],[B],[C]] -> CNF (A OR B OR C)
    r = dnf_to_cnf([["A"], ["B"], ["C"]])
    assert r.cnf_string == "(A OR B OR C)"
    assert _groups(r.cnf_string) == [["A", "B", "C"]]

def test_normalization_merges_spellings():
    r = dnf_to_cnf([["MATH 245"], ["math  245"]])
    assert r.cnf_string == "(MATH 245)"   # _norm-merged, canonical

def test_empty_and_term_is_tautology_not_unsat():
    # an empty AND-term = TRUE => whole disjunction true => no prereq
    r = dnf_to_cnf([[], ["A"]])
    assert r.cnf_string == "" and r.exact is True   # NOT an empty OR-clause

def test_self_reference_dropped_to_no_constraint():
    r = dnf_to_cnf([["C"]], gated_course="C")
    assert r.cnf_string == "" and r.exact is False
    assert r.fallback_reason == "self_referential_prereq_dropped"

def test_guard_exceeded_emits_flagged_union_fallback():
    # 3x3 DNF predicts 9 clauses; budget 4 forces the fallback
    dnf = [["A", "B", "C"], ["D", "E", "F"], ["G", "H", "I"]]
    r = dnf_to_cnf(dnf, max_clauses=4)
    assert r.exact is False
    assert "clause_budget_exceeded" in r.fallback_reason
    assert _groups(r.cnf_string) == [["A", "B", "C", "D", "E", "F", "G", "H", "I"]]
    # every source literal retained (closure unchanged)
    assert set(_groups(r.cnf_string)[0]) == {"A","B","C","D","E","F","G","H","I"}

def test_just_under_budget_stays_exact():
    # 2x2 predicts 4 clauses; budget 4 -> exact (boundary)
    r = dnf_to_cnf([["A", "B"], ["C", "D"]], max_clauses=4)
    assert r.exact is True

def test_fallback_is_under_approx_keeps_engine_feasible():
    # The load-bearing safety property, validated end-to-end against solve_cohort.
    # Build a binding scenario where exact CNF is FEASIBLE but over-approx
    # (A AND B AND D) would be INFEASIBLE; assert the union fallback stays feasible.
    # (Construct the cohort/program/seasons inline like test_engine_features.py.)
    ...
```

- [ ] **Step 2: Run, verify failure** — `pytest tests/test_prereq_cnf.py -v` → `ModuleNotFoundError: sources.prereq_cnf`.

- [ ] **Step 3: Implement `sources/prereq_cnf.py`** per spec §4: `ConversionResult` dataclass, `dnf_to_cnf(dnf, *, gated_course=None, max_clauses=DEFAULT_MAX_CLAUSES)`, `to_catalog_string(cnf_groups)`. Normalize via `mapping._norm`; absorption pre-pass; perf guard on predicted product BEFORE `itertools.product`; exact distribution + within-clause dedup + clause subsumption + lexicographic sort; serialize with always-parenthesized clauses. Fallback = single union OR-clause, `exact=False`, structured `fallback_reason`. `DEFAULT_MAX_CLAUSES = 64`, `BRANCH_CAP = 12`. Raise `SourceDataError` (from `sources.http`) on wrong-nesting input.

- [ ] **Step 4: Run, verify pass** — `pytest tests/test_prereq_cnf.py -v`.

- [ ] **Step 5: Commit**
```bash
git add sources/prereq_cnf.py tests/test_prereq_cnf.py
git commit -m "feat(m7): exact DNF->CNF prereq converter with guard + flagged under-approx fallback

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: eLumen DNF parser + prereq map (`sources/elumen.py`) — OFFLINE/fixture-only, implement now

**Files:**
- Create: `sources/elumen.py`, `tests/fixtures/elumen_prereqs_LAMC.json`
- Test: `tests/test_elumen_prereq_mapping.py`
- Depends on: Task 1

> FIXTURE-ONLY: there is no real eLumen endpoint/auth/response. This module parses a self-defined committed fixture and is labeled *not-validated-on-real-data* in its docstring and the report. It does NOT make any network call.

- [ ] **Step 1: Create the committed fixture** `tests/fixtures/elumen_prereqs_LAMC.json` modeling the DOCUMENTED eLumen shape (course id → DNF + raw text):
```json
{
  "source": "elumen (FIXTURE-ONLY: self-defined shape, not captured from a real endpoint)",
  "campus": "LAMC",
  "courses": [
    {"course_id": "ENGL 102", "raw": "Prerequisite: ENGL 101", "dnf": [["ENGL 101"]]},
    {"course_id": "MATH 246", "raw": "MATH 245", "dnf": [["MATH 245"]]},
    {"course_id": "PHYS 102", "raw": "(MATH 245 and MATH 246) or PHYS 185", "dnf": [["MATH 245", "MATH 246"], ["PHYS 185"]]},
    {"course_id": "CHEM 102", "raw": "CHEM 101 or CHEM 105", "dnf": [["CHEM 101"], ["CHEM 105"]]}
  ]
}
```

- [ ] **Step 2: Write the failing test** (`tests/test_elumen_prereq_mapping.py`): load the fixture, assert `parse_elumen_dnf` returns the normalized DNF; assert `build_prereq_map` yields `{"ENGL 102": "(ENGL 101)", "PHYS 102": "(MATH 245 OR PHYS 185) AND (MATH 246 OR PHYS 185)", "CHEM 102": "(CHEM 101 OR CHEM 105)", ...}` and that every value round-trips through `engine.parse_prereq`; assert the returned `ConversionResult`s are all `exact=True` for this small fixture. Assert NO socket (build a fixture-loader that reads the JSON; no client used).

- [ ] **Step 3: Run, verify failure.**

- [ ] **Step 4: Implement `sources/elumen.py`** — `parse_elumen_dnf(record)` (normalize via `mapping._norm`, validate nesting → `SourceDataError`), `build_prereq_map(records, *, max_clauses=DEFAULT_MAX_CLAUSES)` calling `prereq_cnf.dnf_to_cnf` per course, returning `(prereqs_str_map, results_map)`. Module docstring DOCUMENTS the assumed real eLumen response shape and the *fixture-only* caveat.

- [ ] **Step 5: Run, verify pass.**

- [ ] **Step 6: Commit**
```bash
git add sources/elumen.py tests/test_elumen_prereq_mapping.py tests/fixtures/elumen_prereqs_LAMC.json
git commit -m "feat(m7): fixture-only eLumen DNF parser -> CNF prereq map (not validated on real data)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: IR enrollment ingest (`sources/enrollment.py`) — OFFLINE, implement now

**Files:**
- Create: `sources/enrollment.py`
- Test: `tests/test_enrollment_ingest.py`

> Uses the EXISTING committed fixture `files/lamc_sample_enrollment.xlsx` (real IR PeopleSoft shape, populated counts, no PII). Pure file read — no network.

- [ ] **Step 1: Write the failing test** (`tests/test_enrollment_ingest.py`):
  - `load_enrollment(files/lamc_sample_enrollment.xlsx)` returns a dict keyed `(int term, str class_nbr)` with `Cap/Tot/Wait`; assert a known planted row (e.g. ACCTG 2 / ENGL 101) has the expected non-zero counts.
  - `enrich_sections([{term, course, class_nbr}, ...], enrollment)` writes `Cap Enrl/Tot Enrl/Wait Tot` onto matched records; an unmatched record keeps no enrollment keys (→ stays 0 downstream).
  - Missing-column workbook raises `SourceDataError` naming the file.
  - Join normalizes `class_nbr` to str on both sides (int vs str CRN).

- [ ] **Step 2: Run, verify failure.**

- [ ] **Step 3: Implement `sources/enrollment.py`** — `load_enrollment(path)` (read `sections` sheet, require `Term, Class Nbr, Cap Enrl, Tot Enrl, Wait Tot` → `SourceDataError` on absence), `enrich_sections(records, enrollment)` joining on `(int(term), str(class_nbr))`. Pure; no network. Document the expected real IR schema (spec §9) in the module docstring.

- [ ] **Step 4: Run, verify pass.**

- [ ] **Step 5: Commit**
```bash
git add sources/enrollment.py tests/test_enrollment_ingest.py
git commit -m "feat(m7): IR PeopleSoft enrollment ingest joined on (term, class_nbr)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Additive mapping seams (`sources/mapping.py`) — OFFLINE, implement now

**Files:**
- Modify: `sources/mapping.py`
- Test: `tests/test_mapping.py` (append; existing tests must stay green byte-identical)

> Additive only. Defaults preserve today's behavior — `test_mapping.py:33` (all-zero enrollment) and `:44` (all-blank prereqs) must still pass unchanged. SECTION/CATALOG/PROGRAM_COLUMNS unchanged.

- [ ] **Step 1: Append failing tests** to `tests/test_mapping.py`:
  - `build_sections_df` with records carrying `Cap Enrl/Tot Enrl/Wait Tot` emits those values; without them emits `0` (default unchanged).
  - `build_catalog_df(SECTIONS, PROGRAM, prereqs={"CS 101": "(MATH 245)"})` emits that CNF string for CS 101 and `""` for others; `prereqs=None` (default) emits all-blank (unchanged).
  - Assert column lists still equal `engine.REQUIRED_COLUMNS` for all three sheets (drift guard).

- [ ] **Step 2: Run, verify the NEW tests fail and the OLD ones still pass.**

- [ ] **Step 3: Implement the two seams:**
  - `build_sections_df` (`mapping.py:63-65`): `"Cap Enrl": r.get("Cap Enrl", 0), "Tot Enrl": r.get("Tot Enrl", 0), "Wait Tot": r.get("Wait Tot", 0)`.
  - `build_catalog_df(section_records, program, prereqs=None)`: emit `(prereqs or {}).get(cid, "")` instead of `""`.

- [ ] **Step 4: Run, verify pass** — `pytest tests/test_mapping.py -v` (old + new all green).

- [ ] **Step 5: Commit**
```bash
git add sources/mapping.py tests/test_mapping.py
git commit -m "feat(m7): additive enrollment + prereq seams in mapping builders (defaults preserve behavior)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Live-detector activation + wiring (`build_live_workbook.py`) — OFFLINE, implement now

**Files:**
- Modify: `build_live_workbook.py`
- Test: `tests/test_detector_activation.py`

> Wires the enrollment join + eLumen prereq map into the pipeline OUTSIDE `engine.run()`, and flips `INERT_DETECTORS` entries to active only when data is present. Tested fully offline with the committed fixtures (no network).

- [ ] **Step 1: Write the failing test** (`tests/test_detector_activation.py`):
  - Build a small section-record list + program, join `enrollment.enrich_sections` from `files/lamc_sample_enrollment.xlsx`-derived counts (or a small inline enrollment map), thread an eLumen-derived prereq map, `mapping.write_workbook`, `engine.run` → assert `modality_mismatch`/`under_supply` now NON-empty for the planted course, and a program with prereqs shows ordering (course scheduled after its prereq).
  - Assert the report marks those `INERT_DETECTORS` entries active when data present, and inert when absent.
  - Assert a budget-fallback course is labeled *conservative-permissive, not exact* in the report.
  - All offline: use `lamc_routes` (longest-match `FakeClient`) for any schedule/PM fetch path exercised.

- [ ] **Step 2: Run, verify failure.**

- [ ] **Step 3: Implement** in `build_live_workbook.py`: optional `enrollment_path` and `elumen_fixture` params on `analyze_live`/`build`; when present, `enrollment.enrich_sections(...)` and `elumen.build_prereq_map(...)` run (outside `engine.run`), thread into `mapping.write_workbook`/`build_catalog_df`, and rewrite the `inert_detectors` report so satisfied detectors become `{"detector": ..., "status": "active", "source": ...}` while the rest keep their honest inert reason. Add per-course prereq-conversion summary (exact vs fallback). Keep `engine.py` untouched. Add `--enrollment` / `--elumen-fixture` CLI args.

- [ ] **Step 4: Run, verify pass** + full suite `pytest -q` (all offline green; 3 live deselected).

- [ ] **Step 5: Commit**
```bash
git add build_live_workbook.py tests/test_detector_activation.py
git commit -m "feat(m7): flip INERT_DETECTORS to active when enrollment/prereqs present; wire enrichment outside engine.run

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6 (PLAN-ONLY — do NOT implement in this run): live eLumen HTTP client

**Files (future):** `sources/elumen_client.py`, `tests/fixtures/elumen_*_LAMC.json` (captured), `tests/test_elumen_client.py`, route in `tests/conftest.py` `lamc_routes`.

> BLOCKED: no eLumen endpoint, auth, or captured response is known. Building this now would require inventing credentials/endpoints — forbidden by the m7 no-fabrication constraint. Documented here so it can be picked up the moment a real response is captured.

Plan when unblocked:
- [ ] Capture a real eLumen prereq response into `tests/fixtures/elumen_<...>_LAMC.json`; confirm the actual DNF/text shape vs. `sources/elumen.py`'s assumed shape; adjust `parse_elumen_dnf` if it diverges.
- [ ] Build `sources/elumen_client.py` mirroring `program_mapper.py`: per-campus config, `_headers` spoofing Origin/Referer, `get_json(..., client=client, source="eLumen ...")`, reuse `SourceError/SourceHTTPError/SourceDataError`.
- [ ] Add a `lamc_routes` entry (unique URL fragment, longest-match) and an offline client test through `FakeClient`.
- [ ] Add one `@pytest.mark.live` end-to-end test (deselected by default).
- [ ] Wire the real client into `build_live_workbook.build()` alongside schedule/program_mapper with the injected client; replace the fixture path with the live fetch; flip `prerequisite_ordering` active on real data and remove the *fixture-only* label for that path.

---

### Task 7: Final suite + manual smoke (no commit beyond Tasks 1–5)

- [ ] **Step 1:** `pytest -q` → all offline tests pass, 3 live deselected.
- [ ] **Step 2:** `pytest -m live` is NOT run here (network).
- [ ] **Step 3 (manual, offline):** `python3 build_live_workbook.py --campus LAMC --program "Biology" --terms 2264,2266,2268 --enrollment files/lamc_sample_enrollment.xlsx --elumen-fixture tests/fixtures/elumen_prereqs_LAMC.json --out data/live_LAMC.xlsx` — confirm the report shows active detectors for the enriched fields and the *fixture-only* eLumen label. (Live schedule/PM fetch still needs network; the enrichment seams themselves are offline.)

---

## Self-Review

**1. Spec coverage:**
- §4 DNF→CNF (exact + guard + flagged under-approx fallback) → Task 1. ✓
- §5 eLumen fixture-only parser/map → Task 2. ✓
- §6 IR enrollment ingest + join → Task 3. ✓
- §3/§5/§6 additive mapping seams (defaults preserve today) → Task 4. ✓
- §7 detector activation (only when data present; honest labels) → Task 5. ✓
- §9 documented IR schema → spec §9; ingest validates required columns (Task 3). ✓
- §10 eLumen client plan-only (no fabricated endpoint) → Task 6 (plan-only). ✓
- §2 boundary (network outside engine.run; engine untouched; mapping pure) → enrichment runs in build_live_workbook / pure modules; no engine.py change. ✓
- §11 no overclaiming (fixture-only labels, flagged fallback) → Tasks 2, 5, report fields. ✓

**2. Offline/implement-now correctness:** Tasks 1–5 are fully buildable AND verifiable now with pure unit tests or committed fixtures (`tests/fixtures/elumen_prereqs_LAMC.json`, `files/lamc_sample_enrollment.xlsx`); no live network, no real IR/eLumen data. Task 6 (live eLumen HTTP client) is implement_now:false — it needs a real captured response we cannot fixture without fabrication.

**3. No-regression:** Task 4 keeps `build_sections_df`/`build_catalog_df` defaults byte-identical, so the existing `test_mapping.py`, `test_live_roundtrip.py`, `test_live_offline_pipeline.py`, and `test_sample_enrollment_fixture.py` stay green. `engine.py` and the column constants are untouched.

**4. Placeholder scan:** Task 1's `test_fallback_is_under_approx_keeps_engine_feasible` body is sketched (`...`) — the implementer constructs the binding scenario inline (mirror `tests/test_engine_features.py`); every other step has concrete code/commands. No TODOs in production code.

**5. Determinism:** No CP-SAT parameter change. DNF→CNF output is lexicographically sorted (byte-stable). The IR fixture is byte-deterministic (`test_sample_enrollment_fixture.py` pins it).
