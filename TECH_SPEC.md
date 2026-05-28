# Technical Specification
## 2-Year Completion Schedule Planner

**Status:** Implementation in active development
**Companion:** PRD.md (product requirements), BUILD.md (packaging)

---

## 1. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Native window (pywebview)              │
│   ┌─────────────────────────────────────────────────────┐   │
│   │   ui.html  ── JS bridge ──► Api class (app.py)       │   │
│   └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            │
                ┌───────────┴───────────┐
                ▼                       ▼
        engine.py                  llm_assist.py
   (deterministic core)         (Gemma 4 E2B via Ollama
                                 OR regex/template fallback)
                │
                ├── load_data()      ── parses workbook / CSVs
                ├── parse_prereq()   ── structured prereq logic
                ├── analyze()        ── bottleneck diagnostics
                ├── solve_cohort()   ── OR-Tools CP-SAT solver
                └── run()            ── top-level JSON output

  Optional substrate: Neo4j (load_neo4j.py) for query/dashboarding
```

**Tier responsibilities**

- **Interface (ui.html, app.py)** — file picking, status display, results
  rendering. Stateless except for the last results blob.
- **Engine (engine.py)** — all scheduling logic. Pure functions over
  DataFrames. Returns JSON-serializable dicts.
- **AI assist (llm_assist.py)** — optional. Only handles text parsing and
  text generation. Never makes scheduling decisions.
- **Graph (load_neo4j.py)** — optional persistence/query layer for dashboards
  and ad-hoc Cypher.

## 2. Module Reference

| Module | Public API | Notes |
|---|---|---|
| `engine.py` | `run(path, llm=None)` | Entry point; returns full results dict |
| `engine.py` | `load_data(path)` | Accepts `.xlsx` workbook or directory of CSVs |
| `engine.py` | `solve_cohort(...)` | Per-program, per-cohort solver |
| `engine.py` | `analyze(active, prog, n_terms)` | Bottleneck diagnostics |
| `llm_assist.py` | `available()` | True if Ollama+model are both up |
| `llm_assist.py` | `make_prereq_parser()` | Callable for `engine.parse_prereq` |
| `llm_assist.py` | `explain(results)` | Plain-language briefing |
| `llm_assist.py` | `ensure_model(model)` | Pulls model if missing |
| `app.py` | `Api` class | JS bridge surface |
| `load_neo4j.py` | `main()` | CLI: `--clear`, `--dry-run` |
| `generate_synthetic.py` | module-level | Produces demo data with planted bottlenecks |

## 3. Data Model

### 3.1 Input workbook

Three sheets. Column names match the real LACCD PeopleSoft enrollment export
(`Data and formulas` sheet).

#### `sections` (one row per section per term)

| Column | Type | Required | Notes |
|---|---|---|---|
| `Term` | int | yes | Term code (e.g., 2248 = Fall 2024) |
| `Descr` | str | yes | Human-readable term ("2024 Fall") |
| `Campus` | str | yes | "LAMC" |
| `Class Nbr` | int | yes | CRN |
| `Subject` | str | yes | e.g., "MATH" |
| `Catalog` | str | yes | e.g., "245" |
| `Section` | str | yes | e.g., "M01" |
| `CLASS` | str | yes | Combined "Subject Catalog" |
| `Mode` | str | yes | Modality code (P, H, OA, OS, HY) |
| `IN_PERSON` | str | derived | "Y" or empty |
| `Cap Enrl` | int | yes | Capacity |
| `Tot Enrl` | int | yes | Enrollment |
| `Wait Tot` | int | yes | Waitlist count |
| `Mtg Start`, `Mtg End` | str | optional | "HH:MM" |
| `Meetings` | str | optional | Days pattern ("MWF") |
| `M`,`T`,`W`,`R`,`F`,`S`,`N`,`TBA` | str | derived | Day-of-week flags |
| `Class Status` | str | yes | "Active" / "Cancelled" |
| `IGETC`, `OER` | str | optional | "Y" or empty |
| `Pacoima` | str | optional | "Y" if satellite |

PII fields (`Name`, `SAP Primary ID`, `Emails`, etc.) are **excluded by
ingestion contract**, not just by the engine.

#### `catalog` (one row per course)

| Column | Type | Notes |
|---|---|---|
| `Course ID` | str | "Subject Catalog" |
| `Subject` | str | |
| `Catalog` | str | |
| `Title` | str | |
| `Units` | float | |
| `Prerequisites (structured)` | str | `"(A OR B) AND (C)"` form |
| `IGETC Area` | str | |
| `OER` | str | "Y" / empty |
| `Discipline` | str | |

#### `programs` (one row per program-course requirement)

| Column | Type | Notes |
|---|---|---|
| `Program Code` | str | e.g., "AS-T-CSCI" |
| `Program Title` | str | |
| `GE Pattern` | str | "IGETC" / "CSU GE-Breadth" / "Local AA" |
| `Course ID` | str | Required course |
| `Recommended Semester` | int | 1–4 in the official map |

### 3.2 Derived internal structures

```python
COURSE_SEASONS: dict[str, set[str]]    # "MATH 245" -> {"Fall", "Spring"}
PREREQS:        dict[str, list[list[str]]]   # AND of OR-groups
UNITS:          dict[str, float]
```

### 3.3 Output JSON shape

```jsonc
{
  "terms_in_data": 8,
  "ai_used": false,
  "analysis": {
    "rotation_gaps":     [{"course": "BIOL 7", "offered": 4, "of": 8}, ...],
    "single_section":    [{"course": "CHEM 211"}, ...],
    "modality_mismatch": [{"course": "MATH 246", "fill_pct": 42}, ...],
    "under_supply":      [{"course": "ENGL 101", "waitlisted": 112}, ...]
  },
  "programs": {
    "AS-T-CSCI": {
      "title": "Computer Science AS-T",
      "official_map_issues": ["CS 103 mapped to sem 3 ..."],
      "cohorts": {
        "full_time": {
          "terms_used": 4,
          "plan": {"1": ["CS 101", "ENGL 101", ...], "2": [...], ...},
          "fixes": [],
          "needs_fix": false
        },
        "part_time": { ... }
      }
    }
  }
}
```

## 4. Scheduling Model (CP-SAT)

**Decision variables**

```
take[c, t] ∈ {0, 1}      for c in courses(closure), t in 1..H
```

**Constraints**

1. *Each course taken exactly once:*
   `∀c:  Σ_t take[c, t] = 1`

2. *Season availability (hard or penalized):*
   `take[c, t] = 0`  if `term_season(t) ∉ COURSE_SEASONS[c]`
   (in `minfix` mode this becomes a penalty term instead of a hard constraint)

3. *Prerequisite ordering (AND of OR-groups):*
   For every OR-group `G` of course `c`:
   `take[c, t] = 1 → Σ_{p∈G, t'<t} take[p, t'] ≥ 1`

4. *Per-term unit cap:*
   `∀t:  Σ_c units[c]·take[c, t] ≤ MAX_UNITS[cohort]`

**Objective**

```
last = max over c of (Σ_t t · take[c, t])
minimize:  1000 · Σ(fix penalties) + last      (in minfix mode)
           last                                (otherwise)
```

The big-M coefficient (1000) ensures fixes are minimized lexicographically
before makespan.

**Cohort profiles** (in `engine.COHORTS`)

| Cohort | Max units/term | Horizon | Use case |
|---|---|---|---|
| `full_time` | 18 | 4 terms | 2-year completion question |
| `part_time` | 9 | 8 terms | Realistic timeline for majority of CC students |

New cohorts (evening-only, summer-inclusive, ESL-track) are added by
appending rows to the dict.

## 5. AI Assist Layer

### 5.1 Model
- **Name:** Gemma 4 E2B (Effective 2B parameters)
- **Provider:** Google DeepMind, open-weights, Apache 2.0
- **Released:** April 2026
- **Runtime:** Ollama, localhost:11434
- **Model tag:** `gemma4:e2b` (verify against published tag before shipping)

### 5.2 Jobs

| Job | Input | Output | Fallback |
|---|---|---|---|
| Parse prereq text | Free text like *"MATH 125 or equivalent"* | `[["MATH 125"]]` | Regex parser in `engine.parse_prereq` |
| Write briefing | Full results dict | Concise admin summary | Templated summary from `_template_summary` |

### 5.3 Boundary

The LLM does not see anything except the specific text it is asked to parse,
plus the results dict for the briefing. It never receives or decides on
scheduling logic. The solver output is always authoritative.

### 5.4 Availability detection

```python
ollama_installed()  -> bool   # shutil.which("ollama")
ollama_running()    -> bool   # GET /api/tags
model_present(tag)  -> bool   # tag found in /api/tags response
available()         -> bool   # running AND model present
```

## 6. Graph Schema (Neo4j)

Optional. Used for ad-hoc queries, dashboarding, and integration with the
existing chatbot stack (which already uses Neo4j).

```
(:Course {id, subject, catalog, title, units, igetc, oer, discipline})
(:Section {crn, term, section, mode, in_person, days, start, end,
           cap, enrl, wait, status, fill, pacoima})
(:Term {code, descr, season, year})
(:Program {code, title, ge_pattern})

(:Section)-[:SECTION_OF]->(:Course)
(:Section)-[:OFFERED_IN]->(:Term)
(:Course)-[:HAS_PREREQ {group}]->(:Course)
(:Program)-[:REQUIRES_COURSE {recommended_semester}]->(:Course)
```

**Indexes / constraints**
```cypher
CREATE CONSTRAINT course_id   FOR (c:Course)  REQUIRE c.id IS UNIQUE
CREATE CONSTRAINT term_code   FOR (t:Term)    REQUIRE t.code IS UNIQUE
CREATE CONSTRAINT prog_code   FOR (p:Program) REQUIRE p.code IS UNIQUE
CREATE CONSTRAINT section_crn FOR (s:Section) REQUIRE (s.crn, s.term) IS UNIQUE
```

## 7. JS ↔ Python Bridge

```ts
window.pywebview.api.choose_file()  : Promise<{path: string}>
window.pywebview.api.ai_status()    : Promise<{installed, running, model, model_name}>
window.pywebview.api.setup_ai()     : Promise<{ok: boolean}>
window.pywebview.api.analyze(path)  : Promise<ResultsDict | {error: string}>
window.pywebview.api.explain()      : Promise<{text: string}>
```

All methods are async (pywebview marshals them as promises).

## 8. Dependencies

### 8.1 Runtime (Python)
- `python` 3.10+
- `pandas` ≥ 2.0
- `openpyxl` ≥ 3.1
- `ortools` ≥ 9.8
- `pywebview` ≥ 5.0
- `neo4j` ≥ 5.0 (only for `load_neo4j.py`)

### 8.2 External (optional)
- **Ollama** — required only if AI features are used. Separate user install.
- **Neo4j** — required only for the graph layer. Separate user install.

### 8.3 Build
- `pyinstaller` ≥ 6.0
- Platform toolchain (Xcode CLT on macOS, MSVC build tools on Windows)

## 9. Deployment

### 9.1 Desktop binary

```bash
pyinstaller --noconfirm --windowed --name "SchedulePlanner" \
  --add-data "ui.html:." \
  --collect-all ortools \
  app.py
```

Output: `dist/SchedulePlanner/` (folder) or `dist/SchedulePlanner` (with
`--onefile`). Per-platform separators and notes in `BUILD.md`.

### 9.2 First-run AI setup
1. App starts, calls `ai_status()`.
2. If Ollama installed but model missing, UI offers "get it" → `setup_ai()`
   → `ollama pull gemma4:e2b`.
3. If Ollama missing, UI links to https://ollama.com/download and the app
   continues in offline mode.

### 9.3 Update model
Edit one line in `llm_assist.py`:
```python
MODEL = "gemma4:e2b"   # or gemma4:e4b for more capable parsing
```

## 10. Security and Privacy

- **No PII processed.** Instructor fields are excluded at ingestion. Student
  data is out of scope by design.
- **No external network.** The only network call is to `localhost:11434` for
  Ollama (optional).
- **No telemetry.** No analytics, no error reporting.
- **No persistence by default.** Results live in memory; nothing written to
  disk except what the user explicitly exports.

## 11. Testing

- **Synthetic data with planted bottlenecks** (`generate_synthetic.py`)
  provides deterministic, reproducible inputs covering all four scenarios:
  valid official map, broken-but-resequenceable, broken-needs-fix, and
  feasible-part-time-but-not-full-time.
- **Fallback paths** are exercisable without Ollama installed.
- **Engine is testable headlessly** via `python engine.py path/to/file.xlsx`.

## 12. Known Limitations

- Term-season is currently derived from the term-code suffix (`...8` = Fall,
  `...2` = Spring). Adjust `season_of_code` if another district uses a
  different scheme.
- The solver currently considers only season-level offering availability, not
  time-of-day conflicts within a term. Adding time conflicts is a model
  extension (additional constraints + section-level decision variables).
- `gemma4:e2b` tag should be confirmed against the published Ollama tag
  before shipping.
- Single-user desktop tool — no multi-user collaboration, history, or
  versioning.

## 13. Extension Points

- **More cohort profiles:** add rows to `engine.COHORTS`.
- **Time-of-day conflicts:** extend solver with section-level take vars and
  pairwise conflict constraints.
- **Section-level capacity recommendations:** layer a second optimization on
  the minfix output that selects modality and time-block for new sections.
- **Web version:** replace the pywebview shell with FastAPI + the same UI;
  engine module is unchanged.
- **Chatbot integration:** call `engine.run` from the existing
  lamc-chat backend; results dict is already JSON.
