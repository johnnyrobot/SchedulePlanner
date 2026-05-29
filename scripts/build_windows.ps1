# build_windows.ps1 — build the SchedulePlanner Windows .exe with PyInstaller.
#
# RUN THIS ON WINDOWS. PyInstaller is NOT a cross-compiler: the Windows artifact
# can only be produced and verified on a Windows host. This script was authored
# (but NOT executed) on the macOS dev host, so treat it as the documented build
# recipe to run on Windows, not something verified here.
#
# Mirror of scripts/build_macos.sh, with the two Windows differences:
#   1. The PyInstaller --add-data separator is ';' on Windows (':' on macOS/Linux).
#   2. pywebview uses the EdgeChromium / WebView2 backend on Windows (macOS uses
#      the system WKWebView). WebView2 Runtime must be present on the target
#      machine — it ships with Windows 11 and current Windows 10; otherwise
#      install the Evergreen WebView2 Runtime from Microsoft. The app shell will
#      not render ui.html without it.
#
# Prereqs (PowerShell):
#   python -m venv .venv
#   .\.venv\Scripts\Activate.ps1
#   python -m pip install --upgrade pip
#   python -m pip install -r requirements.txt
#   python -m pip install pyinstaller
#
# Usage:
#   .\scripts\build_windows.ps1            # clean rebuild
#
# Produces: dist\SchedulePlanner\SchedulePlanner.exe (one-dir bundle).

$ErrorActionPreference = "Stop"

# Resolve repo root = parent of the dir containing this script.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

# Prefer `python` (typical on Windows / inside an activated venv); fall back to py launcher.
$PY = $null
if (Get-Command python -ErrorAction SilentlyContinue) {
    $PY = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PY = "py"
} else {
    Write-Error "no 'python' or 'py' launcher found on PATH."
    exit 1
}

& $PY -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller is not installed for '$PY'. Run: $PY -m pip install pyinstaller"
    exit 1
}

Write-Host "Building SchedulePlanner.exe from $RepoRoot (using $PY) ..."

# NOTE: --add-data separator is ';' on Windows (':' on macOS/Linux).
# WebView2 note: pywebview's EdgeChromium backend needs the WebView2 Runtime on
# the target machine (bundled with Win11 / current Win10; else install the
# Evergreen WebView2 Runtime).
& $PY -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name SchedulePlanner `
  --add-data "ui.html;." `
  --add-data "files/lamc_data.xlsx;files" `
  --collect-all ortools `
  app.py

$Exe = "dist\SchedulePlanner\SchedulePlanner.exe"
if (Test-Path $Exe) {
    Write-Host ""
    Write-Host "Build complete: $RepoRoot\$Exe"
    Write-Host "Verify resources with (Git Bash / WSL): ./scripts/verify_build_resources.sh dist/SchedulePlanner"
    Write-Host "Then complete the MANUAL GUI checklist in docs/CROSS_PLATFORM_BUILD.md."
} else {
    Write-Error "expected $Exe was not produced."
    exit 1
}
