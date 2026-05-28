# LAMC 2-Year Completion Scheduling — MVP Pipeline

A working prototype that finds why community-college cohorts can't finish 2-year
programs on time, and recommends the minimum schedule changes to fix it.

Runs entirely on **synthetic data** today. Swap in real exports later with no
code changes — the analysis reads files by column name.

## Files

| File | Role |
|------|------|
| `generate_synthetic.py` | Generates the three data files with planted bottlenecks |
| `sections.xlsx` | 8 terms of section offerings (real PeopleSoft schema, no PII) |
| `catalog.csv` | Course master with structured prerequisites |
| `programs.csv` | Program requirements + recommended 4-semester maps |
| `analyze_bottlenecks.py` | Descriptive report: rotation gaps, single-section risk, modality mismatch, under-supply, feasibility |
| `solve_schedule.py` | OR-Tools solver: corrected 4-term plans + minimum schedule fixes |
| `load_neo4j.py` | Loads everything into Neo4j as a graph |

## Run order

```bash
pip install pandas openpyxl ortools neo4j

python generate_synthetic.py     # creates sections.xlsx, catalog.csv, programs.csv
python analyze_bottlenecks.py    # the "what's broken" report
python solve_schedule.py         # the "here's the fix" report

# graph load (needs a running Neo4j)
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=yourpassword
python load_neo4j.py --clear
# or, without a DB handy:
python load_neo4j.py --dry-run
```

## What the demo shows

The synthetic data has deliberately planted problems so the tools have something
to find:

- **Business AS-T** — clean. Official map valid, completes in 4 terms.
- **CS AS-T / Biology AS-T** — official Program Mapper sequence is *broken*
  (a required course is mapped to a term it's never offered), but the solver
  finds a corrected 4-term path by resequencing.
- **Engineering AS-T** — genuinely impossible: a 3-deep prerequisite chain all
  locked to Fall can't fit in two years. The solver's `minfix` mode reports the
  single change that unblocks it: *add one ENGR 103 section in Spring.*

## Swapping in real data

Replace the three generated files with real exports that use the same column
headers (see the data request spec). Specifically:

- `sections.xlsx` ← PeopleSoft enrollment report (`Data and formulas` sheet)
- `catalog.csv` ← eLumen course outlines, prerequisites parsed to
  `(A OR B) AND (C)` form
- `programs.csv` ← Program Mapper requirements + recommended sequence

Then re-run. Nothing else changes.

## Architecture

```
  data files ──► Neo4j graph ──► OR-Tools solver ──► reports / dashboard
   (or scrape)      (load)        (feasibility +        (admin)
                                   min fix)
```

The synthetic generator stands in for your scraper. The graph is the shared
substrate. The solver is the engine. Reports and dashboards sit on top.

## Cohort profiles

`solve_schedule.py` evaluates each program against multiple cohort types, defined
in the `COHORTS` dict at the top of the file:

- **Full-time** — 18 unit/term cap, 4-term (2-year) horizon
- **Part-time** — 9 unit/term cap, 8-term (4-year) horizon

For each it reports the fastest feasible completion time, or — if the offerings
can't support that cohort within the horizon — the minimum schedule change that
would. This is the analysis that separates *scheduling* problems from *structural
load* problems: a program may be impossible full-time (needs a new section) yet
perfectly feasible part-time (just takes more terms). Add or edit cohort rows to
model evening-only students, summer-inclusive paths, etc.

## Notes

- No student-level data anywhere. Instructor fields are never loaded.
- Term-season is derived from the term code (ends in 8 = Fall, 2 = Spring),
  matching the confirmed real code (Fall 2024 = 2248). Adjust if your coding
  differs.
- The minfix model currently treats every "add a section" as equal cost. With
  real data you can weight the fix penalties so recommendations respect what is
  actually schedulable (room/faculty availability).
