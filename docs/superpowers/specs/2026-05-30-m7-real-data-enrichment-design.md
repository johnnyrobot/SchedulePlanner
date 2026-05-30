# Design: m7 real-data enrichment — eLumen prereqs + IR enrollment (mostly fixture-only)

**Date:** 2026-05-30
**Status:** Approved (design); pending implementation plan
**Branch:** `feat/m7-real-data`
**Related:** PRD.md (M4 enrollment, eLumen), engine.py, `docs/superpowers/specs/2026-05-28-live-sources-to-workbook-design.md`

---

## 1. Goal

Close the two known live-data gaps the prototype documented as INERT, **additively and
without weakening the engine**:

1. **Prerequisite ordering** — fill `catalog['Prerequisites (structured)']` (hard-coded
   `''` at `mapping.py:86`) so the solver's CNF ordering constraints fire. eLumen
   prerequisite logic may arrive as **DNF** (OR of AND-groups); the engine consumes
   **CNF** (AND of OR-groups). We convert DNF→CNF by exact distribution with a
   **configurable expansion guard** and a **flagged, never-silent conservative fallback**.
2. **Enrollment counts** — fill `sections['Cap Enrl' / 'Tot Enrl' / 'Wait Tot']`
   (hard-coded `0` at `mapping.py:64-66`) so `modality_mismatch` (fill < 0.55) and
   `under_supply` (Wait Tot > 15) stop being inert.

Most of this ships **now against committed fixtures**. The only part that cannot is the
**live eLumen HTTP client**: no eLumen endpoint, auth, or response fixture is known, so
that one slice is **plan-only** and any code touching it is labeled
*fixture-only / not-validated-on-real-data* in code comments and the report.

## 2. Boundary (non-negotiable — carried from the 2026-05-28 design)

Network IO lives **entirely outside** `engine.run()`. `engine.py` is **UNTOUCHED**
(`random_seed=42`, `num_search_workers=1`, `REQUIRED_COLUMNS` byte-identical). The flow
stays:

```
fetch (live HTTP, in build_live_workbook) → enrich → map → write .xlsx → engine.run(path)
```

`mapping.py` remains **pure** (no network, no I/O beyond the workbook write). DNF→CNF
conversion is a new **pure** module (`sources/prereq_cnf.py`): no network, no pandas in
its core function. All HTTP goes through the existing `sources/http.py` `get_json(...,
client=...)` injectable-client seam so tests never open a socket.

## 3. What the engine actually consumes (ground truth from engine.py)

The seams are exactly where the prototype left literals:

| Sheet | Column the enrichment fills | Today's literal | Engine reader |
|---|---|---|---|
| sections | `Cap Enrl`, `Tot Enrl`, `Wait Tot` | `0` (`mapping.py:64-66`) | `analyze` fill ratio (`engine.py:138-139`), waitlist (`engine.py:143-144`) |
| catalog | `Prerequisites (structured)` | `''` (`mapping.py:86`) | `parse_prereq` (`engine.py:89-100`) → `solve_cohort` ordering (`engine.py:168-175`) |

Engine invariants the enrichment must respect:

- **CNF grammar.** `parse_prereq("(A OR B) AND (C)") → [["A","B"],["C"]]`: outer split on
  `" AND "`, inner on `" OR "`, strip `()`. Blank/NaN → `[]` (no constraint). The string
  we emit must round-trip through this exact parser.
- **Closure pulls every literal in.** `closure` (`engine.py:113-121`) walks every literal
  of every OR-group into the scheduled set, so any course named in a prereq is scheduled
  regardless of clause strength.
- **`solve_cohort` filters to in-program courses.** `grp = [p for p in grp if p in
  courses]` (`engine.py:170`) — literals must be `_norm`-canonical or they silently drop.
- **Column constants are duplicated.** `mapping.SECTION/CATALOG/PROGRAM_COLUMNS`
  (`mapping.py:20-22`) must stay byte-identical to `engine.REQUIRED_COLUMNS`
  (`engine.py:22-26`). The enrichment adds **no columns** — it only fills existing ones.

## 4. The DNF→CNF conversion (the load-bearing decision)

eLumen prereq logic is naturally **DNF**: alternative pathways OR'd, each pathway a set of
co-required courses AND'd. Example: *"(MATH 245 and MATH 246) or (MATH 260)"* =
`[["MATH 245","MATH 246"], ["MATH 260"]]`. The engine wants **CNF**. We distribute exactly.

### 4.1 New pure module `sources/prereq_cnf.py`

Public API:

```python
DEFAULT_MAX_CLAUSES = 64   # module constant; tunable without code change
BRANCH_CAP = 12            # secondary guard on #OR-branches

@dataclass(frozen=True)
class ConversionResult:
    cnf_string: str          # engine-ready, e.g. "(A OR D) AND (B OR D)"; "" = no prereq
    cnf_groups: list[list[str]]
    exact: bool              # True = exact distribution; False = flagged fallback
    fallback_reason: str | None
    clause_count: int
    clause_budget: int

def dnf_to_cnf(dnf, *, gated_course=None, max_clauses=DEFAULT_MAX_CLAUSES) -> ConversionResult
def to_catalog_string(cnf_groups) -> str
```

**Input contract.** `dnf = list[list[str]]`, OUTER OR'd, INNER AND'd (the **mirror** of
engine CNF). On a non-list / wrong-nesting payload, raise `SourceDataError` naming the
source (reuse the `sources/http.py` class). The nesting is pinned by unit tests against a
committed fixture so a future drift is caught loudly.

### 4.2 Algorithm (correctness-first spine)

1. **Normalize** every literal with `mapping._norm` (upper + collapse whitespace) so join
   keys match catalog `Course ID`. Dedup within each AND-term.
2. **Self-reference drop.** Drop a literal equal to `gated_course` (a course can't be its
   own prereq). If that empties an AND-term, drop that branch; if *all* branches die, emit
   `''` with `exact=False, fallback_reason="self_referential_prereq_dropped"` (never
   manufacture infeasibility).
3. **Truth-value short-circuits.** `dnf == []` / blank / NaN → `('', exact=True)`. An
   **empty AND-term inside a non-trivial DNF** means "empty AND = TRUE" ⇒ the whole
   disjunction is a tautology ⇒ `''` (no prereq), `exact=True`. It must **not** become an
   empty OR-clause (which would be FALSE — the opposite truth value).
4. **Absorption pre-pass.** Drop a branch that is a strict superset of another
   (`(A) OR (A AND B) == (A)`); drop duplicate branches. Equivalence-preserving; shrinks
   the product.
5. **Perf guard (checked BEFORE materializing).** `predicted = ∏ len(branch)` — an
   O(#branches) integer multiply. If `predicted > max_clauses` OR `#branches > BRANCH_CAP`
   ⇒ take the fallback path (§4.3). The Cartesian product is **never allocated** on reject.
6. **Distribute (exact, only within guard).** `itertools.product(*and_terms)`; each tuple
   → one OR-clause; dedup literals within a clause.
7. **Minimize (all equivalence-preserving).** Drop duplicate clauses; **clause subsumption**
   (drop clause `Q` if some clause `P ⊆ Q` — the smaller clause is stronger); sort literals
   within clauses and sort clauses lexicographically for byte-stable output.
8. **Serialize.** `to_catalog_string`: each clause → `"(" + " OR ".join(sorted) + ")"`,
   joined by `" AND "`. **Always parenthesize single clauses** (`"(A)"`, not bare `"A"`) to
   force `parse_prereq`'s structured branch. `cnf == []` → `""`.

**Round-trip invariant (asserted in tests):**
`engine.parse_prereq(result.cnf_string)` equals the intended CNF (modulo literal/clause
sort). Verified cases: `'' → []`; `'(A)' → [['A']]`; `'(A OR B) AND (C)' → [['A','B'],['C']]`;
`'(A OR B OR C)' → [['A','B','C']]`.

### 4.3 Fallback: FLAGGED CONSERVATIVE UNDER-APPROXIMATION (never silent, never over-approx)

When the guard is exceeded, do **not** build the product and do **not** truncate clauses.
Emit a **single OR-clause** that is the union of every distinct normalized literal anywhere
in the DNF: `cnf = [sorted(set(all literals))]` → `"(L1 OR L2 OR ... OR Ln)"`. Engine
semantics: *"at least one of the mentioned courses must be taken strictly before this one."*

**Why under-approximate is the safe direction** (validated against the real engine, not
asserted):

- Every assignment satisfying the true DNF makes ≥1 literal true, so it satisfies the union
  clause: the fallback's feasible region is a **strict superset** of the true one — it
  relaxes, never tightens.
- Empirically running `engine.solve_cohort`: for a true prereq `(A AND B) OR D` under binding
  season+unit constraints, the **exact** CNF is FEASIBLE, but the naive **over-approximation**
  `(A) AND (B) AND (D)` returns `None` = a **false "no plan exists"**, and where `engine.run`'s
  `allow_fixes` retry (`engine.py:233-237`) rescues it, it **fabricates spurious off-season
  fixes** the true prereq never required. The under-approx union clause stayed FEASIBLE in
  every scenario.
- For a feasibility/advising tool, a false "infeasible" or an invented "fix" (over-approx) is
  the **catastrophic, headline-flipping** error. A missed ordering constraint (under-approx)
  is the **same soft, visible loss the engine already ships today** when the column is blank
  (`mapping.py:86` = the maximal under-approximation). The fallback degrades continuously
  toward that already-shipped baseline.
- **Closure-invariant:** `closure` (`engine.py:113-121`) pulls every literal of every OR-group
  into the scheduled set; the union clause retains all literals, so the set of courses
  scheduled is identical to the exact case — nothing silently drops from a student's plan;
  only strictly-before ordering strength relaxes.
- O(n) literals ⇒ exactly one OR-group ⇒ zero blow-up, always encodable.

**Never silent.** `dnf_to_cnf` returns the structured `ConversionResult`. On the fallback
path `exact=False` and `fallback_reason` carries
`"clause_budget_exceeded: product N > budget B; emitted conservative single-OR union over K
literals (UNDER-approximate: ordering relaxed, all courses retained via closure)"`. The flag
travels **out-of-band** (not embedded in the catalog string, so the serialized CNF stays
valid grammar). `build_live_workbook` collects per-course results, distinguishes
exact-CNF from fallback courses, and labels fallback courses *"prereq approximated
(conservative-permissive), not exact."*

### 4.4 Guard defaults

`DEFAULT_MAX_CLAUSES = 64`, a keyword arg forwarded through the eLumen wiring and exposed as a
module constant so a real-data run is tunable **without a code change** (the m7 "configurable"
requirement). Real CC prerequisites are tiny (1–3 pathways × 1–2 courses), so honest exact
CNFs are almost always ≤ 18 clauses; 64 leaves generous headroom while hard-capping the added
CP-SAT BoolVar/constraint count per course at a small constant so `engine.run` stays fast and
deterministic. A wrong default only mis-sets the exact/fallback **boundary**, never
correctness. `BRANCH_CAP = 12` bounds integer-scale products even when individual branches
have size 1.

## 5. eLumen → catalog prereq mapping (fixture-only)

A new pure mapping `sources/elumen.py` exposes:

- `parse_elumen_dnf(raw)` — turn one eLumen prerequisite record into a normalized DNF
  `list[list[str]]`. The **expected real eLumen shape is DOCUMENTED in a module docstring**
  but **assumed**, not validated: there is no eLumen fixture yet.
- A committed fixture `tests/fixtures/elumen_prereqs_LAMC.json` modeling the documented shape
  (course → DNF), used to unit-test parse + DNF→CNF end-to-end with **no network**.
- `build_prereq_map(elumen_records, *, max_clauses=...) -> (prereqs: dict[str,str], results:
  dict[str, ConversionResult])` — normalize course ids, run `dnf_to_cnf` per course, return
  the catalog-string map plus the per-course conversion records for the report.

**Wiring (additive, default preserves today):** `mapping.build_catalog_df(section_records,
program, prereqs=None)` emits `(prereqs or {}).get(cid, '')` instead of `''`. `prereqs=None`
keeps every existing test byte-identical (`test_mapping.py:44` still all-blank). `mapping.py`
stays pure: the DNF→CNF conversion happens **upstream** (in `build_live_workbook` /
`sources/elumen.py`), and only the finished CNF-string map is threaded in.

## 6. IR enrollment ingest (fixture-driven, real-shape)

The committed fixture `files/lamc_sample_enrollment.xlsx` already mirrors the real IR
PeopleSoft export (populated `Cap Enrl/Tot Enrl/Wait Tot`, **no instructor PII**, terms
`{2248, 2252}`; pinned by `tests/test_sample_enrollment_fixture.py`). A new pure
`sources/enrollment.py` reads that **IR-shaped** workbook into a join map and threads counts
onto section records.

- `load_enrollment(path) -> dict[(term:int, class_nbr:str) -> {Cap, Tot, Wait}]` reads the
  IR `sections` sheet (columns `Term`, `Class Nbr`, `Cap Enrl`, `Tot Enrl`, `Wait Tot`; the
  full documented schema in §9). It is **pure** (file read only, no network) and raises
  `SourceDataError` naming the file on missing columns.
- `enrich_sections(section_records, enrollment) -> section_records` joins on
  `(term, class_nbr)` — both keys are already present on `schedule.fetch_sections` records
  (`schedule.py:77,83`) — and writes `Cap Enrl / Tot Enrl / Wait Tot` onto matched records;
  unmatched (live-only) records keep `0`.
- **The additive `mapping.py` seam** (`build_sections_df`): replace the three literal `0`s
  (`mapping.py:64-66`) with `r.get('Cap Enrl', 0) / r.get('Tot Enrl', 0) / r.get('Wait Tot',
  0)`. Records enriched by the join carry real counts; live-only records stay `0`. No column
  or schema change; existing tests stay byte-identical (their records carry no enrollment
  keys → default 0).

**Join-seam risk (§ from survey):** `build_sections_df` does not carry `class_nbr` into the
DataFrame today, so the join must run on the **raw section-record dicts** before
`build_sections_df`. `enrich_sections` operates on the records list, preserving that ordering.

## 7. Live-detector activation (honest, fixture-tested)

`build_live_workbook.INERT_DETECTORS` (`build_live_workbook.py:56-75`) names the three gaps as
machine-readable report fields. The enrichment flips each entry from inert to **active** only
when the corresponding data is present:

- `modality_mismatch` + `under_supply` → active when an enrollment export was joined.
- `prerequisite_ordering` → active when a prereq map was threaded in; per-course it
  distinguishes exact-CNF courses from budget-fallback courses (the latter labeled
  *conservative-permissive, not exact*).

When data is **absent**, the entry stays inert with its existing honest reason. The eLumen
slice (no real fixture) is additionally labeled *fixture-only / not-validated-on-real-data* in
the report and code comments. **No detector is silently flipped without its data.**

## 8. File layout (additive — engine.py / app.py untouched)

```
edgesched/
  sources/
    prereq_cnf.py        # NEW pure DNF->CNF: dnf_to_cnf + to_catalog_string + ConversionResult
    elumen.py            # NEW fixture-only eLumen DNF parser + build_prereq_map (DOCUMENTED real shape)
    enrollment.py        # NEW pure IR-workbook reader + (term,class_nbr) join onto section records
    mapping.py           # MODIFIED: build_sections_df reads r.get('Cap Enrl'...); build_catalog_df(prereqs=None)
  build_live_workbook.py # MODIFIED: optional --enrollment / --elumen-fixture; flip INERT_DETECTORS
  tests/
    fixtures/
      elumen_prereqs_LAMC.json   # NEW committed eLumen-shaped DNF fixture (fixture-only slice)
    test_prereq_cnf.py           # NEW exhaustive: distribution, guard, fallback direction, round-trip
    test_elumen_prereq_mapping.py# NEW eLumen fixture -> CNF catalog strings
    test_enrollment_ingest.py    # NEW IR workbook -> Cap/Tot/Wait join (uses files/lamc_sample_enrollment.xlsx)
    test_detector_activation.py  # NEW enriched workbook fires modality_mismatch/under_supply/prereq ordering
```

The **live eLumen HTTP client** (`sources/elumen_client.py`) is **plan-only** (§10): no
endpoint/fixture exists, so it is documented but not built in this run.

## 9. Documented expected real IR PeopleSoft export schema

The real IR export is an `.xlsx` whose `sections` sheet mirrors `files/lamc_sample_enrollment.
xlsx`. The columns the ingest **requires** (and the engine reads) are bolded; the rest are
carried-but-ignored context, documented so a real export is recognizable. **Instructor PII
columns must be absent** (enforced by `tests/test_sample_enrollment_fixture.py`).

| Column | Type | Used by ingest? | Notes |
|---|---|---|---|
| **`Term`** | int | yes (join key) | PeopleSoft term code, e.g. `2248` (Fall 2024), `2252` (Spring 2025) |
| `Descr` | str | no | e.g. "2024 Fall" |
| `Campus` | str | no | e.g. "LAMC" |
| **`Class Nbr`** | int | yes (join key) | PeopleSoft CRN; joins to `schedule.fetch_sections` `class_nbr` |
| `Subject`, `Catalog`, `Section` | str | no | section identity context |
| `Session`, `Class Type`, `Component` | str | no | |
| `Class Status` | str | no | "Active"; cancelled sections are filtered upstream |
| `Mode`, `IN_PERSON` | str | no | modality context |
| **`Cap Enrl`** | int | yes | section seat capacity → `analyze` fill denominator |
| **`Tot Enrl`** | int | yes | enrolled count → `analyze` fill numerator |
| `Wait Cap` | int | no | waitlist capacity |
| **`Wait Tot`** | int | yes | waitlisted count → `under_supply` (sum > 15) |
| `FILLD`, `.FILLPERCNT`, `ENRL`, `LMT` | num | no | precomputed fill stats (we recompute) |
| `Acad Org`, `Dep`, `Discipline` | str | no | |
| `IGETC`, `OER`, `FTE`, `Class Workload Hrs`, `LEVEL` | mixed | no | |
| **`CLASS`** | str | optional | `"SUBJ CAT"` form; fallback join/identity if `Class Nbr` absent |
| `SEC`, `Class Start Date`, `Class End Date` | str | no | |

**Join contract:** `(int(Term), str(Class Nbr))`. Real exports may type `Class Nbr` as int or
str; `enrich_sections` normalizes both join sides to `str`. If `Class Nbr` is unavailable in a
real export, a documented degraded fallback joins on `(Term, CLASS)` aggregated per
course-term (less precise; flagged in the report).

## 10. eLumen client / fixture plan (plan-only — no real data invented)

No eLumen REST endpoint, auth, or captured response is known. Therefore:

- **Now (fixture-only):** `sources/elumen.py` parses a **committed**
  `tests/fixtures/elumen_prereqs_LAMC.json` whose shape we DEFINE and DOCUMENT (course id →
  DNF list-of-lists, plus a `raw` text field for provenance). Every test routes through this
  fixture; nothing touches a socket. All code carries a *fixture-only / not-validated-on-real-
  data* comment.
- **Later (plan-only `sources/elumen_client.py`):** when a real eLumen endpoint + auth +
  captured response exist, build an HTTP client mirroring `program_mapper.py`
  (`COLLEGE_CONFIGS`-style per-campus config, `_headers` spoofing, `get_json(..., client=...)`
  with a human `source=` label, reuse `SourceError/SourceHTTPError/SourceDataError`). Add the
  captured response to `tests/fixtures/` and a route to `conftest.lamc_routes` (longest-match
  routing), then an offline client test + an `@pytest.mark.live` test. This client is **not
  built in this run** because inventing an endpoint/auth would violate the m7 no-fabrication
  constraint.

## 11. Honest handling of the gaps (no overclaiming)

1. **eLumen is fixture-only.** The DNF parser and the whole eLumen→prereq slice are validated
   only against our self-defined fixture; the report and code comments state
   *not-validated-on-real-data*. We do not claim the parser matches the real eLumen payload.
2. **DNF→CNF fallback is visible.** Budget-exceeded courses are flagged
   *conservative-permissive (not exact)* in the report; they are never silently mis-encoded.
3. **Enrollment ingest is real-shape but fixture-sourced.** It reads the real IR PeopleSoft
   layout, but the data is the synthetic `lamc_sample_enrollment.xlsx`. The report names the
   source file; detectors are active only when an export was actually joined.
4. **Detectors stay inert without their data.** Absent enrollment ⇒ `Cap/Tot/Wait = 0` ⇒
   `modality_mismatch`/`under_supply` empty by construction; absent prereqs ⇒ blank column ⇒
   no ordering constraints. Both are surfaced as structured `INERT_DETECTORS` reasons, exactly
   as today.

## 12. Out of scope (YAGNI)

- A real eLumen HTTP client / live fetch (plan-only, §10).
- A real-network IR enrollment fetch (IR exports are file drops, not an API).
- Assist.org articulation.
- Any change to `engine.py`, the CP-SAT parameters, or the workbook column set.
- Caching, multi-campus batch runs, or wiring live fetches into `engine.run`.

## 13. Known limitations (carried forward, not bugs)

- The eLumen DNF shape is our **assumed** model; first contact with real eLumen data may
  require adjusting `parse_elumen_dnf` (isolated to one fixture-only module).
- DNF→CNF over a pathological prereq falls back to a permissive union clause; ordering for
  that one course relaxes (flagged), it is not exact.
- The IR join needs `class_nbr`; if a real export omits it, the degraded `(Term, CLASS)` join
  is coarser and is flagged.
