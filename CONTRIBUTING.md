# Contributing to SchedulePlanner

Thanks for your interest in improving SchedulePlanner. This is a small,
deterministic codebase — contributions that keep it that way are very welcome.

## Development setup

Requires **Python 3.12 or 3.13**.

```bash
# Reproducible, hash-pinned install (what CI uses):
pip install --require-hashes -r requirements.lock

# Or, for a looser local dev install:
pip install -r requirements.txt
```

Run the app or the headless engine to sanity-check your environment:

```bash
python3 engine.py        # headless analysis on the bundled demo workbook
python3 app.py           # desktop UI (pywebview)
```

## Running tests

The default suite is fully offline and must stay green:

```bash
pytest                   # runs `-m "not live"` per pytest.ini
bash scripts/run_qa.sh   # what CI runs (the full QA gate)
```

Network-touching tests are marked `live` and are **deselected by default**; run
them explicitly only when you mean to hit the public LACCD APIs:

```bash
pytest -m live
```

## Guidelines

- **Keep the engine deterministic and offline.** `engine.run` must never make
  network calls — all IO lives behind `sources/` and the live entry points.
  There is a test that enforces this; please don't work around it.
- **No student-level data or PII**, ever. See
  `tests/test_privacy_invariants.py` for the invariants you must not break.
- **Add tests** for new behavior, and make sure the offline suite passes before
  opening a PR.
- Match the style and structure of the surrounding code.

## Submitting changes

1. Fork the repo and create a topic branch (`feat/...`, `fix/...`, `chore/...`).
2. Make your change with accompanying tests.
3. Ensure `bash scripts/run_qa.sh` passes locally.
4. Open a pull request describing **what** changed and **why**.

For security issues, do **not** open a public PR or issue — see
[SECURITY.md](SECURITY.md).
