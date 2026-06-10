# Stop BogiAgent (watchdog + telegram bot) — no restart.
#
# The running bot/watchdog may have been started elevated, so a normal taskkill
# hits "Access denied". This script self-elevates via UAC and kills the bogi
# watchdog FIRST (so it can't respawn the bot), then the bot. It does NOT start
# anything — use scripts\restart_bot.ps1 to start again.
#
# Usage: right-click -> "Run with PowerShell", OR from any PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\stop_bot.ps1
# Click "Yes" on the UAC prompt.

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
$py = Join-Path $repo ".venv\Scripts\python.exe"

# --- self-elevate if not already admin ---
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Elevating (accept the UAC prompt)..." -ForegroundColor Yellow
    Start-Process powershell.exe -Verb RunAs -ArgumentList `
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`""
    return
}

Set-Location $repo

# --- find bogi processes; kill watchdog FIRST so it can't respawn the bot ---
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
    $_.CommandLine -and (
        $_.CommandLine -match 'watchdog\.py' -or
        $_.CommandLine -match '__main__\.py telegram' -or
        $_.CommandLine -match 'bogi.*telegram'
    )
}
$watchdogs = $procs | Where-Object { $_.CommandLine -match 'watchdog\.py' }
$bots      = $procs | Where-Object { $_.CommandLine -notmatch 'watchdog\.py' }

if (-not $procs) {
    Write-Host "No running bogi processes found." -ForegroundColor DarkGray
} else {
    foreach ($p in @($watchdogs) + @($bots)) {
        Write-Host ("Killing PID {0}  ({1})" -f $p.ProcessId, $p.CommandLine) -ForegroundColor Red
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

# --- confirm nothing live ---
& $py -m bogi status
Write-Host "`nStopped. Start again with: scripts\restart_bot.ps1" -ForegroundColor Cyan
