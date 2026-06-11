# Run on an ONLINE Windows machine. Downloads all dependencies and packs them.
param([string]$Proxy = "")
$ErrorActionPreference = "Stop"

# Find project root (folder containing pyproject.toml)
$Root = $PSScriptRoot
while ($Root -and -not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    $Root = Split-Path $Root -Parent
}
if (-not $Root) { Write-Error "Project root not found (pyproject.toml missing)"; exit 1 }
Set-Location $Root

# Resolve proxy
if (-not $Proxy) { $Proxy = $env:HTTPS_PROXY }
if (-not $Proxy) { $Proxy = $env:HTTP_PROXY }
$ProxyArg = if ($Proxy) { @("--proxy", $Proxy) } else { @() }

# Build backend needed to build this project's wheel
pip install @ProxyArg hatchling

# Download all runtime dependencies as wheels
$PkgDir = Join-Path $Root "offline_packages"
New-Item -ItemType Directory -Force -Path $PkgDir | Out-Null
pip download @ProxyArg "mcp[cli]>=1.0.0" "psycopg[binary]>=3.1" -d $PkgDir

# Build this project into a wheel in the same directory
pip wheel . --no-deps --no-build-isolation -w $PkgDir

# Install pptxgenjs (Node.js) so node_modules can be included in the zip.
# node_modules is portable across machines (unlike Python venv).
if (Get-Command node -ErrorAction SilentlyContinue) {
    Write-Host "Installing pptxgenjs for offline packaging..."
    if ($Proxy) { npm install pptxgenjs --save --proxy $Proxy }
    else         { npm install pptxgenjs --save }
} else {
    Write-Warning "Node.js not found — skipping pptxgenjs. Install Node.js and re-run if you need presentation tools."
}

# Zip the project, excluding .venv / .git / caches.
# node_modules IS included (it's portable; a packed .venv is NOT portable).
$ZipPath = Join-Path (Split-Path $Root -Parent) "mcp-server-offline.zip"
$TempDir = Join-Path $env:TEMP "mcp_pack_$(Get-Random)"
Copy-Item $Root $TempDir -Recurse
Remove-Item "$TempDir\.git"  -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "$TempDir\.venv" -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $TempDir -Recurse -Include "__pycache__","*.egg-info" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Compress-Archive -Path "$TempDir\*" -DestinationPath $ZipPath -Force
Remove-Item $TempDir -Recurse -Force

Write-Host ""
Write-Host "Done: $ZipPath"
Write-Host "Transfer to the target machine, unzip, then run install_offline.ps1"
Write-Host "Node.js must be installed on the target machine (node_modules requires local node binary)."
