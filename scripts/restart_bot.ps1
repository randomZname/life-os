# Restart BogiAgent on the CURRENT code.
#
# The running bot/watchdog may have been started elevated (Task Scheduler /
# admin), so a normal taskkill hits "Access denied". This script self-elevates
# via UAC, kills the existing bogi watchdog + bot by command-line match, then
# starts a fresh watchdog (which respawns the bot from disk = current code).
#
# Usage: right-click -> "Run with PowerShell", OR from any PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\restart_bot.ps1
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

# --- kill existing bogi processes (watchdog.py + bot telegram) ---
$targets = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
    $_.CommandLine -and (
        $_.CommandLine -match 'watchdog\.py' -or
        $_.CommandLine -match '__main__\.py telegram' -or
        $_.CommandLine -match 'bogi.*telegram'
    )
}
if ($targets) {
    foreach ($p in $targets) {
        Write-Host ("Killing PID {0}" -f $p.ProcessId) -ForegroundColor Red
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
} else {
    Write-Host "No running bogi processes found." -ForegroundColor DarkGray
}

# --- start a fresh watchdog (detached, new window, persists) ---
Write-Host "Starting fresh watchdog on current code..." -ForegroundColor Green
Start-Process -FilePath $py -ArgumentList "watchdog.py" -WorkingDirectory $repo
Start-Sleep -Seconds 4

# --- show status (Started should be ~now) ---
& $py -m bogi status
Write-Host "`nIf 'Started' above is ~now and PIDs changed, the new code is live." -ForegroundColor Cyan
Write-Host "Leave the new watchdog window open." -ForegroundColor Cyan
