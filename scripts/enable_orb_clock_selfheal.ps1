# One-time elevation step: let the daily automation fix the clock by itself.
#
# `w32tm /resync` requires administrator rights. The daily task runs as the
# interactive user at Limited level, so its resync attempt is refused and the
# clock is only ever as good as the last time a human remembered to correct it.
#
# That matters more here than it sounds. On 2026-08-13 the machine clock ran
# ~2s behind the exchange, every quote looked like clock skew, the opening
# range never reached its required coverage, and 224 symbols reported
# OPENING_RANGE_NOT_READY for the whole session. The feed was perfect. The
# session was unrecoverable by the time anything showed it.
#
# The drift is not a one-off. On 2026-08-18, over 45 minutes, the share of
# negative-lag samples went 1% -> 5% -> 10% on a machine that had synced
# cleanly that morning.
#
# Raising the task to Highest lets it run `w32tm /resync` at 09:40 each
# trading morning, before the gate that would otherwise refuse the session.
# The readiness gate still decides: this makes the refusal rarer, it does not
# make a bad clock acceptable.
#
# Run once, from an ELEVATED PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File scripts\enable_orb_clock_selfheal.ps1
#
# Idempotent — running it again re-verifies and changes nothing.

param(
    [string]$TaskName = 'EGX ORB Full Shadow Automation'
)

$ErrorActionPreference = 'Stop'

$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "REFUSED  this must run from an elevated PowerShell." -ForegroundColor Red
    Write-Host "         Right-click PowerShell -> Run as administrator, then:"
    Write-Host "         powershell -ExecutionPolicy Bypass -File $PSCommandPath"
    exit 1
}

# --- 1. let the task resync the clock ----------------------------------
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "REFUSED  scheduled task '$TaskName' does not exist." -ForegroundColor Red
    exit 1
}

if ($task.Principal.RunLevel -eq 'Highest') {
    Write-Host "  run level        already Highest"
} else {
    $userId = $task.Principal.UserId
    if (-not $userId) { $userId = "$env:COMPUTERNAME\$env:USERNAME" }
    $elevated = New-ScheduledTaskPrincipal -UserId $userId `
        -LogonType $task.Principal.LogonType -RunLevel Highest
    Set-ScheduledTask -TaskName $TaskName -Principal $elevated | Out-Null
    Write-Host "  run level        Limited -> Highest" -ForegroundColor Green
}

# --- 2. correct the clock now ------------------------------------------
# The task will do this itself from tomorrow; doing it here means the machine
# is correct before the next session rather than after it.
& w32tm.exe /resync /force 2>&1 | ForEach-Object { Write-Host "  resync           $_" }

Start-Sleep -Seconds 2

# --- 3. show what the clock actually says -------------------------------
Write-Host ""
Write-Host "verification" -ForegroundColor Cyan
$status = & w32tm.exe /query /status 2>&1
$status | Select-String 'Source|Last Successful Sync Time|Stratum' |
    ForEach-Object { Write-Host "  $_" }

$source = ($status | Select-String 'Source:') -replace '.*Source:\s*', ''
if ($source -match 'Local CMOS Clock') {
    Write-Host ""
    Write-Host "WARN  the clock is still undisciplined. w32time may not have " -ForegroundColor Yellow -NoNewline
    Write-Host "reached a server yet;" -ForegroundColor Yellow
    Write-Host "      re-run in a minute, and check that w32time is running." -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "OK  clock is disciplined by $($source.Trim())" -ForegroundColor Green
}

# --- 4. offset against a known-good server ------------------------------
Write-Host ""
Write-Host "offset against time.google.com (positive = this machine is behind)" -ForegroundColor Cyan
& w32tm.exe /stripchart /computer:time.google.com /samples:3 /dataonly 2>&1 |
    Select-Object -Last 3 | ForEach-Object { Write-Host "  $_" }

$reverted = (Get-ScheduledTask -TaskName $TaskName).Principal.RunLevel
Write-Host ""
Write-Host "'$TaskName' now runs at level: $reverted"
