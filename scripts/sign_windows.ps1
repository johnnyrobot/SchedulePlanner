# sign_windows.ps1 — Authenticode-sign a COPY of the built SchedulePlanner
# Windows binaries, leaving the unsigned internal tester build untouched.
#
# OPT-IN, POST-BUILD step. RUN THIS ON WINDOWS, AFTER scripts/build_windows.ps1.
#
# SEPARATION OF CONCERNS / DO-NOT-MODIFY-THE-BUILD
#   This script does NOT sign dist\SchedulePlanner\ in place. signtool rewrites
#   each PE in place, which would destroy the UNSIGNED internal tester artifact
#   that scripts/build_windows.ps1 produced. Instead this script REPLICATES the
#   one-dir build into a separate signed-staging directory
#   (default: dist\release\stage\SchedulePlanner-signed\) and signs THAT copy.
#   The original unsigned dist\SchedulePlanner\SchedulePlanner.exe — the
#   documented internal tester artifact — is never touched, rebuilt, or replaced.
#   Packaging (zip) + checksum run as separate later steps against the signed
#   staging dir. Public-release order: build -> sign -> package -> checksum.
#
# It was authored (but NOT executed) on the macOS dev host: there is no signtool,
# no Windows SDK, and no code-signing certificate here. Treat it as the
# documented signing recipe to run on a signing-capable Windows host. Nothing in
# here is verified on the dev host.
#
# WHAT IT DOES
#   1. Pre-package GATE: runs scripts/verify_build_resources.sh against the build
#      dir (via Git Bash / WSL bash) and aborts if any required resource is
#      missing. (Skippable only with an explicit -SkipResourceCheck for hosts
#      with no bash; the operator then owns running it manually.)
#   2. Locates signtool.exe (Windows SDK) and fails clearly if it is absent.
#   3. Reads the signing credential from the environment (never hardcoded):
#        - WIN_SIGN_THUMBPRINT          a cert SHA1 thumbprint already in the
#                                       Windows certificate store (uses /sha1), OR
#        - WIN_SIGN_PFX + WIN_SIGN_PFX_PASSWORD
#                                       a PFX file path + its password (uses /f /p).
#      Exactly one of these two modes must be supplied.
#   4. Requires WIN_SIGN_TIMESTAMP_URL — an RFC 3161 timestamp server URL — so the
#      signature keeps verifying after the signing cert expires.
#   5. COPIES the build dir to a signed-staging dir, then signs all signable
#      binaries in the COPY (*.exe, *.dll, *.pyd) with SHA-256 (/fd sha256) and an
#      RFC 3161 timestamp (/tr <url> /td sha256), inner-first (dlls/pyds before
#      the primary EXE).
#   6. Verifies every signed file with: signtool verify /pa /v.
#   7. Prints the REAL signtool output and reports SIGNED / UNSIGNED accordingly.
#      It NEVER fabricates a signature and NEVER claims notarization (that is an
#      Apple/macOS concept and does not apply to Windows).
#
# EXIT CODES: 0 only if every targeted binary signed AND verified. Non-zero on a
# missing tool, a missing resource, a missing/contradictory credential, or any
# signtool failure.
#
# ---------------------------------------------------------------------------
# KEY MANAGEMENT (read before you sign a public release)
# ---------------------------------------------------------------------------
# Public-trust code-signing private keys may NO LONGER live in a bare .pfx on
# disk. Since the CA/Browser Forum's 2023 key-storage requirement, publicly
# trusted code-signing certificates (OV and EV) must be generated and kept on
# FIPS 140-2/140-3 Level 2+ hardware: a hardware token / smartcard (e.g. a
# YubiKey or a CA-issued eToken) or a cloud HSM-backed signing service. A loose
# WIN_SIGN_PFX + WIN_SIGN_PFX_PASSWORD is therefore ONLY appropriate for an
# INTERNAL / TEST certificate authority (e.g. a self-signed or enterprise-PKI
# cert that you control and distribute internally). It will NOT chain to public
# trust on machines outside your org. For a public release use a hardware token
# (its cert appears in the Windows store — sign by WIN_SIGN_THUMBPRINT) or a
# cloud signing service such as Azure Trusted Signing (below).
#
# IMPORTANT — `signtool verify /pa` AND INTERNAL/TEST CERTS: step 6 uses the
# Default Authentication Verification Policy (/pa), which requires the signing
# cert to chain to a root TRUSTED on THIS machine. A self-signed or internal-PKI
# cert that is NOT installed in the local Trusted Root / enterprise trust store
# will FAIL /pa verification *even though the file was signed correctly*. That
# failure means "this machine does not trust the chain", NOT "signing failed".
# For an internal/test cert, install its root into the machine's trust store
# before running, or expect (and interpret) the /pa failure accordingly. This
# script deliberately fails closed on any verify failure rather than guess.
#
# AZURE TRUSTED SIGNING ALTERNATIVE (recommended for public releases)
# Azure Trusted Signing is Microsoft's managed signing service: keys are held in
# an Azure-managed HSM, you authenticate with an Azure identity, and short-lived
# certs are minted per request — no PFX, no thumbprint, no local private key.
# To use it instead of this script's signtool path, install the Trusted Signing
# dlib and invoke signtool with the Azure dlib provider, roughly:
#
#   signtool sign /v /fd SHA256 /tr <RFC3161-URL> /td SHA256 `
#     /dlib "<path>\Azure.CodeSigning.Dlib.dll" `
#     /dmdf "<path>\metadata.json" `   # holds Endpoint, CodeSigningAccountName, CertificateProfileName
#     <signed-staging-dir>\SchedulePlanner.exe
#
# or via the `Invoke-TrustedSigning` PowerShell module / the `azuresigntool`
# wrapper. The Azure account name, profile name, and endpoint come from your
# Azure Trusted Signing resource and your Azure login — NEVER hardcode them here;
# put them in the metadata.json the dlib reads, or supply them via environment.
# See Microsoft's "Trusted Signing" docs for current setup. This script does not
# embed any Azure identifiers.
#
# Usage (PowerShell, on Windows, after build):
#   $env:WIN_SIGN_THUMBPRINT   = "<cert thumbprint in the Windows store>"
#   $env:WIN_SIGN_TIMESTAMP_URL= "<your RFC 3161 timestamp server URL>"
#   .\scripts\sign_windows.ps1
#     # or PFX mode (internal/test CA only):
#   $env:WIN_SIGN_PFX          = "<path to your .pfx>"
#   $env:WIN_SIGN_PFX_PASSWORD = "<pfx password>"
#   $env:WIN_SIGN_TIMESTAMP_URL= "<your RFC 3161 timestamp server URL>"
#   .\scripts\sign_windows.ps1
#
#   .\scripts\sign_windows.ps1 -BuildDir dist\SchedulePlanner   # explicit build dir
#   .\scripts\sign_windows.ps1 -SkipResourceCheck               # no bash available
#
# After signing, package + checksum the SIGNED STAGING DIR as the next release
# steps (separate scripts). The unsigned dist\SchedulePlanner\ is left intact.

[CmdletBinding()]
param(
    # The one-dir build directory produced by scripts\build_windows.ps1. This dir
    # is READ ONLY here — it is copied, never signed in place.
    [string]$BuildDir = "dist\SchedulePlanner",

    # Where to place the signed COPY. Created/overwritten on demand.
    [string]$SignedDir = "dist\release\stage\SchedulePlanner-signed",

    # Skip the bash-based resource gate (only when no Git Bash / WSL is present;
    # the operator must then run scripts/verify_build_resources.sh manually).
    [switch]$SkipResourceCheck
)

$ErrorActionPreference = "Stop"

# Resolve repo root = parent of the dir containing this script (matches
# scripts/build_windows.ps1).
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

# --- helpers ---------------------------------------------------------------

function Fail([string]$Message) {
    # Write a clear, actionable error and exit non-zero. Never proceed past a
    # missing input or a failed tool — fail closed. Use Write-Host (not
    # Write-Error) so the message is not obscured by a terminating-error trace
    # under $ErrorActionPreference = 'Stop'.
    Write-Host $Message -ForegroundColor Red
    exit 1
}

# Locate signtool.exe. It is part of the Windows SDK and is usually NOT on PATH.
# Try PATH first, then the standard SDK install roots, preferring the newest
# version and the host architecture's build.
function Find-SignTool {
    $onPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    $roots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "${env:ProgramFiles}\Windows Kits\10\bin",
        "${env:ProgramFiles(x86)}\Windows Kits\8.1\bin",
        "${env:ProgramFiles}\Windows Kits\8.1\bin"
    ) | Where-Object { $_ -and (Test-Path $_) }

    # Prefer the host architecture's signtool, then fall back to any.
    $archPref = switch ($env:PROCESSOR_ARCHITECTURE) {
        "ARM64" { @("arm64", "x64", "x86") }
        "AMD64" { @("x64", "x86", "arm64") }
        default { @("x86", "x64", "arm64") }
    }

    foreach ($root in $roots) {
        $candidates = Get-ChildItem -Path $root -Filter "signtool.exe" -Recurse -ErrorAction SilentlyContinue
        if (-not $candidates) { continue }
        # Sort newest-SDK-version first by the version folder in the path.
        $sorted = $candidates | Sort-Object {
            $m = [regex]::Match($_.FullName, "\\10\.0\.(\d+)\.\d+\\")
            if ($m.Success) { [int]$m.Groups[1].Value } else { 0 }
        } -Descending
        foreach ($arch in $archPref) {
            $hit = $sorted | Where-Object { $_.FullName -match "\\$arch\\" } | Select-Object -First 1
            if ($hit) { return $hit.FullName }
        }
        # No arch-qualified path matched; take the newest found.
        return ($sorted | Select-Object -First 1).FullName
    }
    return $null
}

# Locate a bash to run the OS-agnostic resource check. Prefer Git Bash / WSL.
function Find-Bash {
    $onPath = Get-Command bash.exe -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    $candidates = @(
        "${env:ProgramFiles}\Git\bin\bash.exe",
        "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
        "${env:WINDIR}\System32\bash.exe"
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}

# --- 1. locate + validate the build (READ ONLY) ----------------------------

if (-not (Test-Path -LiteralPath $BuildDir -PathType Container)) {
    Fail @"
Build directory '$BuildDir' not found under $RepoRoot.
Build first on Windows:
  .\scripts\build_windows.ps1
then re-run this signing step. Signing is a separate, post-build step; it never
builds or replaces the unsigned build.
"@
}

$PrimaryExeSrc = Join-Path $BuildDir "SchedulePlanner.exe"
if (-not (Test-Path -LiteralPath $PrimaryExeSrc)) {
    Fail "Expected '$PrimaryExeSrc' not found. Did scripts\build_windows.ps1 succeed? The primary EXE must exist before signing."
}

# --- 2. PRE-PACKAGE GATE: verify bundled resources --------------------------

if ($SkipResourceCheck) {
    Write-Host "WARNING: -SkipResourceCheck set. You MUST run scripts/verify_build_resources.sh '$BuildDir' yourself." -ForegroundColor Yellow
} else {
    $bash = Find-Bash
    if (-not $bash) {
        Fail @"
Could not find bash to run the resource gate
(scripts/verify_build_resources.sh). Install Git for Windows (Git Bash) or WSL,
or re-run with -SkipResourceCheck and run the check manually:
  ./scripts/verify_build_resources.sh "$BuildDir"
"@
    }
    # verify_build_resources.sh takes a POSIX-style dist path; pass the build dir.
    $bashPath = ($BuildDir -replace '\\', '/')
    Write-Host "Resource gate: $bash scripts/verify_build_resources.sh $bashPath"
    & $bash "scripts/verify_build_resources.sh" $bashPath
    if ($LASTEXITCODE -ne 0) {
        Fail "Resource gate FAILED for '$BuildDir' (exit $LASTEXITCODE). Required bundle resources are missing — do NOT sign or ship. See output above."
    }
}

# --- 3. signtool ------------------------------------------------------------

$SignTool = Find-SignTool
if (-not $SignTool) {
    Fail @"
signtool.exe not found.
signtool ships with the Windows SDK / Windows App Certification Kit. Install the
'Windows SDK Signing Tools for Desktop Apps' component (via the Windows SDK or
Visual Studio Installer), then re-run. Searched PATH and the Windows Kits bin
folders under Program Files.
"@
}
Write-Host "Using signtool: $SignTool"

# --- 4. credential selection (env only, never hardcoded) --------------------

$thumb  = $env:WIN_SIGN_THUMBPRINT
$pfx    = $env:WIN_SIGN_PFX
$pfxPwd = $env:WIN_SIGN_PFX_PASSWORD
$tsUrl  = $env:WIN_SIGN_TIMESTAMP_URL

$haveThumb = -not [string]::IsNullOrWhiteSpace($thumb)
$havePfx   = -not [string]::IsNullOrWhiteSpace($pfx)

if ($haveThumb -and $havePfx) {
    Fail @"
Both WIN_SIGN_THUMBPRINT and WIN_SIGN_PFX are set — choose exactly ONE signing
mode:
  - store cert : set WIN_SIGN_THUMBPRINT only (the cert/key is in the Windows
                 store or on a hardware token), or
  - PFX file   : set WIN_SIGN_PFX + WIN_SIGN_PFX_PASSWORD (internal/test CA only;
                 a bare PFX is NOT valid for public-trust signing).
Unset one of them and re-run.
"@
}

if (-not $haveThumb -and -not $havePfx) {
    Fail @"
No signing credential supplied. Set ONE of these (values come from your
environment / cert store — this script never hardcodes them):
  - WIN_SIGN_THUMBPRINT       : SHA1 thumbprint of a code-signing cert already in
                                the Windows certificate store (or on a connected
                                hardware token). Recommended for public releases.
  - WIN_SIGN_PFX (+ WIN_SIGN_PFX_PASSWORD)
                              : path to a .pfx and its password. INTERNAL/TEST CA
                                ONLY — public-trust keys must be on FIPS-140
                                hardware or a cloud signing service (see header).
In all modes you must also set WIN_SIGN_TIMESTAMP_URL (an RFC 3161 server).
For a public release, prefer Azure Trusted Signing (see this script's header).
"@
}

if ($havePfx -and [string]::IsNullOrWhiteSpace($pfxPwd)) {
    Fail "WIN_SIGN_PFX is set but WIN_SIGN_PFX_PASSWORD is empty. Set the PFX password in the environment (never on the command line or in a file)."
}

if ($havePfx -and -not (Test-Path -LiteralPath $pfx)) {
    Fail "WIN_SIGN_PFX points to '$pfx', which does not exist. Set WIN_SIGN_PFX to a readable .pfx path."
}

if ([string]::IsNullOrWhiteSpace($tsUrl)) {
    Fail @"
WIN_SIGN_TIMESTAMP_URL is not set. Set it to an RFC 3161 timestamp server URL so
the signature stays valid after the signing certificate expires. Use the
timestamp URL provided by your CA / signing service. Supply your own; this
script does not embed one.
"@
}

if ($haveThumb) {
    Write-Host "Signing mode: certificate store (WIN_SIGN_THUMBPRINT, /sha1)."
} else {
    Write-Host "Signing mode: PFX file (WIN_SIGN_PFX, /f /p). NOTE: PFX is for internal/test CAs only — see header for public-trust key requirements."
}
Write-Host "Timestamp (RFC 3161): $tsUrl"

# --- 5. replicate the build into the signed-staging dir (do NOT touch src) --

if (Test-Path -LiteralPath $SignedDir) {
    Write-Host "Clearing previous signed staging dir: $SignedDir"
    Remove-Item -LiteralPath $SignedDir -Recurse -Force
}
$parent = Split-Path -Parent $SignedDir
if ($parent -and -not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}
Write-Host "Copying unsigned build '$BuildDir' -> signed staging '$SignedDir' (original left untouched) ..."
Copy-Item -LiteralPath $BuildDir -Destination $SignedDir -Recurse -Force

$PrimaryExe = Join-Path $SignedDir "SchedulePlanner.exe"
if (-not (Test-Path -LiteralPath $PrimaryExe)) {
    Fail "Copy failed: '$PrimaryExe' missing in the signed staging dir."
}

# Sign every signable PE in the COPY: the bundled dlls/pyds (incl. OR-Tools
# native libs) first, then the primary EXE last (inner-first ordering).
$targets = @()
$targets += Get-ChildItem -LiteralPath $SignedDir -Recurse -File -Include *.dll, *.pyd -ErrorAction SilentlyContinue
$targets += Get-ChildItem -LiteralPath $SignedDir -Recurse -File -Include *.exe -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -ne (Resolve-Path -LiteralPath $PrimaryExe).Path }
$targets += (Get-Item -LiteralPath $PrimaryExe)

# De-duplicate by full path while preserving order.
$seen = New-Object System.Collections.Generic.HashSet[string]
$targets = $targets | Where-Object { $_ -and $seen.Add($_.FullName) }

if (-not $targets -or $targets.Count -eq 0) {
    Fail "No signable binaries (*.exe/*.dll/*.pyd) found under '$SignedDir'."
}

Write-Host ""
Write-Host ("Signing {0} binar{1} in {2} ..." -f $targets.Count, ($(if ($targets.Count -eq 1) { "y" } else { "ies" })), $SignedDir)

# --- 6. build the signtool sign argument list ------------------------------

# Common: SHA-256 file digest + RFC 3161 timestamp with a SHA-256 digest.
$commonArgs = @("/v", "/fd", "sha256", "/tr", $tsUrl, "/td", "sha256")
if ($haveThumb) {
    $credArgs = @("/sha1", $thumb)
} else {
    $credArgs = @("/f", $pfx, "/p", $pfxPwd)
}

# --- 7. sign each target, printing real signtool output ---------------------

$failures = @()
foreach ($t in $targets) {
    Write-Host ""
    Write-Host "--- sign: $($t.FullName)"
    $signArgs = @("sign") + $commonArgs + $credArgs + @($t.FullName)
    & $SignTool @signArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "signtool sign FAILED (exit $LASTEXITCODE) for $($t.FullName)"
        $failures += $t.FullName
    }
}

if ($failures.Count -gt 0) {
    Write-Host ""
    Write-Host "RESULT: UNSIGNED / INCOMPLETE — $($failures.Count) file(s) failed to sign:" -ForegroundColor Red
    $failures | ForEach-Object { Write-Host "  $_" }
    Fail "Signing failed for $($failures.Count) file(s). See signtool output above. No signature is claimed. The unsigned build at '$BuildDir' is unchanged."
}

# --- 8. verify every signed target with signtool verify /pa /v --------------

Write-Host ""
Write-Host "Verifying signatures (signtool verify /pa /v) ..."
Write-Host "(NOTE: /pa requires the cert chain to be trusted on THIS machine. An internal/self-signed cert whose root is not in the local trust store will fail here even if signing succeeded — install its root first or interpret accordingly.)"
$verifyFailures = @()
foreach ($t in $targets) {
    Write-Host ""
    Write-Host "--- verify: $($t.FullName)"
    & $SignTool verify /pa /v $t.FullName
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "signtool verify FAILED (exit $LASTEXITCODE) for $($t.FullName)"
        $verifyFailures += $t.FullName
    }
}

if ($verifyFailures.Count -gt 0) {
    Write-Host ""
    Write-Host "RESULT: UNSIGNED / UNVERIFIED — $($verifyFailures.Count) file(s) failed verification:" -ForegroundColor Red
    $verifyFailures | ForEach-Object { Write-Host "  $_" }
    Fail "Verification failed for $($verifyFailures.Count) file(s). See signtool output above. Do NOT distribute this build. (For an internal/test cert, confirm its root is trusted on this machine before treating this as a signing failure.)"
}

Write-Host ""
Write-Host ("RESULT: SIGNED + VERIFIED — {0} binar{1} in {2} (SHA-256 + RFC 3161 timestamp)." -f $targets.Count, ($(if ($targets.Count -eq 1) { "y" } else { "ies" })), $SignedDir) -ForegroundColor Green
Write-Host "Signed primary binary: $PrimaryExe"
Write-Host "Unsigned internal tester build left intact at: $BuildDir\SchedulePlanner.exe"
Write-Host "Note: this script signs only. It does not notarize (an Apple/macOS concept; not applicable to Windows) and does not package. Package + checksum the signed staging dir as the next release step."
exit 0
