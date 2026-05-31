# Continuous integration & reproducible installs

This repo is CI-gated on GitHub Actions and ships a pinned, hash-checked
dependency lockfile so installs are reproducible across machines and over time.

## TL;DR

| Need | Command |
|---|---|
| Local dev (loose, convenient) | `pip install -r requirements.txt` |
| Reproducible / release install | `pip install --require-hashes -r requirements.lock` |
| Run the offline suite + gate | `bash scripts/run_qa.sh` |
| Run the live (network) tests | `python3 -m pytest -m live -rs` (overrides the default `-m "not live"`) |
| Regenerate the lockfile | see [Regenerating the lockfile](#regenerating-the-lockfile) |

## Workflows

### `.github/workflows/ci.yml` — gating CI (push + pull_request)

| Job | Runner | What it does |
|---|---|---|
| `test` | `macos-latest`, Python 3.12 & 3.13 | Installs `requirements.lock` with `--require-hashes`, then runs `scripts/run_qa.sh` (the offline suite **with `-m "not live"`** plus the assertion that exactly the live-marked tests were deselected). This is the reproducible green gate. |
| `lint` | `ubuntu-latest` | `ruff check --select E9,F63,F7,F82` — syntax errors and undefined names only (no style enforcement on the existing code). |
| `build-verify` | `ubuntu-latest` | `scripts/verify_macos_build.sh --self-test` — the portable negative control that proves the build resource checker actually fails on a bundle missing the OR-Tools native lib. |
| `drift` | `macos-latest`, **scheduled only**, non-blocking | Installs the **loose** `requirements.txt` and reruns the suite, surfacing when upstream releases drift away from the lock. Advisory: it never gates a PR. |

**Why macOS for the test job.** `app.py` imports `webview` (pywebview) at module
load and the suite imports `app`. macOS provides the Cocoa/WebKit backend used by
the only verified build platform, so the suite imports cleanly without installing
a GUI toolkit. A Linux/Windows test runner would need a GTK or Qt backend; that
is intentionally out of scope. The `lint` and `build-verify` jobs do not import
the app, so they run cheaply on Linux.

### `.github/workflows/live-tests.yml` — opt-in live tests

`workflow_dispatch` + a weekly schedule only (**never** on push/PR). Runs
`pytest -m live`, which hits the real public LACCD APIs and, if present, a local
Ollama model (the AI round-trip skips when absent). Day-to-day CI is never gated
on network access.

### `.github/workflows/release-build.yml` — macOS bundle (unsigned)

**Manual dispatch only.** Installs the deps from `requirements.lock`
(`--require-hashes`), installs the BUILD.md-verified PyInstaller, runs
`scripts/build_macos.sh`, verifies the bundle with `scripts/verify_macos_build.sh`,
and uploads `dist/SchedulePlanner.app` as an artifact.

> This workflow has not yet been run on a GitHub-hosted runner — validate it with
> one manual dispatch before wiring it to release tags. It wraps the same build +
> verify scripts that were green on the dev host (see BUILD.md and
> `docs/M8_QA_REPORT.md`).

**What CI does not claim:** no code signing or notarization (the `.app` is
unsigned — Gatekeeper blocks it on first open; see BUILD.md "macOS Gatekeeper
bypass"); no Windows/Linux build (manual, per `docs/CROSS_PLATFORM_BUILD.md`); no
bit-for-bit identical binaries (only the dependency set is pinned).

## Reproducible dependency install

`requirements.txt` keeps **loose** lower bounds (`pandas>=2.0`, …) for convenient
local development — its behaviour is unchanged. `requirements.lock` is the
**fully pinned, hashed** resolution of that same set, generated with `uv` as a
cross-platform (`--universal`) lockfile that plain `pip` can install:

```bash
pip install --require-hashes -r requirements.lock
```

`--require-hashes` refuses to install anything whose artifact hash does not match
the lock, so a reproducible install fails loudly rather than silently picking up
a tampered or newer wheel.

### Committed fixtures are pinned to the lock

The synthetic IR enrollment fixture `files/lamc_sample_enrollment.xlsx` is checked
for **byte-identical** regeneration (`tests/test_sample_enrollment_fixture.py`).
Because `.xlsx` serialization depends on the writer libraries (openpyxl/pandas),
that fixture is byte-reproducible **against the locked dependency set**. When you
bump the lock, regenerate the fixture in the same step:

```bash
pip install --require-hashes -r requirements.lock
python3 generate_synthetic.py --enrollment-sample --out files/lamc_sample_enrollment.xlsx
```

The engine's *logical* output on the fixture is unchanged by a regeneration (the
snapshot tests pin the planted bottlenecks); only the serialized bytes refresh.

## Regenerating the lockfile

The lock was generated with [`uv`](https://docs.astral.sh/uv/) (universal,
hash-pinned, floor Python 3.12):

```bash
uv pip compile requirements.txt \
  -o requirements.lock \
  --generate-hashes \
  --universal \
  --python-version 3.12
```

The exact command is recorded in the header of `requirements.lock`. After
regenerating: review the diff (expect only the intended version changes),
reinstall locally, run `bash scripts/run_qa.sh`, and — if the bump moved the
xlsx serializer libs — regenerate the committed fixture as shown above. Commit
`requirements.lock` (and any regenerated fixture) together in the same change.

> No `uv`? `pip-tools` produces an equivalent file:
> `pip-compile --generate-hashes --output-file=requirements.lock requirements.txt`
> (single-platform; `uv --universal` is preferred for the macOS/Windows/Linux
> release matrix).
