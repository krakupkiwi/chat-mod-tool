<#
.SYNOPSIS
  Full build pipeline for TwitchIDS -- produces a Windows NSIS installer.

.DESCRIPTION
  Stages:
    1. Validate prerequisites  (Python 3.12, Node 20+, PyInstaller)
    2. Build Python backend     (PyInstaller -> dist-python\twitchids-backend\)
    3. Build React frontend     (vite build -> frontend\dist\)
    4. Package with electron-builder  (NSIS -> dist\TwitchIDS-Setup-X.Y.Z.exe)

.PARAMETER SkipPython
  Skip stage 2 -- use an existing dist-python\ output.

.PARAMETER SkipFrontend
  Skip stage 3 -- use an existing frontend\dist\ output.

.PARAMETER Version
  Override the version string (default: read from frontend\package.json).

.EXAMPLE
  cd "s:\Twitch Chat Bot Detection"
  .\packaging\build.ps1

  # Incremental re-build (Python bundle unchanged):
  .\packaging\build.ps1 -SkipPython
#>

[CmdletBinding()]
param(
    [switch] $SkipPython,
    [switch] $SkipFrontend,
    [string] $Version = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "--- $msg ---" -ForegroundColor Cyan
}

function Write-OK([string]$msg) {
    Write-Host "  OK  $msg" -ForegroundColor Green
}

function Write-Fail([string]$msg) {
    Write-Host "  FAIL  $msg" -ForegroundColor Red
    exit 1
}

function Require-Command([string]$cmd, [string]$hint) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Fail "Command '$cmd' not found. $hint"
    }
}

# ---------------------------------------------------------------------------
# Locate project root
# ---------------------------------------------------------------------------

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Set-Location $ProjectRoot
Write-Host "Project root: $ProjectRoot" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Read version from package.json (unless overridden)
# ---------------------------------------------------------------------------

if (-not $Version) {
    $pkgJson = Get-Content (Join-Path $ProjectRoot "frontend\package.json") | ConvertFrom-Json
    $Version = $pkgJson.version
}
Write-Host "Building version: $Version" -ForegroundColor White

# ---------------------------------------------------------------------------
# Stage 0 -- Validate prerequisites
# ---------------------------------------------------------------------------

Write-Step "Validating prerequisites"

# Python 3.12
$venvPy = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Fail "backend\.venv not found. Create it with: py -3.12 -m venv backend\.venv && backend\.venv\Scripts\pip install -r backend\requirements.txt"
}
$pyVer = & $venvPy -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($pyVer -notmatch "^3\.12") {
    Write-Fail "Python 3.12 required in venv, found: $pyVer"
}
Write-OK "Python $pyVer at $venvPy"

# Node.js
Require-Command "node" "Install Node.js 20+ from https://nodejs.org"
$nodeVer = (node --version) -replace '^v',''
$nodeMaj  = [int]($nodeVer.Split('.')[0])
if ($nodeMaj -lt 20) {
    Write-Fail "Node.js 20+ required, found: $nodeVer"
}
Write-OK "Node.js $nodeVer"

# npm
Require-Command "npm" "npm is bundled with Node.js"
Write-OK "npm $(npm --version)"

# PyInstaller
$pyinstaller = Join-Path $ProjectRoot "backend\.venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $pyinstaller)) {
    Write-Step "Installing PyInstaller into venv"
    & $venvPy -m pip install --quiet pyinstaller
}
Write-OK "PyInstaller $(& $pyinstaller --version 2>&1)"

# ---------------------------------------------------------------------------
# Stage 1 -- Build Python backend with PyInstaller
# ---------------------------------------------------------------------------

if (-not $SkipPython) {
    Write-Step "Building Python backend (PyInstaller)"

    $specFile  = Join-Path $ProjectRoot "packaging\twitchids-backend.spec"
    $distPath  = Join-Path $ProjectRoot "dist-python"
    $buildPath = Join-Path $ProjectRoot "build-python"
    $bundleDir = Join-Path $distPath "twitchids-backend"

    # Clean previous output
    if (Test-Path $bundleDir) {
        Write-Host "  Cleaning $bundleDir" -ForegroundColor Gray
        Remove-Item $bundleDir -Recurse -Force
    }

    & $pyinstaller `
        --distpath $distPath `
        --workpath $buildPath `
        --noconfirm `
        $specFile

    if ($LASTEXITCODE -ne 0) { Write-Fail "PyInstaller failed (exit $LASTEXITCODE)" }

    if (-not (Test-Path (Join-Path $bundleDir "twitchids-backend.exe"))) {
        Write-Fail "PyInstaller finished but twitchids-backend.exe not found in $bundleDir"
    }

    $bundleSize = (Get-ChildItem $bundleDir -Recurse | Measure-Object -Property Length -Sum).Sum
    $bundleMB   = [math]::Round($bundleSize / 1MB, 1)
    Write-OK "Backend bundle: $bundleMB MB at $bundleDir"
} else {
    Write-Host "  SkipPython: using existing dist-python\" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Stage 2 -- Build React frontend
# ---------------------------------------------------------------------------

if (-not $SkipFrontend) {
    Write-Step "Building React frontend (Vite)"

    $frontendDir = Join-Path $ProjectRoot "frontend"

    Push-Location $frontendDir
    try {
        & npm ci --silent
        if ($LASTEXITCODE -ne 0) { Write-Fail "npm ci failed" }

        & npm run build
        if ($LASTEXITCODE -ne 0) { Write-Fail "vite build failed" }
    } finally {
        Pop-Location
    }

    $distIndex = Join-Path $frontendDir "dist\index.html"
    if (-not (Test-Path $distIndex)) {
        Write-Fail "Vite build finished but frontend\dist\index.html not found"
    }
    Write-OK "Frontend bundle at frontend\dist\"
} else {
    Write-Host "  SkipFrontend: using existing frontend\dist\" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Stage 3 -- Package with electron-builder (NSIS installer)
# ---------------------------------------------------------------------------

Write-Step "Packaging with electron-builder"

$frontendDir = Join-Path $ProjectRoot "frontend"
$outDir      = Join-Path $ProjectRoot "dist"

Push-Location $frontendDir
try {
    $env:ELECTRON_BUILDER_VERSION = $Version
    # No code-signing certificate -- skip signing to avoid winCodeSign download
    $env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
    $env:WIN_CSC_LINK = ""
    & npm run build:electron -- --win nsis
    if ($LASTEXITCODE -ne 0) { Write-Fail "electron-builder failed (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

# Locate the produced installer
$installer = Get-ChildItem $outDir -Filter "TwitchIDS-Setup-*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $installer) {
    $installer = Get-ChildItem $outDir -Filter "TwitchIDS*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
}

if ($installer) {
    $sizeMB = [math]::Round($installer.Length / 1MB, 1)
    Write-OK "Installer: $($installer.FullName) ($sizeMB MB)"
} else {
    Write-Host "  Warning: installer .exe not found in $outDir" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  Build complete -- TwitchIDS v$Version" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Gray
Write-Host "    1. Test installer on a clean Windows 11 VM" -ForegroundColor Gray
Write-Host "    2. Submit twitchids-backend.exe to Microsoft Defender portal" -ForegroundColor Gray
Write-Host "    3. Create a GitHub Release and upload the .exe + .yml manifest" -ForegroundColor Gray
Write-Host "    4. Tag the release: git tag v$Version && git push origin v$Version" -ForegroundColor Gray
Write-Host ""
