# Run on the plant (deployment) machine to verify WHICH code the server is
# running and update it to the latest main. Answers the recurring question
# "the bug was fixed, why is it back?" — which is almost always a stale
# checkout (old branch/commit) or a stale copy in the venv's site-packages.
$ErrorActionPreference = "Stop"

# Find project root (folder containing pyproject.toml)
$Root = $PSScriptRoot
while ($Root -and -not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    $Root = Split-Path $Root -Parent
}
if (-not $Root) { Write-Error "Project root not found (pyproject.toml missing)"; exit 1 }
Set-Location $Root

Write-Host "=== 1. Current checkout ==="
git branch --show-current
git log --oneline -1

Write-Host "`n=== 2. Updating to latest main ==="
git checkout main
git pull
git log --oneline -1

Write-Host "`n=== 3. Verifying the venv imports THIS tree (not site-packages) ==="
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$importPath = & $py -c "import mcp_server; print(mcp_server.__file__)"
Write-Host "mcp_server imports from: $importPath"
$expected = Join-Path $Root "src\mcp_server"
if ($importPath -like "$expected*") {
    Write-Host "OK: venv points at the live source tree - git pull updates take effect." -ForegroundColor Green
    Write-Host "`nDone. Restart the MCP server process to load the updated code."
} else {
    Write-Host "PROBLEM: mcp_server is imported from OUTSIDE this repo (probably a stale" -ForegroundColor Red
    Write-Host "copy in site-packages). git pull does NOT update that copy." -ForegroundColor Red
    Write-Host "Fix: re-run scripts\install_offline.ps1 to rebuild the venv, then restart." -ForegroundColor Red
    exit 1
}
