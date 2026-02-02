$ErrorActionPreference = "Stop"

param(
    [switch]$SkipDeps
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$binDir = Join-Path $env:LOCALAPPDATA "Martin\\bin"
$shim = Join-Path $binDir "martin.cmd"
$venvDir = Join-Path $repoRoot ".venv"
$pythonExe = Join-Path $venvDir "Scripts\\python.exe"

New-Item -ItemType Directory -Force -Path $binDir | Out-Null

if (-not (Test-Path $pythonExe)) {
    Write-Host "Creating venv at $venvDir"
    python -m venv $venvDir
}

if (-not $SkipDeps) {
    Write-Host "Installing requirements (this may take a while)..."
    & $pythonExe -m pip install --upgrade pip | Out-Null
    & $pythonExe -m pip install -r (Join-Path $repoRoot "requirements.txt") | Out-Null
} else {
    Write-Host "Skipping dependency install."
}

$shimContent = @"
@echo off
set REPO_ROOT=$repoRoot
cd /d "%REPO_ROOT%"
"$pythonExe" -m researcher chat %*
"@

Set-Content -Path $shim -Value $shimContent -Encoding ASCII

$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath -notlike "*$binDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$binDir", "User")
    Write-Host "Added $binDir to user PATH."
}

Write-Host "martin installed. Open a new shell and run: martin"
