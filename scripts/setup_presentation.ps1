# Install pptxgenjs for the presentation generation tool.
# Run this once in the MCP_server directory before using create_presentation.
$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
while ($Root -and -not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    $Root = Split-Path $Root -Parent
}
if (-not $Root) { Write-Error "Cannot find project root (pyproject.toml not found)"; exit 1 }
Set-Location $Root

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Error "Node.js is not installed. Download from https://nodejs.org/"
    exit 1
}

Write-Host "Installing pptxgenjs in: $Root"

if (-not (Test-Path (Join-Path $Root "package.json"))) {
    npm init -y | Out-Null
}

npm install pptxgenjs --save

Write-Host ""
Write-Host "Done. Verify with:"
Write-Host "  node scripts/generate_pptx.js --test"
