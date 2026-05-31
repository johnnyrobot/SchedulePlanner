# package_release.ps1 — package the built SchedulePlanner Windows one-dir into a
# named, checksummed release artifact.
#
# RUN THIS ON WINDOWS. It is the Windows equivalent of scripts/package_release.sh
# and was authored (but NOT executed) on the macOS dev host — treat it as the
# documented packaging recipe to run on Windows, not something verified here.
#
# This is a SEPARATE, opt-in, post-build step. It does NOT build or modify the
# build produced by scripts/build_windows.ps1 — it only zips what is already in
# dist\SchedulePlanner and records a SHA-256.
#
# Public-release order is:  build  ->  sign  ->  package (this script)
# ->  checksum (this script appends it). Signing is a separate opt-in step that
# runs BEFORE this one; THIS SCRIPT NEVER SIGNS ANYTHING. It only REPORTS whether
# the .exe is already signed (signtool verify) and never claims a signature it
# cannot confirm.
#
# Shared conventions (matched across all packaging artifacts):
#   - VERSION source : repo-root file VERSION (one trimmed semver line).
#                      Missing -> fall back to 0.0.0-dev.
#   - artifact name  : SchedulePlanner-<version>-windows-<arch>.zip
#                        arch = arm64 | x64 (derived from PROCESSOR_ARCHITECTURE:
#                               ARM64->arm64; AMD64/x86->x64)
#   - output dir     : dist\release\  (created on demand; under gitignored dist\)
#   - checksums      : dist\release\SHA256SUMS, one "<sha256>  <filename>" line
#                      per artifact (lowercase hex, two spaces), so that on a
#                      Unix host:  (cd dist/release && shasum -a 256 -c SHA256SUMS)
#
# Pre-package gate: verifies the bundled runtime resources FIRST and ABORTS
# (non-zero) if any is missing. Prefers the shared bash checker
# (scripts/verify_build_resources.sh via Git Bash / WSL); if bash is absent it
# falls back to an inline presence check for ui.html, lamc_data.xlsx, and an
# ortools*.dll.
#
# Usage:
#   .\scripts\package_release.ps1                 # auto-locate dist\SchedulePlanner
#   .\scripts\package_release.ps1 <build-dir>     # override (e.g. a signed copy)

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false, Position = 0)]
    [string]$BuildDir
)

$ErrorActionPreference = "Stop"

# Resolve repo root = parent of the dir containing this script.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

$ReleaseDir = Join-Path $RepoRoot "dist\release"
$SumsFile   = Join-Path $ReleaseDir "SHA256SUMS"
$ResourceCheck = Join-Path $ScriptDir "verify_build_resources.sh"

# --- version (single source of truth) ---------------------------------------
$VersionFile = Join-Path $RepoRoot "VERSION"
if (Test-Path $VersionFile) {
    $Version = (Get-Content -Raw -Path $VersionFile).Trim()
    if ([string]::IsNullOrWhiteSpace($Version)) {
        Write-Error "VERSION file is empty (expected one semver line)."
        exit 1
    }
} else {
    $Version = "0.0.0-dev"
    Write-Warning "no VERSION file at $VersionFile; falling back to $Version."
}

# --- arch (from PROCESSOR_ARCHITECTURE) -------------------------------------
$ProcArch = $env:PROCESSOR_ARCHITECTURE
switch ($ProcArch) {
    "ARM64" { $Arch = "arm64" }
    "AMD64" { $Arch = "x64" }
    "x86"   { $Arch = "x64" }
    default {
        Write-Error "unrecognized PROCESSOR_ARCHITECTURE '$ProcArch' (expected ARM64 or AMD64/x86)."
        exit 1
    }
}
$OS = "windows"

# --- locate the build dir ----------------------------------------------------
if ([string]::IsNullOrWhiteSpace($BuildDir)) {
    $BuildDir = Join-Path $RepoRoot "dist\SchedulePlanner"
    if (-not (Test-Path -PathType Container $BuildDir)) {
        Write-Error ("expected build dir not found: $BuildDir`n" +
                     "       Build it first with: .\scripts\build_windows.ps1")
        exit 1
    }
} else {
    if (-not (Test-Path -PathType Container $BuildDir)) {
        Write-Error "build dir '$BuildDir' does not exist."
        exit 1
    }
}
$BuildDir = (Resolve-Path $BuildDir).Path

Write-Host "=== package_release: $OS-$Arch v$Version ==="

# --- pre-package gate: required runtime resources must be bundled ------------
Write-Host "--- pre-package resource gate ($BuildDir) ---"
$bash = Get-Command bash -ErrorAction SilentlyContinue
if ($bash -and (Test-Path $ResourceCheck)) {
    # Prefer the shared, OS-agnostic checker (Git Bash / WSL).
    & $bash.Source $ResourceCheck $BuildDir
    if ($LASTEXITCODE -ne 0) {
        Write-Error "resource gate FAILED for $BuildDir; refusing to package an incomplete bundle."
        exit 1
    }
} else {
    # Inline fallback: presence check for the three runtime resources by name,
    # mirroring scripts/verify_build_resources.sh (locate by NAME, not a
    # hardcoded sub-path — the PyInstaller layout varies by version).
    Write-Warning "bash/verify_build_resources.sh unavailable; using inline presence check."
    $missing = @()
    if (-not (Get-ChildItem -Path $BuildDir -Recurse -Filter "ui.html" -File -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        $missing += "ui.html"
    } else { Write-Host "PASS: ui.html bundled" }
    if (-not (Get-ChildItem -Path $BuildDir -Recurse -Filter "lamc_data.xlsx" -File -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        $missing += "lamc_data.xlsx"
    } else { Write-Host "PASS: lamc_data.xlsx bundled" }
    if (-not (Get-ChildItem -Path $BuildDir -Recurse -Filter "ortools*.dll" -File -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        $missing += "ortools*.dll"
    } else { Write-Host "PASS: OR-Tools native lib (ortools*.dll) bundled" }
    if ($missing.Count -gt 0) {
        Write-Error ("resource gate FAILED for $BuildDir; missing: " + ($missing -join ", ") +
                     "; refusing to package an incomplete bundle.")
        exit 1
    }
    Write-Host "RESULT: all required resources present in $BuildDir"
}

# --- package (Compress-Archive) ---------------------------------------------
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
$ArtifactName = "SchedulePlanner-$Version-$OS-$Arch.zip"
$Artifact     = Join-Path $ReleaseDir $ArtifactName

Write-Host "--- packaging (Compress-Archive) ---"
if (Test-Path $Artifact) { Remove-Item -Force $Artifact }
# Compress the one-dir BY ITS DIRECTORY (not its contents) so the tree archives
# under its own top-level "SchedulePlanner\" folder — matching how the Linux
# .tar.gz unpacks under its own name and the macOS .zip keeps the .app parent.
# (Passing "$BuildDir\*" here would instead dump the loose contents at the zip
# root, with no wrapping folder; pass the directory itself.)
Compress-Archive -Path $BuildDir -DestinationPath $Artifact -CompressionLevel Optimal
if (-not (Test-Path $Artifact)) {
    Write-Error "packaging did not produce $Artifact"
    exit 1
}

# --- checksum (append to the single SHA256SUMS) ------------------------------
Write-Host "--- sha256 ---"
# Get-FileHash returns UPPERCASE hex; lowercase it and use two spaces before the
# BARE filename so the line is byte-for-byte compatible with `shasum -a 256`.
$Sha256 = (Get-FileHash -Algorithm SHA256 -Path $Artifact).Hash.ToLower()
$SumLine = "$Sha256  $ArtifactName"

if (-not (Test-Path $SumsFile)) { New-Item -ItemType File -Force -Path $SumsFile | Out-Null }
# Replace (don't duplicate) any prior line for this exact filename.
$existing = @(Get-Content -Path $SumsFile -ErrorAction SilentlyContinue |
              Where-Object { $_ -notmatch "  $([regex]::Escape($ArtifactName))$" })
$existing += $SumLine
# Write LF-terminated, no BOM, so `shasum -c` on a Unix host parses it cleanly.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($SumsFile, (($existing -join "`n") + "`n"), $utf8NoBom)

# --- signed-state reporting (report ONLY; no claim it cannot confirm) --------
Write-Host "--- signing state (report only) ---"
$Exe = Join-Path $BuildDir "SchedulePlanner.exe"
$signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if (-not (Test-Path $Exe)) {
    Write-Warning "executable not found at $Exe; cannot report signing state."
} elseif ($signtool) {
    # signtool verify exits non-zero when there is no valid signature; capture
    # the ACTUAL output and verdict rather than asserting anything ourselves.
    $verifyOut = & $signtool.Source verify /pa /v $Exe 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "SIGNED: signtool verify reports a valid signature for $Exe. Details:"
        $verifyOut | ForEach-Object { Write-Host "    $_" }
        Write-Host "  NOTE: a valid Authenticode signature does NOT by itself prove the cert is"
        Write-Host "        trusted on every target machine — confirm the signing policy with the operator."
    } else {
        Write-Host "UNSIGNED (or unverifiable): signtool verify returned non-zero for $Exe. Output:"
        $verifyOut | ForEach-Object { Write-Host "    $_" }
        Write-Host "  Sign as a SEPARATE opt-in step BEFORE public release; this script does not sign."
    }
} else {
    Write-Warning ("signtool.exe not on PATH; cannot report signing state. It ships with the " +
                   "Windows SDK (e.g. C:\Program Files (x86)\Windows Kits\10\bin\<ver>\x64\). " +
                   "The artifact was still packaged and checksummed.")
}

# --- summary -----------------------------------------------------------------
Write-Host ""
Write-Host "=== package_release: DONE ==="
Write-Host "artifact : $Artifact"
Write-Host "sha256   : $Sha256"
Write-Host "checksums: $SumsFile"
Write-Host "verify   : in Git Bash / WSL: (cd dist/release && shasum -a 256 -c SHA256SUMS)"
