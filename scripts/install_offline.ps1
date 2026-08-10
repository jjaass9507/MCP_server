# Run on the OFFLINE Windows target machine. Installs from offline_packages/.
$ErrorActionPreference = "Stop"

# Find project root (folder containing pyproject.toml)
$Root = $PSScriptRoot
while ($Root -and -not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    $Root = Split-Path $Root -Parent
}
if (-not $Root) { Write-Error "Project root not found (pyproject.toml missing)"; exit 1 }
Set-Location $Root

# Require the exact Python patch release used for this offline deployment.
$RequiredPythonVersion = "3.11.9"

function Resolve-PythonExecutable {
    param(
        [string]$CommandName,
        [string[]]$LauncherArgs = @()
    )

    $Command = Get-Command $CommandName -ErrorAction SilentlyContinue
    if (-not $Command) { return $null }

    try {
        $Executable = & $Command.Source @LauncherArgs -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $Executable) {
            return ($Executable | Select-Object -Last 1).Trim()
        }
    } catch {
        return $null
    }

    return $null
}

# Check common Windows launch methods. Resolve each one to python.exe first so
# venv creation cannot accidentally use a different Python from the one tested.
$PythonCandidates = @()
$CandidateSpecs = @(
    @{ Name = "python"; Args = @() },
    @{ Name = "py"; Args = @("-3.11") },
    @{ Name = "python3.11"; Args = @() }
)

foreach ($Spec in $CandidateSpecs) {
    $Candidate = Resolve-PythonExecutable -CommandName $Spec.Name -LauncherArgs $Spec.Args
    if ($Candidate -and $PythonCandidates -notcontains $Candidate) {
        $PythonCandidates += $Candidate
    }
}

$PythonExe = $null
$DetectedVersions = @()
foreach ($Candidate in $PythonCandidates) {
    try {
        $CandidateVersion = (& $Candidate -c "import platform; print(platform.python_version())" 2>$null).Trim()
        if ($LASTEXITCODE -eq 0) {
            $DetectedVersions += "$CandidateVersion ($Candidate)"
            if ($CandidateVersion -eq $RequiredPythonVersion) {
                $PythonExe = $Candidate
                break
            }
        }
    } catch {
        # Ignore broken aliases/installations and continue checking candidates.
    }
}

if (-not $PythonExe) {
    $Detected = if ($DetectedVersions.Count -gt 0) {
        $DetectedVersions -join "; "
    } else {
        "none"
    }
    Write-Error "Python $RequiredPythonVersion is required. Detected: $Detected. Install the exact version from https://www.python.org/downloads/release/python-3119/"
    exit 1
}

Write-Host "Using Python $RequiredPythonVersion at $PythonExe"

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
& $PythonExe -m venv $VenvDir

# Use 'python -m pip' (NOT pip.exe) so it works even on a fresh venv.
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
$VenvVersion = (& $VenvPy -c "import platform; print(platform.python_version())").Trim()
if ($LASTEXITCODE -ne 0 -or $VenvVersion -ne $RequiredPythonVersion) {
    Write-Error "Virtual environment uses Python $VenvVersion; expected $RequiredPythonVersion."
    exit 1
}

# 1. Install core runtime dependencies, the database-driver extras selected
#    while packing, and the project wheel.
$ExtrasFile = Join-Path $PkgDir "_mcp_extras.txt"
$Extras = if (Test-Path $ExtrasFile) { (Get-Content $ExtrasFile -Raw).Trim() } else { "core" }
$WheelSpec = if ($Extras -eq "core") { $WheelFile.FullName } else { "$($WheelFile.FullName)[$Extras]" }
& $VenvPy -m pip install --no-index --find-links="$PkgDir" "$WheelSpec"

# 2. Make the LIVE source tree authoritative so a plain 'git pull' updates the
#    running server with no reinstall (the #1 cause of "I updated the code but
#    nothing changed"). We do this WITHOUT an editable install, which would need
#    the hatchling/editables build backend to be present offline.
#
#    Steps: uninstall the copied package (keeps all its dependencies), then drop
#    a .pth file that puts src/ on the interpreter's import path. After this,
#    'import mcp_server' resolves to src/mcp_server directly.
Write-Host ""
Write-Host "Pointing the venv at the live source tree (git pull will now be enough to update)..."
& $VenvPy -m pip uninstall -y mcp-server
$SitePackages = & $VenvPy -c "import sysconfig; print(sysconfig.get_path('purelib'))"
$SrcDir = Join-Path $Root "src"
Set-Content -Path (Join-Path $SitePackages "mcp_server.pth") -Value $SrcDir -Encoding ASCII
Write-Host "Wrote $SitePackages\mcp_server.pth -> $SrcDir"

# Verify the import resolves to the source tree (not a stale copy).
& $VenvPy -c "import mcp_server, pathlib; print('mcp_server loads from:', pathlib.Path(mcp_server.__file__).parent)"

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
