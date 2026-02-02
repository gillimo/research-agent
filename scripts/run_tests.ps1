$ErrorActionPreference = "Stop"

param(
    [switch]$SkipInstall
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvDir = Join-Path $repoRoot ".venv"
$pythonExe = Join-Path $venvDir "Scripts\\python.exe"

if (-not (Test-Path $pythonExe)) {
    Write-Host "Creating venv at $venvDir"
    python -m venv $venvDir
}

if (-not $SkipInstall) {
    Write-Host "Installing requirements..."
    & $pythonExe -m pip install --upgrade pip | Out-Null
    & $pythonExe -m pip install -r (Join-Path $repoRoot "requirements.txt") | Out-Null
} else {
    Write-Host "Skipping dependency install."
}

Write-Host "Running pytest..."
& $pythonExe -m pytest -q
