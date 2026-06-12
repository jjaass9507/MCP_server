# Run on the OFFLINE Windows target machine. Installs from offline_packages/.
$ErrorActionPreference = "Stop"

# Find project root (folder containing pyproject.toml)
$Root = $PSScriptRoot
while ($Root -and -not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    $Root = Split-Path $Root -Parent
}
if (-not $Root) { Write-Error "Project root not found (pyproject.toml missing)"; exit 1 }
Set-Location $Root

# Confirm Python is available
$ver = python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Python not found. Install Python 3.11+ from https://www.python.org/downloads/"
    exit 1
}
Write-Host "Using $ver"

$PkgDir = Join-Path $Root "offline_packages"
if (-not (Test-Path $PkgDir)) {
    Write-Error "offline_packages not found. Run pack_offline.ps1 on an online machine first."
    exit 1
}

# Find the project wheel
$WheelFile = Get-ChildItem -Path $PkgDir -Filter "mcp_server-*.whl" | Select-Object -First 1
if (-not $WheelFile) {
    Write-Error "mcp_server-*.whl not found. Re-run pack_offline.ps1."
    exit 1
}

# A venv copied from another path/machine is broken (pip.exe hardcodes the
# original python.exe path). Always recreate it here.
$VenvDir = Join-Path $Root ".venv"
if (Test-Path $VenvDir) {
    Write-Host "Removing existing .venv (cannot be reused across machines/paths)..."
    Remove-Item $VenvDir -Recurse -Force
}

Write-Host "Creating virtual environment..."
python -m venv $VenvDir

# Use 'python -m pip' (NOT pip.exe) so it works even on a fresh venv.
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"

# 1. Install runtime dependencies (mcp, psycopg, ...) from the offline wheels.
& $VenvPy -m pip install --no-index --find-links="$PkgDir" "$($WheelFile.FullName)"

# 2. Install hatchling build backend (required for editable install).
#    hatchling is build-time only, so step 1 does not pull it in automatically.
Write-Host "Installing hatchling build backend..."
& $VenvPy -m pip install --no-index --find-links="$PkgDir" hatchling editables

# 3. Re-install the project itself in EDITABLE mode so the live source tree is
#    used at runtime. After this, a plain 'git pull' updates the running server
#    with no reinstall — the #1 cause of "I updated the code but nothing changed".
Write-Host ""
Write-Host "Installing project in editable mode (git pull will now be enough to update)..."
& $VenvPy -m pip install --no-index --find-links="$PkgDir" --no-build-isolation -e .

Write-Host ""
Write-Host "Install complete. Next steps:"
Write-Host "  1. copy config.toml.example config.toml"
Write-Host "  2. Edit config.toml with your paths and databases"
Write-Host "  3. Start the server:"
Write-Host "       .\.venv\Scripts\activate"
Write-Host "       python -m mcp_server.server --transport sse"
Write-Host ""
Write-Host "To update later: just 'git pull' (or unzip a new package over this folder)."
Write-Host "No reinstall needed unless dependencies in pyproject.toml changed."
