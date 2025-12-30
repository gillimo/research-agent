$ErrorActionPreference = "Stop"

$binDir = Join-Path $env:LOCALAPPDATA "Martin\\bin"
$shim = Join-Path $binDir "martin.cmd"

if (Test-Path $shim) {
    Remove-Item -Force $shim
}
if (Test-Path $binDir) {
    Remove-Item -Force -Recurse $binDir
}

$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath) {
    $parts = $currentPath.Split(";") | Where-Object { $_ -and ($_ -ne $binDir) }
    [Environment]::SetEnvironmentVariable("Path", ($parts -join ";"), "User")
}

Write-Host "martin uninstalled."
