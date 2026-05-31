# Continuous integration & reproducible installs

CI runs on GitHub Actions (`.github/workflows/ci.yml`) and the repo ships a
pinned, hash-checked dependency lockfile so installs are reproducible across
machines and over time.

## TL;DR

| Need | Command |
|---|---|
| Local dev (loose, convenient) | `pip install -r requirements.txt` |
| Reproducible / CI install | `pip install --require-hashes -r requirements.lock` |
| Run the offline suite locally | `python3 -m pytest -m "not live"` |
| Run the live (network) tests | `python3 -m pytest -m live -rs` (overrides the default `-m "not live"`) |

## `.github/workflows/ci.yml`

| Job | Runner | What it does |
|---|---|---|
| `test` | `macos-latest`, Python 3.12 & 3.13 | Installs `requirements.lock` with `--require-hashes`, then runs the offline suite (`pytest -m "not live"`). This is the reproducible green gate. |
| `build-verify` | `ubuntu-latest` | `scripts/verify_macos_build.sh --self-test` — the portable negative control proving the build resource checker fails on a bundle missing the OR-Tools native lib. |

**Why macOS for the test job.** `app.py` imports `webview` (pywebview) at module
load and the suite imports `app`; macOS provides the Cocoa/WebKit backend the
lock targets (the `pyobjc-*` wheels carry `sys_platform == 'darwin'` markers), so
the suite imports cleanly without a GUI toolkit. A Linux/Windows test runner would
need a GTK/Qt backend (out of scope). The `build-verify` job does not import the
app, so it runs cheaply on Linux.

Live/network tests are deselected by default via `pytest.ini`
(`addopts = -m "not live"`), so PRs are never gated on network access.

## Reproducible dependency install

`requirements.txt` keeps **loose** lower bounds (`pandas>=2.0`, …) for convenient
local development — its behaviour is unchanged. `requirements.lock` is the
**pinned, hashed** resolution of that same set, generated with `uv` as a
cross-platform (`--universal`) lockfile that plain `pip` can install:

```bash
pip install --require-hashes -r requirements.lock
```

`--require-hashes` refuses to install anything whose artifact hash does not match
the lock, so a reproducible install fails loudly rather than silently picking up
a newer or tampered wheel.

**Verified:** `pip install --require-hashes -r requirements.lock` then the offline
suite → `217 passed, 3 deselected` on Python 3.12 and 3.13 (the 3 deselected are
the `live`-marked tests).

## Regenerating the lockfile

```bash
uv pip compile requirements.txt \
  --generate-hashes --universal --python-version 3.12 \
  -o requirements.lock
```

The exact command is recorded in the header of `requirements.lock`. After
regenerating: review the diff (expect only the intended version changes),
reinstall locally, and run the suite. Commit `requirements.lock` in the same
change.

> No `uv`? `pip-tools` produces an equivalent file:
> `pip-compile --generate-hashes -o requirements.lock requirements.txt`
> (single-platform; `uv --universal` is preferred for the macOS/Windows/Linux
> target matrix).
