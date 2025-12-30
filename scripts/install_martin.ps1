$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$binDir = Join-Path $env:LOCALAPPDATA "Martin\\bin"
$shim = Join-Path $binDir "martin.cmd"

New-Item -ItemType Directory -Force -Path $binDir | Out-Null

$shimContent = @"
@echo off
set REPO_ROOT=$repoRoot
cd /d "%REPO_ROOT%"
python -m researcher chat %*
"@

Set-Content -Path $shim -Value $shimContent -Encoding ASCII

$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath -notlike "*$binDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$binDir", "User")
    Write-Host "Added $binDir to user PATH."
}

Write-Host "martin installed. Open a new shell and run: martin"
