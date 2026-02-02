$ErrorActionPreference = "Stop"

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("start","stop","status")]
    [string]$Action
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$logsDir = Join-Path $repoRoot "logs"
$pidFile = Join-Path $logsDir "martin_service.pid"
$venvDir = Join-Path $repoRoot ".venv"
$pythonExe = Join-Path $venvDir "Scripts\\python.exe"

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

function Get-Pid {
    if (Test-Path $pidFile) {
        try { return Get-Content $pidFile } catch { return "" }
    }
    return ""
}

if ($Action -eq "start") {
    if (-not (Test-Path $pythonExe)) {
        Write-Host "Missing venv python at $pythonExe. Run scripts\\install_martin.ps1 first."
        exit 1
    }
    $existing = Get-Pid
    if ($existing) {
        try {
            $p = Get-Process -Id $existing -ErrorAction Stop
            Write-Host "Service already running (PID $existing)."
            exit 0
        } catch {
            Remove-Item $pidFile -ErrorAction SilentlyContinue
        }
    }
    $proc = Start-Process -FilePath $pythonExe -ArgumentList "-m","researcher.librarian" -PassThru -WindowStyle Hidden
    Set-Content -Path $pidFile -Value $proc.Id -Encoding ASCII
    Write-Host "Service started (PID $($proc.Id))."
    exit 0
}

if ($Action -eq "stop") {
    $pid = Get-Pid
    if (-not $pid) {
        Write-Host "Service not running."
        exit 0
    }
    try {
        Stop-Process -Id $pid -Force
        Remove-Item $pidFile -ErrorAction SilentlyContinue
        Write-Host "Service stopped."
        exit 0
    } catch {
        Write-Host "Failed to stop service (PID $pid)."
        exit 1
    }
}

if ($Action -eq "status") {
    $pid = Get-Pid
    if (-not $pid) {
        Write-Host "Service not running."
        exit 1
    }
    try {
        $p = Get-Process -Id $pid -ErrorAction Stop
        Write-Host "Service running (PID $pid)."
        exit 0
    } catch {
        Write-Host "Service not running (stale PID file)."
        exit 1
    }
}
