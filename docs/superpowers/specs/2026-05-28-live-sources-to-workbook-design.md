# Design: Live data sources → EdgeSched workbook (prototype)

**Date:** 2026-05-28
**Status:** Approved (design); pending implementation plan
**Related:** PRD.md, TECH_SPEC.md, engine.py
**Companion repo:** `/Users/laccd/code/project_laccd_chatbot` (source of the ported clients)

---

## 1. Goal

Prove that **live LACCD data sources can produce valid EdgeSched engine inputs**, by
porting two of the chatbot's public-API clients (LACCD live schedule, Program
Mapper) into the edgesched repo as standalone, dependency-light modules, mapping
their output into the engine's `.xlsx` workbook schema, and running
`engine.run(path)` on the generated workbook as a smoke check.

This is a **prototype extraction**, not a production integration. It stops before
making the solver depend on unstable external APIs.

## 2. Boundary (non-negotiable)

Network IO lives **entirely outside** `engine.run()`. The engine keeps its current
contract: deterministic, offline, path in → results out. The flow is:

```
fetch (live HTTP) → map → write .xlsx → engine.run(path)
```

`engine.run()` gains **no** awareness of live clients. The smoke check calls it on
a generated workbook exactly as it would on a hand-authored one.

## 3. Why these two sources, and why now

Both are **public, unauthenticated REST APIs** (no secrets — safe for a public
GitHub repo) and self-contained (`httpx` + parsing only; no GCP/Vertex/Neo4j/
Firestore). They map cleanly onto two halves of the engine's input:

- **LACCD schedule API** (`https://services.laccd.edu/apps/api/classschedule`) →
  the *supply/structure* half: which courses are offered, in which terms, how many
  sections, units, modality.
- **Program Mapper API** (`https://b.api.programmapper.com`) → the *program
  requirements* half: required courses per program and their recommended semester
  (drives `programs` sheet + official-map-violation analysis).

eLumen (prerequisites) and Assist.org (articulation) are the documented *next*
sources, explicitly out of scope here (see §10).

## 4. Ground truth: what the engine actually consumes

Read from `engine.py` (not the spec prose). The engine touches far fewer columns
than TECH_SPEC §3.1 documents:

| Sheet | Columns the engine reads | Where |
|---|---|---|
| `sections` | `Term`, `CLASS`, `Class Status`, `Cap Enrl`, `Tot Enrl`, `Wait Tot` | `build_model` (line 57-59), `analyze` (line 90-98) |
| `catalog` | `Course ID`, `Units`, `Prerequisites (structured)` | `build_model` (line 60-62) |
| `programs` | `Program Code`, `Program Title`, `Course ID`, `Recommended Semester` | `run`, `solve_cohort`, `official_map_issues` |

Two engine behaviors the mapping must respect:

- **Gotcha 1 — units must be numeric.** `solve_cohort` does `int(units.get(c, 3))`
  (line 130). The schedule API returns units as the string `"3.00"`; `int("3.00")`
  raises `ValueError`. Mapping coerces units to `float`.
- **Gotcha 2 — season bucketing.** `season_of_code = lambda t: "Fall" if
  str(t).endswith("8") else "Spring"` (line 22). Summer term codes (`...6`) collapse
  into "Spring". This is an existing engine simplification; the mapping just emits
  real term codes and does not work around it.
- **KeyError guard.** `analyze` references `d["Cap Enrl"]`, `d["Tot Enrl"]`,
  `d["Wait Tot"]` unconditionally (line 91-96). These columns **must be present** in
  the sections sheet even though the schedule API can't fill them — emit as `0`.

## 5. File layout (additive — engine.py / app.py untouched)

```
edgesched/
  sources/
    __init__.py
    http.py                 # injectable-client helper (owns_client pattern)
    schedule.py             # LACCD schedule client (sync) + fetch_sections()
    program_mapper.py       # Program Mapper client (sync) + fetch_program()
    mapping.py              # API dicts -> 3 engine sheets -> write_workbook()
  build_live_workbook.py    # CLI/demo: orchestrate fetch+map+write+engine.run()
  tests/
    fixtures/               # captured real LAMC JSON for offline tests
    test_schedule_client.py
    test_program_mapper_client.py
    test_mapping.py
    test_live_roundtrip.py  # fixture -> workbook -> engine.run -> assert shape
```

## 6. Components

### 6.1 `sources/http.py`
Single helper implementing the injectable-client pattern so callers can reuse one
connection across many requests (Program Mapper makes several sequential calls;
schedule makes one per subject/term) and tests can inject a fake:

```python
def get_json(url, *, params=None, headers=None, client=None, timeout=30.0):
    owns_client = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        r = client.get(url, params=params, headers=headers)
        r.raise_for_status()
        return r.json()
    finally:
        if owns_client:
            client.close()
```

### 6.2 `sources/schedule.py`
Ported from chatbot `live_schedule.py`, stripped of `langfuse`/`app.config`. Sync.
- `get_subjects(campus, term, *, client=None)`
- `get_class_listing(campus, term, subjects=None, *, client=None)`
- `fetch_sections(campus, terms, *, client=None) -> list[dict]` — iterates terms,
  flattens `subjects→courses→sections` **and** `relsections`, returns records with:
  `subject, catalog, term, class_nbr, status, seats, woi, modality (classType),
  units, days, times, room, instr`.
- Default terms: `[2264, 2266, 2268]` (currently-published terms as of 2026-05).

### 6.3 `sources/program_mapper.py`
Ported from chatbot `program_mapper.py`, stripped of `langfuse`/`app.config`. Sync.
Keeps the per-campus `site_content_id` + `Origin`/`Referer` header config (LAMC
default). Walks: home-page-content → program-groups → program → program-map.
- `fetch_program(campus, program_query, *, client=None) -> dict` returning
  `{code, title, ge_pattern, courses: [{course_id, recommended_semester, units,
  title}]}` parsed from `pathwayElements`
  (`name`, `recommendedOpportunity.term.termNumber`, `recommendedOpportunity.minUnits`).

### 6.4 `sources/mapping.py`
Pure functions; no network. Converts the two clients' output into three pandas
DataFrames matching the engine schema, then writes a `.xlsx` with sheets named
exactly `sections`, `catalog`, `programs`.

Mapping table:

| Sheet | Column | Source / rule |
|---|---|---|
| sections | `Term` | schedule termcode → `int` |
| sections | `CLASS` | `f"{subject} {catalog}"` |
| sections | `Class Status` | literal `"Active"` |
| sections | `Cap Enrl`, `Tot Enrl`, `Wait Tot` | `0` (not in API; present to avoid KeyError) |
| catalog | `Course ID` | union of section CLASS codes + PM course codes |
| catalog | `Units` | schedule `units` or PM `minUnits` → `float` (coerced; ranges like `"3-4"` → low bound) |
| catalog | `Prerequisites (structured)` | blank (needs eLumen) |
| programs | `Program Code` | PM `awardShortTitle` (slugified) |
| programs | `Program Title` | PM `title` |
| programs | `Course ID` | PM pathway `name`, normalized (upper, collapse whitespace) |
| programs | `Recommended Semester` | PM `recommendedOpportunity.term.termNumber` |

- `write_workbook(sections_records, programs, path) -> path`
- `reconcile_courses(section_classes, program_courses) -> (matched, unmatched)` —
  normalizes both sides; returns unmatched PM courses so the CLI can surface them.

### 6.5 `build_live_workbook.py` (CLI / demo)
Orchestrates the smoke check:
1. Fetch sections for `campus=LAMC`, `terms=[2264,2266,2268]`.
2. Fetch 1–3 named programs (default: a Computer Science program).
3. Map + `write_workbook(...)` → `data/live_LAMC.xlsx`.
4. Call `engine.run(path)` and pretty-print the results dict.
5. Print an **honesty banner** (see §7) and the `unmatched_courses` report.

Args: `--campus LAMC --terms 2264,2266,2268 --program "Computer Science" --out data/live_LAMC.xlsx`.

## 7. Honest handling of the two known gaps

1. **Enrollment gap.** `Cap/Tot/Wait = 0` ⇒ `analyze()`'s `modality_mismatch` and
   `under_supply` lists are **empty by construction**; `rotation_gaps` and
   `single_section` work fully. No fabricated proxies. CLI prints:
   *"Fill/waitlist detectors inert — requires the IR PeopleSoft enrollment export
   (PRD milestone M4)."*
2. **Prerequisite gap.** Blank prereqs ⇒ solver runs with no ordering constraints
   and still yields a valid makespan plan. CLI flags it; eLumen is the next source.

Both gaps are *expected* and documented, not failures.

## 8. Data flow

```
build_live_workbook.py
  ├─ schedule.fetch_sections(LAMC, [2264,2266,2268])   ─┐
  ├─ program_mapper.fetch_program(LAMC, "Computer Science") ─┤
  │                                                          ▼
  ├─ mapping.reconcile_courses(...) ── unmatched report ── stdout
  ├─ mapping.write_workbook(...) ──────────────────────► data/live_LAMC.xlsx
  └─ engine.run("data/live_LAMC.xlsx") ────────────────► results dict ── stdout
```

## 9. Testing (TDD)

- **Offline by default.** Client tests inject a fake client returning captured
  `fixtures/*.json`; assert parsing (incl. `relsections` flattening, `"Inquire"`/
  sentinel `seats` handling, units coercion).
- `test_mapping.py` — fixture API data → assert DataFrame columns exactly match the
  engine schema, units are numeric, the three KeyError-guard columns exist.
- `test_live_roundtrip.py` — fixture → `write_workbook` → `engine.run` → assert
  results shape: `terms_in_data` int, all four `analysis` keys present, `programs`
  non-empty, a `full_time` cohort plan exists.
- One `@pytest.mark.live` test hits the real API; **skipped by default** so the
  suite is offline/deterministic.

## 10. Out of scope (YAGNI)

- eLumen (prerequisites) and Assist.org (articulation) clients — documented next steps.
- In-memory TTL caching (a long-running-server concern; the chatbot's version is
  dropped). Optionally the CLI may dump raw responses to `data/raw/` for replay.
- Wiring live sources into `engine.run` (violates §2 boundary).
- Multi-campus batch runs (campus is a parameter; LAMC is the default).
- Enrollment/waitlist proxies derived from `status`/`seats` (rejected in favor of honest `0`s).

## 11. Known limitations (carried forward, not bugs)

- Only currently-published terms are fetchable (~3), vs. the ~8 the analysis ideally
  wants — so `COURSE_SEASONS` is partial and forward-looking.
- PM↔schedule course-code mismatches are surfaced via `unmatched_courses`, not
  silently reconciled.
- Summer terms bucket into "Spring" via the engine's `season_of_code` (§4 Gotcha 2).
