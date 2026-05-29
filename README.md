# EdgeSched

A deterministic scheduling engine that finds why community-college cohorts
can't finish 2-year programs on time and recommends the minimum schedule
changes to fix it. Ships as a desktop app (pywebview) backed by an OR-Tools
CP-SAT solver. Works on real LACCD data or a bundled synthetic demo.

## Quick Start

### Install

```bash
pip install -r requirements.txt
```

### Run the headless analysis

```bash
# Uses the bundled demo workbook (files/lamc_data.xlsx):
python3 engine.py

# Or point at your own workbook or a folder of three CSVs:
python3 engine.py path/to/workbook.xlsx
python3 engine.py path/to/csv-folder/
```

Prints JSON: bottleneck analysis + per-program, per-cohort completion plans
and minimum fixes.

### Run the desktop app

```bash
python3 app.py
```

Opens a native window (pywebview) with the full interactive UI.

### Build a workbook from live LACCD data

```bash
python3 build_live_workbook.py --campus LAMC --program Biology \
    --terms 2264,2266,2268 --out data/live_LAMC.xlsx
```

Scrapes live LACCD sources and writes a ready-to-use workbook (`--out`
defaults to `data/live_LAMC.xlsx` if omitted). Use the resulting `.xlsx`
with `engine.py` or drag it into the desktop app.

### Regenerate the bundled synthetic demo

```bash
python3 generate_synthetic.py --out files/lamc_data.xlsx
```

Recreates the three-sheet demo workbook with planted bottlenecks so the
engine has something interesting to find.

### Run the test suite

```bash
python3 -m pytest -q                 # fast, no network
python3 -m pytest -m live            # network-gated integration tests
```

## Optional integrations

### AI briefings / prereq parsing (Ollama + Gemma)

`llm_assist.py` uses a local Gemma model via Ollama to parse messy
prerequisite text and write plain-English schedule briefings. The engine
falls back gracefully to a rule-based template if Ollama is absent — no
setup required for core functionality.

Install Ollama and pull the model if you want the AI layer:

```bash
ollama pull gemma3:4b
```

### Neo4j graph layer (reference prototype, not wired in)

`legacy/load_neo4j.py` is an early reference prototype that sketches loading
the scheduling data into a Neo4j graph for advanced graph queries and
dashboards (TECH_SPEC §6). It is not wired into the engine, app, or live
pipeline, and is not runnable as-is (it carries hardcoded sandbox paths). It
is retained for reference only — treat it as design notes, not an installable
feature.

## Architecture

```
live LACCD data ──► build_live_workbook.py
                          │
                          ▼
    files/lamc_data.xlsx (or user-supplied file)
                          │
                  engine.py  (OR-Tools CP-SAT solver)
                          │
            ┌─────────────┼─────────────┐
        app.py         JSON out      llm_assist.py
      (desktop UI)   (headless)   (optional AI layer)
```

The schedule is always produced by the deterministic solver. The LLM layer
only parses messy text and writes explanations — it never decides the
schedule.

## Demo data

The bundled demo (`files/lamc_data.xlsx`) has deliberately planted problems:

- **Business AS-T** — clean; official map valid, completes in 4 terms.
- **CS AS-T / Biology AS-T** — official Program Mapper sequence is broken
  (a required course mapped to a term it is never offered), but the solver
  finds a corrected 4-term path.
- **Engineering AS-T** — genuinely impossible: a 3-deep prerequisite chain
  locked to Fall can't fit in two years. The solver reports the single change
  that unblocks it: add one ENGR 103 section in Spring.

## Notes

- No student-level data anywhere. Instructor fields are never loaded.
- Term season is derived from the term code (ends in 8 = Fall, 2 = Spring,
  matching Fall 2024 = 2248). Adjust in `engine.py` if your coding differs.
- `legacy/` contains early prototype scripts (`analyze_bottlenecks.py`,
  `solve_schedule.py`, `load_neo4j.py`) retained for reference; they are not
  part of the product.
