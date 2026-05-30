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
   (hard-coded `0` at `mapping.py:63-65`) so `modality_mismatch` (fill < 0.55) and
   `under_supply` (Wait Tot > 15) stop being inert.

> **JOIN-VALIDATION CAVEAT (read first).** The live-schedule ↔ IR-enrollment join is
> **fixture-only and NOT validated end-to-end against the real schedule source.** Two
> structural facts in the committed fixtures (verified empirically) make a real join produce
> **zero matches**: (a) `schedule.fetch_sections` emits `class_nbr` as a *decorated* string
> like `'17818 (LEC)'` / `'17819 (LAB)'` (all 81 records in `schedule_listing_LAMC_2268.json`
> carry the ` (LEC)`/` (LAB)` suffix) while the IR `Class Nbr` is a bare int (`20001`); plain
> `str()` can never reconcile them — a CRN-extraction step is REQUIRED (§6/§9). (b) The
> committed schedule fixture is term `2268`; the committed enrollment fixture is terms
> `{2248, 2252}` — **zero term overlap**, and even after CRN extraction the two CRN sets do not
> intersect. The enrollment detectors are therefore validated **only within one self-consistent
> set of records** (the enrollment fixture's own terms, or a hand-keyed inline map matching the
> term-2268 schedule CRNs), never across the live schedule↔IR boundary. §7/§11.3 and the plan's
> Task 7 smoke command are corrected to NOT claim live detector activation.

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
conversion is a new **pure** module (`sources/prereq_cnf.py`): no network, **no pandas in the
core algorithm**. Note: `prereq_cnf.py` does NOT import `mapping._norm` — `from .mapping import
_norm` would transitively pull `pandas` (and inertly `httpx`) into the module at import time
(verified: importing `sources.mapping` puts both in `sys.modules`). Instead `prereq_cnf.py`
inlines a byte-identical 1-line normalizer (`re.sub(r"\s+", " ", str(x).strip().upper())`) so it
imports neither pandas nor mapping; a code comment pins it as "must stay byte-identical to
`mapping._norm` so join keys match catalog `Course ID`." All HTTP goes through the existing
`sources/http.py` `get_json(..., client=...)` injectable-client seam so tests never open a
socket.

## 3. What the engine actually consumes (ground truth from engine.py)

The seams are exactly where the prototype left literals:

| Sheet | Column the enrichment fills | Today's literal | Engine reader |
|---|---|---|---|
| sections | `Cap Enrl`, `Tot Enrl`, `Wait Tot` | `0` (`mapping.py:63-65`) | `analyze` fill ratio (`engine.py:138-139`), waitlist (`engine.py:143-144`) |
| catalog | `Prerequisites (structured)` | `''` (`mapping.py:86`) | `parse_prereq` (`engine.py:89-100`) → `solve_cohort` ordering (`engine.py:168-175`) |

Engine invariants the enrichment must respect:

- **CNF grammar.** `parse_prereq("(A OR B) AND (C)") → [["A","B"],["C"]]`: outer split on
  `" AND "`, inner on `" OR "`, strip `()`. Blank/NaN → `[]` (no constraint). The string
  we emit must round-trip through this exact parser.
- **Closure pulls every literal in.** `closure` (`engine.py:113-121`) walks every literal
  of every OR-group into the scheduled set, so any course named in a prereq is scheduled
  regardless of clause strength.
- **`solve_cohort` filters to in-program courses.** `grp = [p for p in grp if p in
  courses]` (`engine.py:170`) — literals must be `_norm`-canonical or they silently drop. An
  OR-group that filters to empty is `continue`-skipped (`engine.py:171-172`): an empty/unknown
  OR-group is a **no-op (no constraint)**, NOT made FALSE/UNSAT. (This corrects the §4.2-step-3
  rationale below.)
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
engine CNF).

- **Top-level "no prereq" sentinels.** `None`, `NaN` (a bare `float('nan')`), `''`, and `[]` at
  the **top-level `dnf` slot** all map to `('', exact=True)` (no prereq). eLumen records with a
  null/NaN `dnf` field hit this path and must NOT raise.
- **Malformed payloads raise.** A **non-empty** payload that is not a list-of-lists raises
  `SourceDataError` naming the source (reuse the `sources/http.py` class): a bare non-empty
  string (e.g. `dnf='MATH 245'`), or mixed/wrong nesting (e.g. `dnf=[['A'], 'B']` — a list whose
  members are not all lists). This resolves the apparent §4.1↔§4.2 conflict: the NaN/None/blank
  sentinel check happens FIRST (top-level only); only a non-empty wrongly-shaped payload reaches
  the raise. Pinned by tests: `dnf=float('nan')` → `('', exact=True)`; `dnf='MATH 245'` →
  `SourceDataError`; `dnf=[['A'], 'B']` → `SourceDataError`.

The nesting is pinned by unit tests against a committed fixture so a future drift is caught
loudly.

**Delimiter / parenthesis safety (round-trip integrity).** A single literal whose normalized
form contains a serializer-delimiter or paren substring — `' OR '`, `' AND '`, `'('`, or `')'`
— would CORRUPT the round-trip through `engine.parse_prereq` (verified: the literal
`'BIO 3 OR 4'` serializes to `'(BIO 3 OR 4)'` and parses back to `[['BIO 3','4']]` — inventing
a phantom literal `'4'` and losing the real one; `'MATH 125 (FORMERLY 120)'` parses to
`[['MATH 125 (FORMERLY 120']]` — a stray-paren mangle; and the corrupted literal then silently
drops in `solve_cohort`'s `grp=[p for p in grp if p in courses]` filter, vanishing an ordering
constraint with **no flag**). Real PM/eLumen data contains parens (confirmed `'('` present in
`pm_program_map_LAMC.json`). Therefore, after normalization, **detect any delimiter/paren-bearing
literal and route the WHOLE course to the flagged conservative fallback** (`exact=False`,
`fallback_reason="unserializable_literal: <lit>"`) — never emit a corrupting string. The
fallback union itself is also filtered: only safely-serializable literals enter it, and if the
unserializable literal would be the *only* content, emit `''` with the same flagged reason
rather than a string that mis-parses. Pinned by tests: `dnf_to_cnf([['BIO 3 OR 4']])` and
`dnf_to_cnf([['MATH 125 (FORMERLY 120)']])` must NOT produce a string that mis-parses —
`engine.parse_prereq(result.cnf_string)` yields exactly the intended literal set or the course
is flagged (`exact=False`).

### 4.2 Algorithm (correctness-first spine)

0. **Normalize `gated_course` FIRST.** `gated_course = _norm(gated_course)` (when given)
   BEFORE any comparison, so it is byte-identical to the normalized literals in step 1. A raw
   caller value (e.g. `'math 245'`) must compare equal to a normalized literal (`'MATH 245'`),
   otherwise the self-reference drop SILENTLY FAILS — and a course that is its own prereq makes
   `solve_cohort` return `None` (false-INFEASIBLE, verified: `prereqs={'C':[['C']]}` →
   INFEASIBLE). `build_prereq_map` passes eLumen `course_id` (whose casing/whitespace is
   fixture-only and unvalidated) as `gated_course`, so the mismatch is reachable.
   Pinned by test: `dnf_to_cnf([['math 245'],['C']], gated_course='c')` drops the self-ref AND
   merges spellings, and `solve_cohort` stays feasible.
1. **Normalize** every literal with the inlined `_norm` (upper + collapse whitespace; byte-
   identical to `mapping._norm`) so join keys match catalog `Course ID`. Dedup within each
   AND-term. **Delimiter/paren check** (§4.1): a literal whose normalized form contains
   `' OR '`/`' AND '`/`'('`/`')'` routes the whole course to the flagged fallback
   (`unserializable_literal`).
2. **Self-reference drop.** Drop a literal equal to the normalized `gated_course` (a course
   can't be its own prereq). If that empties an AND-term, drop that branch; if *all* branches
   die, emit `''` with `exact=False, fallback_reason="self_referential_prereq_dropped"` (never
   manufacture infeasibility).
3. **Truth-value short-circuits.** `dnf == []` / blank / `None` / `NaN` (top-level slot) →
   `('', exact=True)` (§4.1). An **empty AND-term inside a non-trivial DNF** means "empty AND =
   TRUE" ⇒ the whole disjunction is a tautology ⇒ `''` (no prereq), `exact=True`. It must **not**
   become an empty OR-clause. *(Rationale corrected: it is NOT that an empty OR-clause "would be
   FALSE" in the engine — `parse_prereq('()')` → `[['']]` and `solve_cohort` filters/`continue`-
   skips an unknown OR-group (`engine.py:170-172`), so even an accidental empty clause **no-ops**
   to "no constraint", never infeasibility. We still emit `''` for the tautology because that is
   the truth-correct encoding; the safety margin is that a mis-encode here degrades to a relaxed
   constraint, not a catastrophic false-INFEASIBLE.)*
4. **Absorption pre-pass.** Drop a branch that is a strict superset of another
   (`(A) OR (A AND B) == (A)`); drop duplicate branches. Equivalence-preserving; shrinks
   the product.
5. **Perf guard (checked BEFORE materializing).** `predicted = ∏ len(branch)` — an
   O(#branches) integer multiply, computed AFTER the step-4 absorption pre-pass but BEFORE the
   `itertools.product` distribution and step-7 minimization. If `predicted > max_clauses` OR
   `#branches > BRANCH_CAP` ⇒ take the fallback path (§4.3). The Cartesian product is **never
   allocated** on reject. **The guard is intentionally conservative / pre-minimization:** clause
   subsumption (step 7) can shrink the materialized product dramatically (e.g. `(A AND B) OR
   (A AND C)` has product 4 but minimizes to 2 clauses), so an honest CNF whose *final* clause
   count is small may still fall back if its *pre-minimization* product exceeds the budget. This
   only mis-sets the exact/fallback boundary (relaxing an ordering constraint that did not need
   relaxing), never correctness; `DEFAULT_MAX_CLAUSES = 64` leaves generous headroom. A test pins
   a case where pre-minimization product > budget but minimized clauses ≤ budget, documenting the
   chosen (fall-back-early) behavior so it is not a surprise.
6. **Distribute (exact, only within guard).** `itertools.product(*and_terms)`; each tuple
   → one OR-clause; dedup literals within a clause.
7. **Minimize (all equivalence-preserving).** Drop duplicate clauses; **clause subsumption**
   (drop clause `Q` if some clause `P ⊆ Q` — the smaller clause is stronger); sort literals
   within each clause; then sort the clauses by their **sorted-literal list key** (a
   `tuple`/`list` comparison on the literals, NOT a comparison of the rendered `"(...)"`
   strings). *(The two diverge: groups `[['A','B'],['A','B','C']]` sort as lists to
   `[['A','B'],['A','B','C']]` but as rendered strings to `['(A OR B OR C)','(A OR B)']`,
   because space `0x20` < `')'` `0x29` flips the order. Pinning the list-key removes the
   ambiguity and locks byte-stability.)* A test asserts the exact canonical string for a
   list-vs-string-divergent case (e.g. forced-exact `[['A','B'],['A','B','C']]`).
8. **Serialize.** `to_catalog_string`: for each clause **in the step-7 list-key order**,
   `"(" + " OR ".join(sorted_literals) + ")"`, joined by `" AND "`. **Always parenthesize
   single clauses** (`"(A)"`, not bare `"A"`) — defensive: guarantees `parse_prereq`'s
   structured branch regardless of whether an `llm` is later wired into `parse_prereq`. *(Today
   the call path never reaches the unstructured-delegation branch for these strings:
   `parse_prereq('MATH 245')` already returns `[['MATH 245']]` when `llm=None`, and
   `build_live_workbook` calls `engine.run()` with no `llm` — so the parenthesization is
   belt-and-suspenders, not a fix for a current misparse.)* `cnf == []` → `""`.

**Round-trip invariant (asserted in tests):**
`engine.parse_prereq(result.cnf_string)` equals the intended CNF (modulo literal/clause
sort). Verified cases: `'' → []`; `'(A)' → [['A']]`; `'(A OR B) AND (C)' → [['A','B'],['C']]`;
`'(A OR B OR C)' → [['A','B','C']]`; common-literal factoring
`[['A','X'],['B','X']] → '(A OR B) AND (X)' → [['A','B'],['X']]` (the canonical shared-coreq
eLumen pattern, verified to round-trip). Plus the delimiter/paren cases (§4.1): a
delimiter-bearing literal does NOT yield a string that mis-parses (the course is flagged
instead).

### 4.3 Fallback: FLAGGED CONSERVATIVE UNDER-APPROXIMATION (never silent, never over-approx)

When the guard is exceeded, do **not** build the product and do **not** truncate clauses.
Emit a **single OR-clause** that is the union of every distinct normalized literal in the
**CLEANED DNF** — i.e. AFTER the step-2 self-reference drop and the step-4 absorption pre-pass,
and EXCLUDING any delimiter/paren-bearing literal: `cnf = [sorted(set(safe literals in cleaned
dnf))]` → `"(L1 OR L2 OR ... OR Ln)"`. Engine semantics: *"at least one of the mentioned
courses must be taken strictly before this one."*

> **The union scope is the CLEANED dnf, never the original.** If the implementer re-scanned the
> ORIGINAL `dnf`, `gated_course` would leak back into the union OR-clause specifically on the
> fallback path — re-creating a self-prereq (the catastrophic false-INFEASIBLE direction) on the
> path that is supposed to be the *safe* one. Pinned by test: a large self-referential DNF that
> exceeds the guard must NOT contain `gated_course` in the union clause
> (`assert gated_course not in engine.parse_prereq(result.cnf_string)[0]`).

**Why under-approximate is the safe direction** (validated against the real engine, not
asserted):

- Every assignment satisfying the true DNF makes ≥1 literal true, so it satisfies the union
  clause: the fallback's feasible region is a **strict superset** of the true one — it
  relaxes, never tightens.
- Empirically running `engine.solve_cohort`: for a true prereq `(A AND B) OR D` under a binding
  **unit-cap** (capacity + ordering), the **exact** CNF `[['A','D'],['B','D']]` is FEASIBLE, but
  the naive **over-approximation** `(A) AND (B) AND (D)` returns `None` = a **false "no plan
  exists"**, and where `engine.run`'s `allow_fixes` retry rescues it, it **fabricates spurious
  off-season fixes** the true prereq never required. The under-approx union clause `(A OR B OR D)`
  stayed FEASIBLE. **Reproduced with: `horizon=2, max_units=6`, all of `{X,A,B,D}` offered every
  season, `X`'s true prereq `(A AND B) OR D`** → exact FEASIBLE, over-approx INFEASIBLE, union
  FEASIBLE. *(NOTE: this is a UNIT-CAP bind, NOT a season bind. A season bind does not reproduce
  it — `closure` (`engine.py:113-121`) pulls every literal of every OR-group into the scheduled
  set, so exact/over/under all schedule `{A,B,D}`; making a literal season-unavailable kills the
  exact and over cases together, yielding a vacuous test. The binding pressure must come from the
  per-term unit cap so that over-approx's THREE forced ANDed courses overflow the cap where the
  exact two-of-three does not.)*
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

**Write-workbook ownership (resolves the Task 4↔Task 5 file-ownership gap).**
`mapping.write_workbook(section_records, program, path)` has **no** `prereqs`/`enriched`
parameters today and internally calls `build_catalog_df(section_records, program)` with none.
To keep file ownership clean, the optional pass-through params are added to `write_workbook`
**under the mapping task** (the task that owns `mapping.py`):
`write_workbook(section_records, program, path, *, prereqs=None)` (the enriched section records
are passed positionally as `section_records`, since `build_sections_df` already reads
`r.get('Cap Enrl'...)`). `build_live_workbook` (Task 5) then only **passes** the already-built
prereq map and the enriched records into `write_workbook` — it does NOT modify `mapping.py`.
`prereqs=None` keeps `write_workbook`'s existing call sites byte-identical.

## 6. IR enrollment ingest (fixture-driven, real-shape)

The committed fixture `files/lamc_sample_enrollment.xlsx` already mirrors the real IR
PeopleSoft export (populated `Cap Enrl/Tot Enrl/Wait Tot`, **no instructor PII**, terms
`{2248, 2252}`; pinned by `tests/test_sample_enrollment_fixture.py`). A new pure
`sources/enrollment.py` reads that **IR-shaped** workbook into a join map and threads counts
onto section records.

- `load_enrollment(path) -> dict[(term:int, class_nbr:str) -> {Cap, Tot, Wait}]` reads the
  IR `sections` sheet (columns `Term`, `Class Nbr`, `Cap Enrl`, `Tot Enrl`, `Wait Tot`; the
  full documented schema in §9). It is **pure** (file read only, no network) and raises
  `SourceDataError` naming the file on missing columns. The IR `Class Nbr` is a bare int
  (`20001`); the key is `str(int(class_nbr))` (the canonical bare-CRN form, §9).
- `enrich_sections(section_records, enrollment) -> list[dict]` is **pure-returning a NEW list**
  of records (it does NOT mutate the caller's dicts in place — this keeps it idempotent and
  non-aliasing). It joins on the canonical **CRN** key (see below) and the term; matched records
  get `Cap Enrl / Tot Enrl / Wait Tot`; unmatched (live-only) records keep `0`.
  - **CRN extraction (REQUIRED — the schedule side is decorated).** `schedule.fetch_sections`
    emits `class_nbr` as a *decorated* string like `'17818 (LEC)'` / `'17819 (LAB)'` (verified:
    ALL 81 records in the committed schedule fixture carry the ` (LEC)`/` (LAB)` suffix), while
    IR `Class Nbr` is a bare int. Plain `str()` of each side NEVER matches. The join therefore
    extracts the leading integer CRN from the schedule side —
    `re.match(r'\s*(\d+)', str(class_nbr))` → group(1) — and keys on that canonical bare CRN on
    BOTH sides. A blank/empty `class_nbr` is **skipped** (never keyed on `''`), so blank-CRN
    relsections are not falsely matched.
  - **Idempotent / non-aliasing.** Calling `enrich_sections` twice is a no-op beyond the first
    (it returns fresh dicts, never accumulating). Two live records sharing the same
    `(term, CRN)` each get the counts written exactly once **per record** — but note
    `engine.analyze` SUMS `Cap/Tot/Wait` across all rows of a `CLASS` (`engine.py:138-145`), so
    duplicate `(term, CRN)` live rows would double-count downstream. In the committed IR fixture
    the `(term, class_nbr)` key is unique (verified 116/116), and `enrich_sections` does not
    aggregate — it writes the per-section count onto each matching section, matching
    `engine.analyze`'s per-row sum. Pinned by test: a section list with a duplicated
    `(term, CRN)` and a blank-`class_nbr` relsection — counts written once per matching record;
    blanks not falsely matched.
- **The additive `mapping.py` seam** (`build_sections_df`): replace the three literal `0`s
  (`mapping.py:63-65`) with `r.get('Cap Enrl', 0) / r.get('Tot Enrl', 0) / r.get('Wait Tot',
  0)`. Records enriched by the join carry real counts; live-only records stay `0`. No column
  or schema change; existing tests stay byte-identical (their records carry no enrollment
  keys → default 0).

**Join-seam risk (§ from survey):** `build_sections_df` does not carry `class_nbr` into the
DataFrame today, so the join must run on the **raw section-record dicts** before
`build_sections_df`. `enrich_sections` operates on the records list (returning a new list),
preserving that ordering.

**FIXTURE-ONLY join validation (no overclaiming).** No committed schedule+enrollment fixture
pair shares a `(term, CRN)`: the schedule fixture is term `2268`; the enrollment fixture is
terms `{2248, 2252}`; and the CRN value-ranges, though overlapping in range, have **zero actual
intersection**. The CRN-extraction step is therefore unit-tested on a real `'17818 (LEC)'`-shaped
value (proving the suffix strip), and the **end-to-end detector activation** is exercised on
records derived from the enrollment fixture's own terms OR a hand-keyed inline enrollment map
matching the term-2268 schedule CRNs. The live-schedule → enrollment join is **NOT** validated
on the real schedule shape by any committed fixture pair; this is stated in code comments, the
report, and §11.3.

## 7. Live-detector activation (honest, fixture-tested)

`build_live_workbook.INERT_DETECTORS` (`build_live_workbook.py:56-75`) names the three gaps as
machine-readable report fields. The enrichment flips each entry from inert to **active** only
when the corresponding data is present:

- `modality_mismatch` + `under_supply` → active when an enrollment export was joined **and the
  join actually matched ≥1 section**. Because no committed schedule+enrollment fixture pair
  shares a `(term, CRN)` (§6), a real live-schedule run with today's fixtures matches **nothing**
  and these detectors stay INERT; the activation is exercised only on the enrollment fixture's
  own terms (offline) or a hand-keyed inline map. The report labels the enrollment activation
  *fixture-scoped (live-schedule↔IR join not validated on real data)*.
- `prerequisite_ordering` → active when a prereq map was threaded in; per-course it
  distinguishes exact-CNF courses from budget-fallback courses (the latter labeled
  *conservative-permissive, not exact*).

When data is **absent** (or the join matched zero rows), the entry stays inert with its existing
honest reason. The eLumen slice (no real fixture) is additionally labeled *fixture-only /
not-validated-on-real-data* in the report and code comments. **No detector is silently flipped
without its data.**

## 8. File layout (additive — engine.py / app.py untouched)

```
edgesched/
  sources/
    prereq_cnf.py        # NEW pure DNF->CNF: dnf_to_cnf + to_catalog_string + ConversionResult (inlines _norm; imports NO pandas/mapping)
    elumen.py            # NEW fixture-only eLumen DNF parser + build_prereq_map (DOCUMENTED real shape)
    enrollment.py        # NEW pure IR-workbook reader + (term, bare-CRN) join (returns a NEW records list)
    mapping.py           # MODIFIED: build_sections_df reads r.get('Cap Enrl'...); build_catalog_df(prereqs=None); write_workbook(*, prereqs=None)
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
| **`Class Nbr`** | int | yes (join key) | PeopleSoft CRN, a **bare int** (`20001`). Joins to the **CRN extracted from** `schedule.fetch_sections` `class_nbr`, which is a **decorated string** `'17818 (LEC)'` / `'17819 (LAB)'` — the join strips the ` (LEC)`/` (LAB)` suffix (`re.match(r'\s*(\d+)', ...)`) before comparing. Plain `str()` is insufficient. |
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

**Join contract:** `(int(Term), canonical_CRN)` where `canonical_CRN` is the bare integer CRN
as a string. The IR side is `str(int(Class Nbr))`; the schedule side strips the
` (LEC)`/` (LAB)` (or any non-digit) suffix via `re.match(r'\s*(\d+)', str(class_nbr)).group(1)`.
A blank/empty `class_nbr` is skipped (not keyed on `''`). **Plain `str()` on both sides does NOT
reconcile `'17818 (LEC)'` with `17818` — the suffix strip is mandatory.** Pinned by a unit test
on a real `'17818 (LEC)'`-shaped value.

**Degraded `(Term, CLASS)` fallback (PLAN-ONLY, NOT IMPLEMENTED this run).** If a real export
lacks `Class Nbr`, a coarser join on `(Term, CLASS)` is *possible* but is **largely inert across
the two real sources** and is therefore documented, not built:
- The schedule side emits `CLASS = _norm(course)` = `'BIOLOGY 003'` / `'CHEM 101'` (full subject
  word, zero-padded catalog nbr); the IR `CLASS` column is `'BIOL 6'` / `'ACCTG 2'` (abbreviated
  subject, unpadded). These are NOT `_norm`-identical: verified only 2 of the program's courses
  (`CHEM 101`/`102`) coincide, and those still fail the term check. A real `(Term, CLASS)` join
  would require a subject-abbreviation + catalog-zero-padding normalization map on both sides.
- The aggregation semantics would also have to avoid double-counting: `engine.analyze` already
  sums `Cap/Tot/Wait` per `CLASS` (`engine.py:138-145`), so a pre-aggregated per-course-term
  value written onto every section would double-count. Any future implementation must write
  per-section (not pre-aggregated) counts, normalize CLASS through the abbreviation/padding map
  on BOTH sides, and flag the path as coarser and **not validated against the live schedule
  course-id form.** Until then this fallback is **plan-only** and unimplemented.

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
3. **Enrollment ingest is real-shape but fixture-sourced, and the join is NOT validated against
   the live schedule.** It reads the real IR PeopleSoft layout, but the data is the synthetic
   `lamc_sample_enrollment.xlsx`. The report names the source file. **The live-schedule ↔ IR
   `(term, CRN)` join produces ZERO matches on every committed fixture pair** — for two
   independent, verified reasons: (a) the schedule `class_nbr` is a decorated string
   (`'17818 (LEC)'`) vs. the IR bare int (`20001`), reconciled only by the §9 CRN-suffix strip;
   and (b) the schedule fixture is term `2268` while the enrollment fixture is terms
   `{2248, 2252}` — zero term overlap, and the CRN sets do not intersect even after stripping.
   So `modality_mismatch` / `under_supply` are validated **only within self-consistent records**
   (the enrollment fixture's own terms, offline, or a hand-keyed inline map matching the
   term-2268 schedule CRNs), **never across the live schedule↔IR boundary.** A real
   `--enrollment` run with today's fixtures keeps every section at `Cap/Tot/Wait = 0` and the
   enrollment detectors INERT. The report and the plan's smoke step state this explicitly;
   detectors are active only when the join actually matched ≥1 row.
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
- DNF→CNF over a pathological prereq (or one carrying a delimiter/paren-bearing literal) falls
  back to a permissive union clause; ordering for that one course relaxes (flagged), it is not
  exact.
- The IR join needs the schedule `class_nbr`, stripped to a bare CRN (§9). With today's
  committed fixtures the live-schedule↔IR join matches **nothing** (term + CRN disjoint); the
  join is fixture-only / not validated on the real schedule shape. The degraded `(Term, CLASS)`
  fallback is **plan-only and unimplemented**: across the two real sources it matches almost
  nothing (subject-abbreviation + catalog-padding mismatch — verified only `CHEM 101/102`
  coincide, and those fail the term check).
