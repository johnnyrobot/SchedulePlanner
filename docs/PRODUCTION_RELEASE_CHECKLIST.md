# SchedulePlanner — Production Release Checklist

The authoritative, count-agnostic checklist for distributing **SchedulePlanner**
(the EdgeSched desktop app: `app.py` pywebview shell + `ui.html` over the
deterministic OR-Tools `engine.py`, shipping the demo workbook
`files/lamc_data.xlsx`; the local Ollama AI layer in `llm_assist.py` is optional
and the app runs fully without it, with no telemetry).

This document does **not** duplicate the build mechanics — it orchestrates the
existing pieces into a release. Read alongside:

- **`BUILD.md`** — the verified macOS build, flag-by-flag rationale, the frozen
  native-stack smoke test, and the Gatekeeper-bypass instructions for internal
  testers.
- **`docs/CROSS_PLATFORM_BUILD.md`** — Windows/Linux recipes, the `--add-data`
  separator gotcha (`;` on Windows, `:` on macOS/Linux), per-OS pywebview
  backends (WKWebView / WebView2 / GTK-Qt), and the shared resource verifier.
- **`docs/MANUAL_QA_RUNBOOK.md`** — the manual GUI / live-fetch / Ollama test
  pass that automation cannot reach.

## Two tracks (do not conflate them)

| Track | Audience | Signing | Credentials needed | Status today |
|---|---|---|---|---|
| **INTERNAL** | dev team / internal testers | **UNSIGNED**, un-notarized | none | **Supported today** (macOS built + verified; Win/Linux unbuilt) |
| **PUBLIC** | end users / external distribution | macOS **signed + notarized + stapled**; Windows **Authenticode-signed** | external (NOT in this repo) | **Not yet performed** — no credentials present |

The **INTERNAL** track is the existing, default path and is **not changed** by
anything here. The **PUBLIC** track adds two *separate, opt-in* steps
(`scripts/sign_notarize_macos.sh`, `scripts/sign_windows.ps1`) that run **after**
the build and never modify or replace the unsigned build.

**Public-release order, always:** build -> sign + notarize -> package ->
checksum -> manual GUI checklist.

---

## Current status (honest)

- [x] **macOS unsigned build** — built **and** verified on the dev host
      (PyInstaller 6.20.0, Python 3.13, ortools 9.15, arm64). `dist/SchedulePlanner.app`
      exists; resources + frozen native stack verified (see `BUILD.md`).
- [ ] **macOS signing / notarization** — **NOT performed.** No Apple Developer
      credentials exist in this repo. `scripts/sign_notarize_macos.sh` and
      `packaging/entitlements.mac.plist` are prepared but **unverified**; the
      entitlements are a **TEMPLATE** to validate on a signing-capable Mac.
- [ ] **Windows build / signing** — **unbuilt, unverified.** Recipe and scripts
      exist (`scripts/build_windows.ps1`, `scripts/sign_windows.ps1`,
      `scripts/package_release.ps1`); must be run on Windows.
- [ ] **Linux build** — **unbuilt, unverified.** Recipe + scripts exist
      (`scripts/build_linux.sh`, `scripts/package_release.sh`); Linux ships
      **unsigned** by design. Must be run on Linux.

> Nothing in this checklist claims a signature or notarization was performed.
> The signing/packaging scripts report the **actual** tool output and **fail
> closed** (exit non-zero) on any failure or missing input.

---

## Shared release conventions

Every packaging artifact follows these (do not diverge):

| Concern | Convention |
|---|---|
| **Version source** | repo-root file **`VERSION`** (one semver line, trimmed); seeded `0.1.0`. Absent -> fallback `0.0.0-dev`. |
| **Artifact name** | `SchedulePlanner-<version>-<os>-<arch>.<ext>` |
| **`<os>`** | `macos` \| `windows` \| `linux` |
| **`<arch>`** | `arm64` (from arm64/aarch64) \| `x64` (from x86_64/AMD64) |
| **`<ext>`** | macOS `zip` via `ditto -c -k --keepParent`; Windows `zip`; Linux `tar.gz` |
| **Output dir** | `dist/release/` (created on demand; under the already-gitignored `dist/`) |
| **Checksums** | single `dist/release/SHA256SUMS`, `<sha256>␠␠<bare-filename>` per line, verifiable with `shasum -a 256 -c SHA256SUMS` |
| **Pre-package gate** | `scripts/verify_build_resources.sh <build-dir>` must pass first; packaging aborts otherwise |

The `dist/` tree (incl. `dist/release/`) is gitignored — release artifacts are
**never** committed.

### Bump VERSION (manual pre-release step)

- [ ] Edit the repo-root **`VERSION`** file to the new semver (e.g. `0.2.0`).
- [ ] Commit it on its own (`git add VERSION`) — this is the single source of
      truth every packaging script reads; nothing else encodes the version.

---

## External requirements (PUBLIC track only — NOT in this repo)

All credentials come from the **environment / keychain at release time** and are
**never committed**. The scripts accept only the placeholder env vars below and
fail closed if any is missing.

### macOS (signed + notarized)

- [ ] **Apple Developer Program** membership.
- [ ] A **Developer ID Application** certificate in the login keychain
      (-> `MAC_SIGN_IDENTITY`, the identity string / SHA-1 from
      `security find-identity -v -p codesigning`).
- [ ] Your **Team ID** (10-char).
- [ ] **notarytool credentials**, one of:
  - [ ] `MAC_NOTARY_PROFILE` — a keychain profile from
        `xcrun notarytool store-credentials` *(preferred)*; **or**
  - [ ] the trio `MAC_NOTARY_APPLE_ID` + `MAC_NOTARY_TEAM_ID` +
        `MAC_NOTARY_PASSWORD` (an **app-specific password**, not your Apple ID
        password).

### Windows (Authenticode)

- [ ] An **OV or EV code-signing certificate** (or an **Azure Trusted Signing**
      account + identity-validated profile as an alternative), exposed as either:
  - [ ] `WIN_SIGN_THUMBPRINT` — a cert already in the Windows cert store; **or**
  - [ ] `WIN_SIGN_PFX` + `WIN_SIGN_PFX_PASSWORD` (PFX path + password from env).
- [ ] `WIN_SIGN_TIMESTAMP_URL` — an **RFC 3161** timestamp server URL (so
      signatures outlive the certificate).
- [ ] The **Windows SDK** signing tools (`signtool.exe`).

### Linux

- No signing credentials. Linux artifacts are distributed **unsigned**; integrity
  is via `SHA256SUMS`.

---

## Pre-release gate (every track, every OS)

Run from the repo root, in a venv with `pip install -r requirements.txt`:

- [ ] **Bump `VERSION`** (above) and confirm `cat VERSION` shows the intended semver.
- [ ] **Full offline test suite:** `python3 -m pytest -q`
  - Expected: a summary line like **`<N> passed, 3 deselected`** with no
    `failed` / `error`. The passed count grows as tests are added — only
    failures/errors matter; the **3 deselected** are the network `live` tests.
- [ ] **QA gate:** `./scripts/run_qa.sh`
  - Expected tail: **`QA gate PASS (live deselected: 3)`** (exit 0).
- [ ] **Clean tree of artifacts:** confirm `git status --short` shows no
      `dist/`, `build/`, `*.spec`, `*.log`, or `.claude/` staged (all gitignored).
- [ ] **Decide the track** (INTERNAL or PUBLIC) and, for PUBLIC, confirm every
      external requirement above is in hand.

Do **not** proceed past a failing gate.

---

## Track A — INTERNAL release (unsigned; supported today)

The default path for internal testers. No credentials required.

### A.1 macOS (built + verified today)

- [ ] Pre-release gate passed (above).
- [ ] **Build:** `./scripts/build_macos.sh` -> `dist/SchedulePlanner.app`.
- [ ] **Verify resources + Mach-O + negative control:** `./scripts/verify_macos_build.sh`
      (ends `ALL HEADLESS CHECKS PASSED`).
- [ ] **Package:** `./scripts/package_release.sh`
  - Produces `dist/release/SchedulePlanner-<version>-macos-<arch>.zip` and
    refreshes `dist/release/SHA256SUMS`.
  - The script's **SIGNED-STATE** line will read **AD-HOC SIGNED** (PyInstaller
    ad-hoc-signs the bundle on Apple Silicon: `Signature=adhoc`, no Developer ID,
    `spctl … rejected`) — expected for an internal build; it is **not** a
    distributable signature.
- [ ] **Verify checksums:** `( cd dist/release && shasum -a 256 -c SHA256SUMS )`
      (every line `OK`).
- [ ] **Tell testers how to bypass Gatekeeper** (the bundle is unsigned): right-click
      -> **Open**, or `xattr -dr com.apple.quarantine /path/to/SchedulePlanner.app`.
      See `BUILD.md` -> *macOS Gatekeeper bypass*.
- [ ] **Manual GUI checklist** (below) on a macOS desktop session.

### A.2 Windows (unbuilt here — run on Windows)

- [ ] Pre-release gate passed.
- [ ] **Build (on Windows):** `.\scripts\build_windows.ps1` ->
      `dist\SchedulePlanner\SchedulePlanner.exe`.
- [ ] **Verify resources:** `./scripts/verify_build_resources.sh dist/SchedulePlanner`
      (Git Bash / WSL).
- [ ] **Package:** `.\scripts\package_release.ps1` ->
      `dist\release\SchedulePlanner-<version>-windows-<arch>.zip` (SIGNED-STATE: UNSIGNED).
- [ ] **Verify checksums:** `( cd dist/release && shasum -a 256 -c SHA256SUMS )`.
- [ ] **WebView2 note:** internal testers need the **WebView2 Runtime** (ships
      with Win11 & current Win10; else install the Evergreen WebView2 Runtime) —
      a blank window means it is missing. See `docs/CROSS_PLATFORM_BUILD.md`.
- [ ] **Manual GUI checklist** on a Windows desktop session.

### A.3 Linux (unbuilt here — run on Linux; always unsigned)

- [ ] Pre-release gate passed.
- [ ] **Install a pywebview backend first** (GTK or Qt — see
      `docs/CROSS_PLATFORM_BUILD.md`), else the app raises at startup.
- [ ] **Build (on Linux):** `./scripts/build_linux.sh` ->
      `dist/SchedulePlanner/SchedulePlanner`.
- [ ] **Verify resources:** `./scripts/verify_build_resources.sh dist/SchedulePlanner`.
- [ ] **Package:** `./scripts/package_release.sh` ->
      `dist/release/SchedulePlanner-<version>-linux-<arch>.tar.gz`.
- [ ] **Verify checksums:** `( cd dist/release && shasum -a 256 -c SHA256SUMS )`.
- [ ] **Manual GUI checklist** on a Linux desktop session.

---

## Track B — PUBLIC production release

Everything in Track A **plus** the opt-in signing step **before** packaging.
Requires the external credentials above; the scripts fail closed without them.

### B.1 macOS — signed + notarized + stapled (run on macOS)

- [ ] Pre-release gate passed; all **macOS external requirements** in hand.
- [ ] **Build:** `./scripts/build_macos.sh` (the *same* unsigned build — signing
      runs on top of it; the internal build is untouched).
- [ ] **Verify the build:** `./scripts/verify_macos_build.sh`.
- [ ] **Export credentials in this shell** (values from your env/keychain only — never committed):
  ```bash
  export MAC_SIGN_IDENTITY="<Developer ID Application identity>"
  export MAC_NOTARY_PROFILE="<notarytool keychain profile>"   # preferred
  # --- OR the fallback trio instead of MAC_NOTARY_PROFILE ---
  # export MAC_NOTARY_APPLE_ID="<apple-id-email>"
  # export MAC_NOTARY_TEAM_ID="<team-id>"
  # export MAC_NOTARY_PASSWORD="<app-specific-password>"
  ```
- [ ] **Sign + notarize + staple:** `./scripts/sign_notarize_macos.sh`
  - Signs nested dylibs/frameworks **inner-first** (deepest-first, NOT
    `codesign --deep`), then the `.app`, with `--options runtime --timestamp`
    and `packaging/entitlements.mac.plist`.
  - Submits to Apple with `xcrun notarytool ... --wait` (a rejected/failed
    submission aborts), then `xcrun stapler staple` + `validate`.
  - **Validate the entitlements template** on this signing-capable Mac: if the
    signed app crashes on launch or when the solver runs, revisit
    `packaging/entitlements.mac.plist` (the library-validation / JIT entitlements).
- [ ] **Package the signed bundle:** `./scripts/package_release.sh`
  - SIGNED-STATE must report **SIGNED** with an `Authority`/`TeamIdentifier`.
  - Confirm stapling: `xcrun stapler validate dist/SchedulePlanner.app`.
  - `ditto -c -k --keepParent` preserves the bundle **and its signature** in the zip.
- [ ] **Verify checksums:** `( cd dist/release && shasum -a 256 -c SHA256SUMS )`.
- [ ] **Manual GUI checklist** — additionally confirm a **double-click launch
      with NO Gatekeeper prompt** (a properly notarized + stapled app opens
      without the "Apple cannot check it" dialog, even offline).

### B.2 Windows — Authenticode-signed (run on Windows)

- [ ] Pre-release gate passed; all **Windows external requirements** in hand.
- [ ] **Build:** `.\scripts\build_windows.ps1`.
- [ ] **Verify resources:** `./scripts/verify_build_resources.sh dist/SchedulePlanner`.
- [ ] **Export credentials in this shell** (env / cert store only):
  ```powershell
  $env:WIN_SIGN_TIMESTAMP_URL = "<rfc3161-timestamp-url>"
  $env:WIN_SIGN_THUMBPRINT    = "<cert-thumbprint-in-store>"   # scheme A
  # --- OR scheme B (PFX) instead of the thumbprint ---
  # $env:WIN_SIGN_PFX          = "<path-to.pfx>"
  # $env:WIN_SIGN_PFX_PASSWORD = "<pfx-password>"
  ```
  *(Azure Trusted Signing is the cert-less alternative — see
  `scripts/sign_windows.ps1` header.)*
- [ ] **Sign:** `.\scripts\sign_windows.ps1`
  - SHA-256 digest + RFC 3161 timestamp, then `signtool verify /pa /v` (fails
    closed if the signature is not valid).
- [ ] **Package the signed build:** `.\scripts\package_release.ps1`
  - SIGNED-STATE must report **SIGNED** (Authenticode `Valid`).
- [ ] **Verify checksums:** `( cd dist/release && shasum -a 256 -c SHA256SUMS )`.
- [ ] **Manual GUI checklist** on Windows (incl. the WebView2 runtime note).

> **Linux has no public-signing step** — distribute the unsigned `tar.gz` from
> Track A.3 with its `SHA256SUMS`.

---

## Manual GUI checklist (per OS — cannot be automated headlessly)

Launch the **packaged** app on an interactive desktop session and confirm. This
mirrors `docs/CROSS_PLATFORM_BUILD.md` and `docs/MANUAL_QA_RUNBOOK.md` — run the
full runbook for a public release.

- [ ] App launches with **no console/terminal window** and shows a native window.
- [ ] `ui.html` renders the **Choose data file** and **Load demo data** buttons.
- [ ] **Load demo data** runs analysis on the bundled workbook and renders
      full-time and part-time cohort results — **with no file picking**.
- [ ] **Choose data file** opens the OS-native file picker and accepts an `.xlsx`.
- [ ] Missing Ollama does **not** block analysis (the AI layer is optional;
      results still render, just without the AI explanation). No telemetry.
- [ ] The app exits cleanly.
- [ ] **macOS PUBLIC only:** double-clicking the notarized + stapled app opens
      with **no Gatekeeper prompt** (test offline too).
- [ ] **Windows:** a blank window means the WebView2 Runtime is missing.
- [ ] **Linux:** a startup crash about a missing web view means no GTK/Qt backend.

Launch commands:

```bash
# macOS
open dist/SchedulePlanner.app
# or for logs: ./dist/SchedulePlanner.app/Contents/MacOS/SchedulePlanner
```
```powershell
# Windows
.\dist\SchedulePlanner\SchedulePlanner.exe
```
```bash
# Linux
./dist/SchedulePlanner/SchedulePlanner
```

---

## Security notes

- **No secrets are committed.** All signing/notarization credentials come from
  **environment variables / keychain profiles** at release time only. This repo
  contains no Apple IDs, Team IDs, certificate identities, passwords,
  app-specific passwords, PFX files, thumbprints, or API keys — only the
  documented placeholder env-var **names**.
- **Scripts fail closed.** If a required credential or tool is missing, the
  signing/packaging scripts print exactly which inputs and external
  prerequisites are missing and exit non-zero. They never fabricate a signature
  and never claim notarization the Apple tools did not report.
- **The unsigned internal build remains the default.** Signing and packaging are
  separate, opt-in, post-build steps. They run on top of `scripts/build_macos.sh`
  (and the Windows/Linux builds) and never modify or replace the unsigned build
  documented in `BUILD.md`.
- **macOS hardened-runtime entitlements are a TEMPLATE.** `packaging/entitlements.mac.plist`
  grants six keys: `com.apple.security.cs.disable-library-validation` (needed for
  the bundled, unsigned OR-Tools/pandas/numpy/WebKit dylibs), the executable-memory
  / JIT pair (`allow-unsigned-executable-memory`, `allow-jit`),
  `allow-dyld-environment-variables`, `network.client` (optional LACCD/Ollama
  fetches), and `files.user-selected.read-only` (the file picker). Each is
  annotated in the plist with why it may be needed; **validate and minimize** them
  on a signing-capable Mac (drop any the notarized app runs without).
- **No telemetry / offline engine.** The engine is deterministic and offline;
  the only network paths are optional public-source fetches and optional local
  Ollama calls to `localhost`. Packaging changes none of this.
- **Release artifacts stay gitignored.** Everything lands under `dist/release/`;
  never commit `dist/`, `build/`, `*.spec`, or `*.log`.

---

## Artifacts produced by a release

```text
dist/release/
  SchedulePlanner-<version>-macos-<arch>.zip      # macOS (ditto --keepParent; signed for PUBLIC)
  SchedulePlanner-<version>-windows-<arch>.zip    # Windows (Authenticode-signed for PUBLIC)
  SchedulePlanner-<version>-linux-<arch>.tar.gz   # Linux (always unsigned)
  SHA256SUMS                                      # one `<sha256>  <filename>` line per artifact
```

Publish only the artifacts you actually built and verified on each OS, alongside
`SHA256SUMS`. For a PUBLIC macOS release, the published zip must contain the
**notarized + stapled** `.app`.

## See also

- `BUILD.md` — verified macOS build + Gatekeeper bypass.
- `docs/CROSS_PLATFORM_BUILD.md` — Windows/Linux recipes + per-OS backends.
- `docs/MANUAL_QA_RUNBOOK.md` — full manual GUI / live / Ollama test pass.
- `scripts/sign_notarize_macos.sh`, `scripts/sign_windows.ps1` — opt-in signing.
- `scripts/package_release.sh`, `scripts/package_release.ps1` — packaging + checksums.
- `packaging/entitlements.mac.plist` — hardened-runtime entitlements TEMPLATE.
