# m7 Real-Data Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill the two INERT live-data gaps additively: (1) `catalog['Prerequisites (structured)']` via an exact DNF→CNF converter with a configurable guard + flagged conservative fallback, fed by a fixture-only eLumen DNF parser; (2) `sections['Cap Enrl'/'Tot Enrl'/'Wait Tot']` via an IR-shaped enrollment workbook reader joined on `(term, class_nbr)`. Flip `INERT_DETECTORS` to active only when the data is present. `engine.py` is UNTOUCHED.

**Architecture:** New pure modules `sources/prereq_cnf.py`, `sources/elumen.py`, `sources/enrollment.py`; additive changes to `sources/mapping.py` (three builders — `build_sections_df`, `build_catalog_df`, `write_workbook` — gain optional enrichment args defaulting to today's behavior) and `build_live_workbook.py` (optional enrollment/eLumen inputs; detector activation). Network IO stays outside `engine.run()`; all HTTP would route through `sources/http.py` `get_json(..., client=...)`. DNF→CNF and the eLumen parser are pure (no network, no pandas in the core algorithm — `prereq_cnf.py` INLINES `_norm` rather than importing `mapping._norm`, which would transitively pull pandas/httpx).

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
- `parse_prereq` (`engine.py:89-100`): `"(A OR B) AND (C)" → [["A","B"],["C"]]`; blank/NaN → `[]`. Single clauses must be parenthesized (`"(A)"`) to force the structured branch — **defensive only**: with `llm=None` (the `build_live_workbook` call path), `parse_prereq('MATH 245')` already returns `[['MATH 245']]`, so a bare token does NOT currently misparse; parenthesizing guarantees the structured branch if an `llm` is ever wired in. **Delimiter/paren hazard:** `parse_prereq` splits naively on `" AND "`/`" OR "` and strips outer `()`, so a literal containing those substrings corrupts the round-trip — verified `'(BIO 3 OR 4)' → [['BIO 3','4']]` (phantom `'4'`), `'(MATH 125 (FORMERLY 120))' → [['MATH 125 (FORMERLY 120']]` (stray paren). `prereq_cnf` must route such literals to the flagged fallback, never emit them.
- `closure` (`engine.py:113-121`) pulls every prereq literal into the scheduled set; `solve_cohort` (`engine.py:170`) filters each OR-group to `p in courses`, so literals must be `_norm`-canonical. An OR-group that filters to empty is `continue`-skipped (`engine.py:171-172`) → **no-op (no constraint), NOT FALSE/UNSAT** (verified: `()` parses to `[['']]` and acts as no-constraint, not infeasibility). A **self-prereq IS catastrophic**: `prereqs={'C':[['C']]}` makes `solve_cohort` return `None` = false-INFEASIBLE (verified).
- Enrollment seam: `build_sections_df` hard-codes `Cap/Tot/Wait = 0` (`mapping.py:63-65`). Replace with `r.get('Cap Enrl', 0)` etc.
- Prereq seam: `build_catalog_df` hard-codes `''` (`mapping.py:86`). Add optional `prereqs` map, emit `(prereqs or {}).get(cid, '')`.
- IR fixture `files/lamc_sample_enrollment.xlsx` `sections` sheet columns include `Term, Class Nbr, ..., Cap Enrl, Tot Enrl, Wait Cap, Wait Tot, ..., CLASS`; terms `{2248, 2252}`; `Class Nbr` is a **bare int** (`20001`); `CLASS` is `'ACCTG 2'`/`'MATH 245'` (abbreviated, unpadded); populated counts; no instructor PII; `(term, class_nbr)` unique (116/116). `schedule.fetch_sections` records carry `term` and `class_nbr` (`schedule.py:77,83`), **BUT `class_nbr` is a decorated string** `'17818 (LEC)'`/`'17819 (LAB)'` (verified: ALL 81 schedule-fixture records). **The join MUST strip the ` (LEC)`/` (LAB)` suffix to a bare CRN** (`re.match(r'\s*(\d+)', ...)`) before comparing — plain `str()` never matches `'17818 (LEC)'` to `17818`. **No committed schedule (term 2268) + enrollment (terms {2248,2252}) fixture pair shares a `(term, CRN)`** (zero term overlap; CRN sets disjoint), so the live-schedule↔IR join is fixture-only / NOT validated end-to-end — see Task 3/Task 5/Task 7.
- `_norm` import note: `prereq_cnf.py` must NOT do `from .mapping import _norm` — that transitively pulls `pandas` + `httpx` into the "pure" module (verified). It **inlines** `re.sub(r"\s+", " ", str(x).strip().upper())` with a comment pinning byte-identity to `mapping._norm`.
- Empirically (ran `engine.solve_cohort`): for a true `(A AND B) OR D` under a **UNIT-CAP bind** (`horizon=2, max_units=6`, all of `{X,A,B,D}` offered every season), the exact CNF `[['A','D'],['B','D']]` is FEASIBLE but the over-approx `(A) AND (B) AND (D)` is INFEASIBLE; the union `(A OR B OR D)` stays FEASIBLE (verified `exact=feasible over=INFEAS union=feasible`). A **season** bind does NOT reproduce this (closure schedules all literals; killing one season kills exact and over together → vacuous test). Therefore the guard fallback UNDER-approximates (single union OR-clause), and the load-bearing safety test uses a UNIT-CAP bind, not a season bind.

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

def test_blank_empty_none_nan_are_no_constraint():
    # [] (top-level empty disjunction), None, NaN, and [[]] (one empty AND-branch)
    # all mean "no prereq" -> '' exact=True (the cases §4.2 step 3 hinges on).
    for dnf in ([], None, float("nan"), [[]], [[], ["A"]]):
        r = dnf_to_cnf(dnf)
        assert r.cnf_string == "" and r.exact is True
        assert _groups(r.cnf_string) == []

def test_malformed_non_empty_payload_raises():
    import pytest
    from sources.http import SourceDataError
    with pytest.raises(SourceDataError):
        dnf_to_cnf("MATH 245")          # bare non-empty string, not list-of-lists
    with pytest.raises(SourceDataError):
        dnf_to_cnf([["A"], "B"])        # mixed/wrong nesting

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

def test_common_literal_factoring():
    # (A AND X) OR (B AND X) -> (A OR B) AND (X)  [canonical shared-coreq eLumen pattern]
    r = dnf_to_cnf([["A", "X"], ["B", "X"]])
    assert r.cnf_string == "(A OR B) AND (X)"
    assert _groups(r.cnf_string) == [["A", "B"], ["X"]]   # verified round-trip

def test_clause_sort_key_is_list_not_rendered_string():
    # list-vs-string sort diverges: groups [['A','B'],['A','B','C']] sort as LISTS to
    # [['A','B'],['A','B','C']] but as rendered strings to ['(A OR B OR C)','(A OR B)'].
    # Pin the list-key order. Force exact (no subsumption collapse) via a fresh budget.
    r = dnf_to_cnf([["A", "B"], ["A", "B", "C"]], max_clauses=64)
    # NOTE: subsumption drops the superset clause ['A','B','C'] (['A','B'] subset),
    # so this particular input minimizes to (A OR B). To exercise the SORT itself,
    # use a non-subsumed pair, e.g. two clauses neither a subset of the other:
    r2 = dnf_to_cnf([["A", "C"], ["B", "C"]])          # -> (A OR B) AND (C)
    assert r2.cnf_string == "(A OR B) AND (C)"          # list-key, byte-stable
    assert _groups(r2.cnf_string) == [["A", "B"], ["C"]]

def test_delimiter_or_paren_literal_routes_to_flagged_fallback():
    # A literal carrying ' OR '/' AND '/'('/')' would corrupt parse_prereq round-trip.
    # It must NOT emit a mis-parsing string; the course is flagged (exact=False).
    for dnf in ([["BIO 3 OR 4"]], [["MATH 125 (FORMERLY 120)"]]):
        r = dnf_to_cnf(dnf)
        assert r.exact is False
        assert "unserializable_literal" in (r.fallback_reason or "")
        # the emitted string must not mis-parse into phantom/lost literals:
        # either '' (no constraint) or a clean structured group, never the corrupt form.
        groups = _groups(r.cnf_string)
        assert "4" not in (groups[0] if groups else [])           # no phantom literal
        assert all(")" not in lit and "(" not in lit for g in groups for lit in g)

def test_single_disjunctive_branch_collapses():
    # (A OR B OR C) expressed as DNF [[A],[B],[C]] -> CNF (A OR B OR C)
    r = dnf_to_cnf([["A"], ["B"], ["C"]])
    assert r.cnf_string == "(A OR B OR C)"
    assert _groups(r.cnf_string) == [["A", "B", "C"]]

def test_normalization_merges_spellings():
    r = dnf_to_cnf([["MATH 245"], ["math  245"]])
    assert r.cnf_string == "(MATH 245)"   # _norm-merged, canonical

def test_self_reference_dropped_to_no_constraint():
    r = dnf_to_cnf([["C"]], gated_course="C")
    assert r.cnf_string == "" and r.exact is False
    assert r.fallback_reason == "self_referential_prereq_dropped"

def test_self_reference_drop_normalizes_gated_course():
    # gated_course must be _norm'd BEFORE comparison, else a raw-cased self-ref
    # leaks through and makes solve_cohort false-INFEASIBLE. Lower-case 'c' + mixed
    # spellings must still drop the self-ref AND merge 'math 245'/'MATH 245'.
    r = dnf_to_cnf([["math 245"], ["C"]], gated_course="c")
    assert "C" not in [lit for g in _groups(r.cnf_string) for lit in g]   # self-ref gone
    assert r.cnf_string == "(MATH 245)"                                   # spelling merged

def test_guard_exceeded_emits_flagged_union_fallback():
    # 3 branches x 3 literals -> product 27 clauses; budget 4 forces the fallback.
    dnf = [["A", "B", "C"], ["D", "E", "F"], ["G", "H", "I"]]
    r = dnf_to_cnf(dnf, max_clauses=4)
    assert r.exact is False
    assert "clause_budget_exceeded" in r.fallback_reason
    assert _groups(r.cnf_string) == [["A", "B", "C", "D", "E", "F", "G", "H", "I"]]
    # every source literal retained (closure unchanged)
    assert set(_groups(r.cnf_string)[0]) == {"A","B","C","D","E","F","G","H","I"}

def test_fallback_union_excludes_self_reference():
    # On the budget-exceeded path the union must be over the CLEANED dnf, so a
    # self-referential literal must NOT leak back into the union OR-clause (that would
    # re-create the catastrophic self-prereq on the 'safe' fallback path).
    big = [["X", "A1", "B1"], ["X", "A2", "B2"], ["X", "A3", "B3"], ["X", "A4", "B4"]]
    r = dnf_to_cnf(big, gated_course="X", max_clauses=4)
    assert r.exact is False
    assert "X" not in _groups(r.cnf_string)[0]    # gated_course not in the union

def test_just_under_budget_stays_exact():
    # 2x2 predicts 4 clauses; budget 4 -> exact (boundary)
    r = dnf_to_cnf([["A", "B"], ["C", "D"]], max_clauses=4)
    assert r.exact is True

def test_guard_is_pre_minimization_conservative():
    # Documents the chosen behavior: the guard checks the PRE-minimization product.
    # (A AND B) OR (A AND C) has product 4 but minimizes to 2 clauses. With budget 3,
    # product (4) > budget so it falls back EARLY even though minimized clauses (2) fit.
    r = dnf_to_cnf([["A", "B"], ["A", "C"]], max_clauses=3)
    assert r.exact is False                       # fell back on pre-min product, by design
    assert "clause_budget_exceeded" in r.fallback_reason

def test_fallback_is_under_approx_keeps_engine_feasible():
    # The load-bearing safety property, validated end-to-end against solve_cohort.
    # UNIT-CAP bind (NOT season): H=2, max_units=6, all of {X,A,B,D} every season,
    # X's true prereq (A AND B) OR D. Exact CNF FEASIBLE, over-approx INFEASIBLE,
    # union FEASIBLE. (A season bind would be vacuous — see Verified facts.)
    import engine, pandas as pd
    def solve(prereq_for_X):
        terms = [2248, 2252]   # two distinct seasons; both offer every course
        sec = pd.DataFrame([
            {"Term": t, "CLASS": c, "Class Status": "Active",
             "Cap Enrl": 0, "Tot Enrl": 0, "Wait Tot": 0}
            for c in ("X", "A", "B", "D") for t in terms])
        cat = pd.DataFrame([
            {"Course ID": c, "Units": 3,
             "Prerequisites (structured)": (prereq_for_X if c == "X" else "")}
            for c in ("X", "A", "B", "D")])
        prog = pd.DataFrame([
            {"Program Code": "P", "Program Title": "T", "Course ID": c,
             "Recommended Semester": 1} for c in ("X", "A", "B", "D")])
        active, cs, units, prq = engine.build_model(sec, cat, prog)
        return engine.solve_cohort("P", prog, cs, units, prq,
                                   {"horizon": 2, "max_units": 6}, False)
    exact = dnf_to_cnf([["A", "B"], ["D"]], gated_course="X")
    assert exact.cnf_string == "(A OR D) AND (B OR D)" and exact.exact is True
    assert solve(exact.cnf_string) is not None           # exact FEASIBLE
    assert solve("(A) AND (B) AND (D)") is None           # over-approx false-INFEASIBLE
    union = dnf_to_cnf([["A", "B"], ["D"]], gated_course="X", max_clauses=1)
    assert union.exact is False and "clause_budget_exceeded" in union.fallback_reason
    assert solve(union.cnf_string) is not None           # union UNDER-approx stays FEASIBLE
```

- [ ] **Step 2: Run, verify failure** — `pytest tests/test_prereq_cnf.py -v` → `ModuleNotFoundError: sources.prereq_cnf`.

- [ ] **Step 3: Implement `sources/prereq_cnf.py`** per spec §4: `ConversionResult` dataclass, `dnf_to_cnf(dnf, *, gated_course=None, max_clauses=DEFAULT_MAX_CLAUSES)`, `to_catalog_string(cnf_groups)`. Specifics:
  - **Inline `_norm`** (`re.sub(r"\s+", " ", str(x).strip().upper())`) with a comment "must stay byte-identical to `mapping._norm`"; do NOT `from .mapping import _norm` (pulls pandas/httpx into this pure module).
  - **Top-level sentinels FIRST:** `None` / `NaN` (`isinstance(dnf, float) and math.isnan`) / `''` / `[]` → `('', exact=True)`. THEN validate: a non-empty payload that is not a list-of-lists → raise `SourceDataError` (from `sources.http`).
  - `gated_course = _norm(gated_course)` BEFORE the self-ref drop.
  - Per-literal: normalize, then **delimiter/paren check** — if `' OR '`/`' AND '`/`'('`/`')'` in the normalized literal, route the whole course to the flagged fallback with `fallback_reason="unserializable_literal: <lit>"` (drop that literal from any union it would otherwise enter).
  - Self-ref drop; truth-value short-circuits (empty AND-term = tautology → `''` exact=True); absorption pre-pass; perf guard on predicted product (post-absorption, pre-`itertools.product`); exact distribution + within-clause dedup + clause subsumption; sort literals within clauses, then **sort clauses by their sorted-literal LIST key** (not the rendered string); serialize with always-parenthesized clauses.
  - **Fallback** = single union OR-clause over the **CLEANED dnf** (after self-ref drop + absorption, excluding delimiter/paren literals), `exact=False`, structured `fallback_reason`. `DEFAULT_MAX_CLAUSES = 64`, `BRANCH_CAP = 12`.

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
  - `load_enrollment(files/lamc_sample_enrollment.xlsx)` returns a dict keyed `(int term, str bare-CRN)` with `Cap/Tot/Wait`; assert a known planted row has the expected non-zero counts. Key is `str(int(Class Nbr))` (e.g. `"20001"`), since IR `Class Nbr` is a bare int.
  - **CRN-suffix-strip on the schedule side (the load-bearing join test):** a record with the REAL decorated `class_nbr="17818 (LEC)"` joins to enrollment keyed `"17818"` (assert the leading-integer extraction `re.match(r"\s*(\d+)", ...)` works on the real shape). A plain `str("17818 (LEC)")` must NOT match `"17818"` — prove the strip is what makes it join.
  - `enrich_sections([...], enrollment)` returns a **NEW list** (does not mutate caller dicts) with `Cap Enrl/Tot Enrl/Wait Tot` on matched records; an unmatched record keeps no enrollment keys (→ stays 0 downstream).
  - **Idempotency + duplicate/blank handling:** a section list with a DUPLICATED `(term, CRN)` and a BLANK-`class_nbr` relsection — assert counts are written once per matching record and the blank-CRN record is NOT falsely matched (skip the join for blank `class_nbr`, never key on `""`). Calling `enrich_sections` twice yields the same result (idempotent).
  - Missing-column workbook raises `SourceDataError` naming the file.

- [ ] **Step 2: Run, verify failure.**

- [ ] **Step 3: Implement `sources/enrollment.py`** — `load_enrollment(path)` (read `sections` sheet, require `Term, Class Nbr, Cap Enrl, Tot Enrl, Wait Tot` → `SourceDataError` on absence; key `(int(Term), str(int(Class Nbr)))`), `enrich_sections(records, enrollment) -> new list` joining on `(int(term), _crn(class_nbr))` where `_crn = re.match(r"\s*(\d+)", str(class_nbr))` group(1), **skipping blank/non-numeric `class_nbr`** (no `""` key). Returns fresh dicts (pure, idempotent, non-aliasing). Pure; no network. Document the expected real IR schema (spec §9) in the module docstring, **including the explicit caveat that the live-schedule↔IR join is fixture-only / NOT validated end-to-end** (term + CRN disjoint across the committed schedule (2268) and enrollment ({2248,2252}) fixtures).

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
  - `write_workbook(records, program, path, prereqs={"CS 101": "(MATH 245)"})` round-trips that prereq into the catalog sheet; `write_workbook(records, program, path)` (no kwarg) stays byte-identical (all-blank).
  - Assert column lists still equal `engine.REQUIRED_COLUMNS` for all three sheets (drift guard).

- [ ] **Step 2: Run, verify the NEW tests fail and the OLD ones still pass.**

- [ ] **Step 3: Implement the three seams (all in `mapping.py`, owned by THIS task):**
  - `build_sections_df` (`mapping.py:63-65`): `"Cap Enrl": r.get("Cap Enrl", 0), "Tot Enrl": r.get("Tot Enrl", 0), "Wait Tot": r.get("Wait Tot", 0)`.
  - `build_catalog_df(section_records, program, prereqs=None)`: emit `(prereqs or {}).get(cid, "")` instead of `""`.
  - `write_workbook(section_records, program, path, *, prereqs=None)`: thread `prereqs` into `build_catalog_df(section_records, program, prereqs)`. The optional kwarg defaults to None so existing callers (and `test_live_roundtrip.py` / `test_live_offline_pipeline.py`) stay byte-identical. **This resolves the Task 4↔Task 5 ownership gap: `write_workbook` is modified HERE (mapping.py owner), and Task 5 only PASSES the prereq map + enriched records — it does not touch `mapping.py`.**

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

> Wires the enrollment join + eLumen prereq map into the pipeline OUTSIDE `engine.run()`, and flips `INERT_DETECTORS` entries to active only when data is present AND the join actually matched ≥1 section. Tested fully offline with the committed fixtures (no network).
>
> **JOIN IS FIXTURE-ONLY (no overclaiming).** No committed schedule (term 2268) + enrollment (terms {2248,2252}) fixture pair shares a `(term, CRN)`, so a real live-schedule run matches NOTHING (verified). The detector-activation test therefore drives the enrollment join with a **hand-keyed inline enrollment map whose `(term, CRN)` keys match the term-2268 schedule records** (or section records derived from the enrollment fixture's own terms) — and that synthetic-key fact is labeled in the test and the report. Do NOT assert that `--enrollment files/lamc_sample_enrollment.xlsx` against a live/2268 schedule activates detectors; it does not.

- [ ] **Step 1: Write the failing test** (`tests/test_detector_activation.py`):
  - Build a small section-record list + program. Drive the enrollment join with a **hand-keyed inline enrollment map** whose `(term, bare-CRN)` keys match the section records' stripped CRNs (label it: synthetic key — the real schedule↔IR fixtures do not overlap). `enrollment.enrich_sections(records, inline_map)`, thread an eLumen-derived prereq map, `mapping.write_workbook(..., prereqs=...)`, `engine.run` → assert `modality_mismatch`/`under_supply` now NON-empty for the planted course (fill < 0.55, Wait Tot > 15), and a program with prereqs shows ordering (course scheduled after its prereq).
  - Assert the report marks those `INERT_DETECTORS` entries active **only when the join matched ≥1 row**; inert when absent OR when the join matched zero rows.
  - Assert the enrollment activation is labeled *fixture-scoped (live-schedule↔IR join not validated on real data)*.
  - Assert a budget-fallback course is labeled *conservative-permissive, not exact* in the report; the eLumen path is labeled *fixture-only*.
  - Add a **zero-match guard test**: enrich the term-2268 schedule records with `load_enrollment(files/lamc_sample_enrollment.xlsx)` (terms {2248,2252}) → assert ZERO matches and the enrollment detectors stay INERT (proves the no-overclaim contract).
  - All offline: use `lamc_routes` (longest-match `FakeClient`) for any schedule/PM fetch path exercised.

- [ ] **Step 2: Run, verify failure.**

- [ ] **Step 3: Implement** in `build_live_workbook.py`: optional `enrollment_path` and `elumen_fixture` params on `analyze_live`/`build`; when present, `enrollment.load_enrollment(...)` + `enrollment.enrich_sections(...)` and `elumen.build_prereq_map(...)` run (outside `engine.run`) on the raw section records BEFORE `write_workbook`, thread the prereq map into `mapping.write_workbook(sections, program, out_path, prereqs=prereq_map)` (Task 5 does NOT modify `mapping.py` — it only passes the kwarg added in Task 4). Rewrite the `inert_detectors` report so a detector becomes `{"detector": ..., "status": "active", "source": ...}` **only when its data is present and (for enrollment) the join matched ≥1 section**; otherwise it keeps its honest inert reason. Add a per-course prereq-conversion summary (exact vs fallback, labeled *conservative-permissive*). Label the enrollment activation *fixture-scoped (live↔IR join not validated on real data)* and the eLumen path *fixture-only*. Keep `engine.py` untouched. Add `--enrollment` / `--elumen-fixture` CLI args.

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
- [ ] **Step 3 (manual, live — EXPECT NO ENROLLMENT MATCH):** `python3 build_live_workbook.py --campus LAMC --program "Biology" --terms 2264,2266,2268 --enrollment files/lamc_sample_enrollment.xlsx --elumen-fixture tests/fixtures/elumen_prereqs_LAMC.json --out data/live_LAMC.xlsx`. **Do NOT expect the enrollment detectors to activate**: the IR fixture is terms {2248,2252} with CRNs 20001-20116, the live terms are {2264,2266,2268} with different CRNs, so the `(term, CRN)` join matches ZERO sections and `modality_mismatch`/`under_supply` stay INERT (`Cap/Tot/Wait = 0`) — this is the documented fixture-only limitation, not a bug. Confirm: the report shows the eLumen *fixture-only* prereq map threaded (`prerequisite_ordering` active), and the enrollment detectors honestly INERT with a "join matched 0 sections (live↔IR fixtures disjoint)" reason. The end-to-end enrollment activation is exercised only by the OFFLINE `test_detector_activation.py` (inline-key map / enrollment-fixture-own-terms). (Live schedule/PM fetch still needs network; the enrichment seams themselves are offline.)

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

**3. No-regression:** Task 4 keeps `build_sections_df`/`build_catalog_df`/`write_workbook` defaults byte-identical (the `write_workbook(*, prereqs=None)` kwarg is added in Task 4, owned by `mapping.py`, so Task 5 only passes it — no cross-task file-ownership violation), so the existing `test_mapping.py`, `test_live_roundtrip.py`, `test_live_offline_pipeline.py`, and `test_sample_enrollment_fixture.py` stay green. `engine.py` and the column constants are untouched.

**4. Placeholder scan:** RESOLVED — Task 1's `test_fallback_is_under_approx_keeps_engine_feasible` now ships a concrete UNIT-CAP scenario (H=2, max_units=6, all of {X,A,B,D} every season, X's prereq `(A AND B) OR D`); the prior `...` sketch and the wrong "make seasons binding" guidance are removed (a season bind is vacuous — see Verified facts). No TODOs in production code.

**5. Determinism:** No CP-SAT parameter change. DNF→CNF output sorts clauses by their **sorted-literal list key** (not the rendered string — the two diverge on space-vs-`)` ordering) for byte-stable output. The IR fixture is byte-deterministic (`test_sample_enrollment_fixture.py` pins it).

**6. Adversarial-findings reconciliation (this revision):** Applied — delimiter/paren-literal → flagged fallback (§4.1); `gated_course` normalized before self-ref drop (§4.2-0); fallback union over the CLEANED dnf (§4.3); clause sort key pinned to the list, not the string (§4.2-7); guard documented as pre-minimization/conservative (§4.2-5); NaN-top-level→`''` vs malformed→raise disambiguated (§4.1); empty-clause rationale corrected (no-op, not FALSE — §4.2-3); `_norm` inlined (no pandas import — §2); enrollment join needs CRN-suffix-strip + pure-return + idempotent + blank-skip (§6/§9); `(Term, CLASS)` degraded fallback marked plan-only/inert-cross-source (§9); `write_workbook(*, prereqs=None)` ownership fixed under Task 4 (§5); enrollment join labeled FIXTURE-ONLY / not validated live (§6/§7/§11.3, Task 5/7); line-number drift 64-66→63-65 corrected; 3×3 product comment corrected to 27.
