$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$env:LOCAL_ENDPOINT = "http://localhost:11434"

$localExe = Join-Path $repoRoot "..\\opencode\\opencode.exe"
if (Test-Path $localExe) {
  & $localExe
  exit $LASTEXITCODE
}

if (Get-Command opencode -ErrorAction SilentlyContinue) {
  opencode
  exit $LASTEXITCODE
}

Write-Host "opencode not found. Build or install it, then re-run this script."
Write-Host "Local repo: $env:USERPROFILE\\OneDrive\\Desktop\\projects\\opencode"
exit 1
