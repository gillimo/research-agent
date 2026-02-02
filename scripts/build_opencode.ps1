$ErrorActionPreference = "Stop"

$repo = Join-Path $env:USERPROFILE "OneDrive\\Desktop\\projects\\opencode"
if (-not (Test-Path $repo)) {
  Write-Host "OpenCode repo not found at $repo"
  exit 1
}

if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
  Write-Host "Go is not installed. Install Go, then re-run this script."
  exit 1
}

Push-Location $repo
try {
  go build -o opencode.exe .
  Write-Host "Built opencode.exe in $repo"
} finally {
  Pop-Location
}
