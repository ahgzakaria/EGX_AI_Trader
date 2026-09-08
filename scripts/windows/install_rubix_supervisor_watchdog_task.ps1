<#
.SYNOPSIS
    Install the session-hours Rubix collector watchdog. Alert only.

.DESCRIPTION
    Starts scripts/watch_rubix_supervisor.py at 10:00 Cairo and lets it run to
    the 14:30 close. It watches the supervisor's diagnostic PID record and the
    Rubix database read-only, and raises one desktop alert when the collector
    dies, never started, or goes silent while still running.

    It starts nothing, stops nothing, authenticates to nothing and stores no
    credential. Mid-session recovery stays a human act: the supervisor refuses
    an auth frame older than fifteen minutes and nothing in this repository can
    produce one (RUBIX_AUTOSTART_ARCHITECTURE_AUDIT.md, section 5).

    LogonType is Interactive on purpose. The alert is a sound and a dialog on
    the operator's own desktop, and an S4U task has no desktop to put them on --
    it would run all session and be seen by nobody.

.EXAMPLE
    .\install_rubix_supervisor_watchdog_task.ps1 `
        -WorktreePath "F:\EGX_AI_Trader" `
        -PythonwExe   "F:\EGX_AI_Trader\venv\Scripts\pythonw.exe" `
        -WhatIfOnly
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorktreePath,
    [Parameter(Mandatory = $true)][string]$PythonwExe,

    [string]$TaskName = "EGX Rubix Supervisor Watchdog",
    # The open. Before it there is nothing to lose: the 09:10 assisted start
    # owns the morning, and a watchdog complaining at 09:20 that the collector
    # is not up would fire every single day during the export.
    [string]$CairoStartTime = "10:00",
    [string]$LocalStartTimeOverride = "",
    # 10:00 to 14:30 is four and a half hours; five gives the close room without
    # letting an orphaned watcher live into the evening.
    [int]$MaxRuntimeHours = 5,
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

function Assert-PathExists {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path)) { throw "$Label not found: $Path" }
}

Assert-PathExists -Path $WorktreePath -Label "Worktree"
Assert-PathExists -Path $PythonwExe   -Label "pythonw.exe"

$scriptRelative = Join-Path "scripts" "watch_rubix_supervisor.py"
$scriptPath = Join-Path $WorktreePath $scriptRelative
Assert-PathExists -Path $scriptPath -Label "Watchdog entry point"

# --- Refuse to schedule anything that could start or authenticate -----------
# The whole safety argument for this task is that it only ever looks. That is
# worth verifying at install time rather than trusting a code review that
# happened once.
$watchText = Get-Content -LiteralPath $scriptPath -Raw

# Docstrings and comments are stripped first, because this file has to be able
# to *say* "never opens a websocket" without that sentence reading as evidence
# that it does. The guard is about executable code, so it inspects only code.
$watchCode = [regex]::Replace($watchText, '(?s)[rbuf]*"""(.*?)"""', '')
$watchCode = [regex]::Replace($watchCode, "(?s)[rbuf]*'''(.*?)'''", '')
$watchCode = ($watchCode -split "`n" | ForEach-Object { $_ -replace '#.*$', '' }) -join "`n"

foreach ($pattern in @("subprocess", "Popen", "rubix_collector_supervisor",
                       "auth_frame", "streamlit", "websocket")) {
    if ($watchCode -match $pattern) {
        throw "Refusing to schedule: the watchdog references '$pattern'; it must only observe."
    }
}
if ($watchCode -notmatch "mode=ro") {
    throw "Refusing to schedule: the watchdog does not open the Rubix database read-only."
}
Write-Host "  Entry point verified: observes only; starts nothing, writes no market data."

$logicPath = Join-Path $WorktreePath (Join-Path "services" "rubix_supervisor_watchdog.py")
Assert-PathExists -Path $logicPath -Label "Watchdog decision module"

# --- Timezone: the exchange runs on Cairo; the trigger fires on local time ---
$cairoTz = $null
foreach ($id in @("Egypt Standard Time", "Africa/Cairo")) {
    try { $cairoTz = [System.TimeZoneInfo]::FindSystemTimeZoneById($id); break } catch { }
}
if (-not $cairoTz) {
    throw ("Cannot resolve the Cairo timezone (tried 'Egypt Standard Time' and " +
           "'Africa/Cairo'). Pass -LocalStartTimeOverride HH:mm after converting.")
}
$localTz = [System.TimeZoneInfo]::Local
if ($LocalStartTimeOverride) {
    $StartTime = $LocalStartTimeOverride
    $note = "explicit -LocalStartTimeOverride"
}
elseif ($localTz.Id -eq $cairoTz.Id) {
    $StartTime = $CairoStartTime
    $note = "machine is already on Cairo time"
}
else {
    $parts = $CairoStartTime.Split(":")
    $cairoTomorrow = (Get-Date).AddDays(1).Date.AddHours([int]$parts[0]).AddMinutes([int]$parts[1])
    $unspecified = [DateTime]::SpecifyKind($cairoTomorrow, [DateTimeKind]::Unspecified)
    $utc   = [System.TimeZoneInfo]::ConvertTimeToUtc($unspecified, $cairoTz)
    $local = [System.TimeZoneInfo]::ConvertTimeFromUtc($utc, $localTz)
    $StartTime = $local.ToString("HH:mm")
    $note = "converted from $CairoStartTime Cairo"
    Write-Host "TIMEZONE MISMATCH" -ForegroundColor Yellow
    Write-Host "  Machine: $($localTz.Id)   Exchange: $($cairoTz.Id)"
    Write-Host "  Egypt observes DST; re-run this installer after a Cairo DST change." -ForegroundColor Yellow
    Write-Host ""
}

$argumentList = """$scriptRelative"""

Write-Host "Schedule:"
Write-Host "  Cairo start      : $CairoStartTime  (runs to the 14:30 close)"
Write-Host "  Local trigger    : $StartTime  ($note)"
Write-Host "  The 09:10 assisted start and 09:45 ORB Shadow are NOT modified."
Write-Host ""
Write-Host "Task command:"
Write-Host "  Executable       : $PythonwExe"
Write-Host "  Arguments        : $argumentList"
Write-Host "  Working directory: $WorktreePath"
Write-Host ""

if ($WhatIfOnly) {
    Write-Host "-WhatIfOnly supplied; nothing was registered." -ForegroundColor Green
    return
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    $action = $existing.Actions | Select-Object -First 1
    if ($action.Arguments -notmatch [regex]::Escape("watch_rubix_supervisor.py")) {
        throw ("A task named '$TaskName' exists and does NOT invoke " +
               "watch_rubix_supervisor.py. Refusing to overwrite an unrelated task.")
    }
    Write-Host "  Replacing the existing watchdog task." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $PythonwExe -Argument $argumentList -WorkingDirectory $WorktreePath
$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Sunday, Monday, Tuesday, Wednesday, Thursday -At $StartTime
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours $MaxRuntimeHours) `
    -StartWhenAvailable `
    -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description ("Rubix collector watchdog: alerts during 10:00-14:30 Cairo when the " +
                  "collector dies, never started, or goes silent. Starts nothing.") | Out-Null

Write-Host "Registered scheduled task '$TaskName'." -ForegroundColor Green
Write-Host "  Requires a logged-in interactive session; no password is stored."
Write-Host "  It alerts only. Recovery remains a fresh auth frame plus the launcher."
