<#
.SYNOPSIS
    Install the ORB Shadow orchestrator as a Windows scheduled task. Research Only.

.DESCRIPTION
    SUPERSEDED. What actually runs on the production machine is the task
    'EGX ORB Full Shadow Automation', which invokes
    scripts\run_daily_orb_automation.ps1 -- a wrapper that performs the feed
    readiness gate, hands off to this same orchestrator, and then banks the
    session's microstructure. Install that one with
    install_orb_full_shadow_task.ps1.

    This script registers only the middle step, under a different task name
    ('ORB_Shadow_Orchestrator'), and no such task is registered. Running it
    would leave two ORB automations on one machine racing the same session and
    the same repository, which is worse than either alone. It refuses to
    register while the wrapper's task exists.

    Kept because the orchestrator is still runnable on its own for a bare
    research session with no readiness gate and no banking.

    Registers a weekday task that starts the orchestrator shortly before the
    Cairo continuous session. The task starts NOTHING except the orchestrator:
    it never launches Rubix, never touches the collector, and carries no
    credential.

    The weekday schedule is a coarse filter only. The Python orchestrator
    performs the authoritative trading-day check (weekends, configured EGX
    holidays, exceptional closures, special sessions) and exits safely with
    SKIPPED_NON_TRADING_DAY on a non-trading day. If the calendar cannot be
    resolved it fails closed rather than assuming the market is open.

    Runs under the *current* user by default and does not require
    administrator rights. -RunWhetherLoggedOn requires a stored credential,
    which this script deliberately does not do; use the Task Scheduler UI if
    you need it.

.EXAMPLE
    .\install_orb_shadow_scheduled_task.ps1 `
        -WorktreePath "F:\EGX_ORB_full_shadow_live_wt" `
        -PythonExe    "F:\EGX_AI_Trader\venv\Scripts\python.exe" `
        -RubixDbPath  "F:\EGX_AI_Trader\data\rubix_live_market.db"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorktreePath,
    [Parameter(Mandatory = $true)][string]$PythonExe,
    [Parameter(Mandatory = $true)][string]$RubixDbPath,

    [string]$TaskName = "ORB_Shadow_Orchestrator",
    # Cairo wall-clock time of the pre-session check. Converted to local time
    # below; never assumed to already be local.
    [string]$CairoStartTime = "09:40",
    # Explicit override, in LOCAL time, when you have converted it yourself.
    [string]$LocalStartTimeOverride = "",
    [string]$ResearchRoot = "data\research\orb_full_shadow",
    # Hard ceiling; the orchestrator also stops itself at the continuous end.
    [int]$MaxRuntimeHours = 7,
    # Update only the power/availability settings of an already-registered
    # task, leaving its action, arguments, working directory and trigger
    # untouched. Used to enable WakeToRun on a task installed before that
    # setting was added, without re-registering and risking a changed action.
    [switch]$UpdateSettingsOnly,
    # Register even though 'EGX ORB Full Shadow Automation' already runs this
    # orchestrator. Deliberate double-running only; see the gate below.
    [switch]$AllowAlongsideFullShadow,
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

if ($UpdateSettingsOnly) {
    Write-Host "=== ORB Shadow Orchestrator - settings update only ===" -ForegroundColor Cyan
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) { throw "Task '$TaskName' is not registered; nothing to update." }

    $action = $existing.Actions | Select-Object -First 1
    if ($action.Arguments -notmatch [regex]::Escape("run_orb_shadow_orchestrator.py")) {
        throw ("Task '$TaskName' does NOT invoke run_orb_shadow_orchestrator.py. " +
               "Refusing to modify an unrelated task.")
    }

    Write-Host "  Action, arguments, working directory and trigger are NOT modified."
    Write-Host "  Before: WakeToRun=$($existing.Settings.WakeToRun) " +
               "StartWhenAvailable=$($existing.Settings.StartWhenAvailable) " +
               "MultipleInstances=$($existing.Settings.MultipleInstances)"

    if ($WhatIfOnly) {
        Write-Host "-WhatIfOnly supplied; nothing was changed." -ForegroundColor Green
        return
    }

    $settings = New-ScheduledTaskSettingsSet `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours $MaxRuntimeHours) `
        -StartWhenAvailable `
        -WakeToRun `
        -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5)
    Set-ScheduledTask -TaskName $TaskName -Settings $settings | Out-Null

    $after = Get-ScheduledTask -TaskName $TaskName
    Write-Host "  After : WakeToRun=$($after.Settings.WakeToRun) " +
               "StartWhenAvailable=$($after.Settings.StartWhenAvailable) " +
               "MultipleInstances=$($after.Settings.MultipleInstances)"
    Write-Host "Settings updated." -ForegroundColor Green
    return
}

function Assert-PathExists {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Label not found: $Path"
    }
}

Write-Host "=== ORB Shadow Orchestrator - scheduled task install ===" -ForegroundColor Cyan
Write-Host "  RESEARCH ONLY. PRODUCTION EXECUTION DISABLED." -ForegroundColor Yellow
Write-Host ""

Assert-PathExists -Path $WorktreePath -Label "Worktree"
Assert-PathExists -Path $PythonExe    -Label "Python executable"
Assert-PathExists -Path $RubixDbPath  -Label "Rubix source database"

# --- Timezone resolution -----------------------------------------------------
# Task Scheduler triggers fire on LOCAL wall-clock time. The exchange runs on
# Africa/Cairo, which observes DST. Assuming the machine is already on Cairo
# time would silently start the run at the wrong exchange time for part of the
# year, so the conversion is explicit and printed.
$cairoTz = $null
foreach ($id in @("Egypt Standard Time", "Africa/Cairo")) {
    try { $cairoTz = [System.TimeZoneInfo]::FindSystemTimeZoneById($id); break } catch { }
}
if (-not $cairoTz) {
    throw ("Cannot resolve the Cairo timezone on this machine " +
           "(tried 'Egypt Standard Time' and 'Africa/Cairo'). " +
           "Convert the time yourself and pass -LocalStartTimeOverride HH:mm.")
}

$localTz = [System.TimeZoneInfo]::Local
$machineIsCairo = ($localTz.Id -eq $cairoTz.Id)

if ($LocalStartTimeOverride) {
    $StartTime = $LocalStartTimeOverride
    $conversionNote = "explicit -LocalStartTimeOverride"
}
elseif ($machineIsCairo) {
    $StartTime = $CairoStartTime
    $conversionNote = "machine is already on Cairo time"
}
else {
    # Convert tomorrow's Cairo start instant into local time. Tomorrow rather
    # than today so an install run after the start time still resolves the
    # offset that will actually apply on the next firing.
    $parts = $CairoStartTime.Split(":")
    $cairoTomorrow = (Get-Date).AddDays(1).Date.AddHours([int]$parts[0]).AddMinutes([int]$parts[1])
    $cairoUnspecified = [DateTime]::SpecifyKind($cairoTomorrow, [DateTimeKind]::Unspecified)
    $utc = [System.TimeZoneInfo]::ConvertTimeToUtc($cairoUnspecified, $cairoTz)
    $local = [System.TimeZoneInfo]::ConvertTimeFromUtc($utc, $localTz)
    $StartTime = $local.ToString("HH:mm")
    $conversionNote = "converted from $CairoStartTime Cairo"

    Write-Host "TIMEZONE MISMATCH" -ForegroundColor Yellow
    Write-Host "  Machine timezone : $($localTz.Id)"
    Write-Host "  Exchange timezone: $($cairoTz.Id)"
    Write-Host "  $CairoStartTime Cairo resolves to $StartTime local for the next firing."
    Write-Host "  Egypt observes DST, so this offset can shift. Re-run this" -ForegroundColor Yellow
    Write-Host "  installer after a Cairo DST change, or set the machine to" -ForegroundColor Yellow
    Write-Host "  Egypt Standard Time so no conversion is needed." -ForegroundColor Yellow
    Write-Host ""
}

Write-Host "Schedule:"
Write-Host "  Cairo start      : $CairoStartTime"
Write-Host "  Local trigger    : $StartTime  ($conversionNote)"
Write-Host "  Machine timezone : $($localTz.Id)"
Write-Host ""

$scriptRelative = "scripts\run_orb_shadow_orchestrator.py"
$scriptPath = Join-Path $WorktreePath $scriptRelative
Assert-PathExists -Path $scriptPath -Label "Orchestrator script"

# The orchestrator resolves every other path itself from the session date, so
# the task command carries no secret, no .env reference and no database
# credential - only three filesystem paths.
$argumentList = @(
    """$scriptRelative"""
    "--rubix-db-path"
    """$RubixDbPath"""
    "--research-root"
    """$ResearchRoot"""
    "--no-network"
) -join " "

Write-Host "Task command:"
Write-Host "  Executable       : $PythonExe"
Write-Host "  Arguments        : $argumentList"
Write-Host "  Working directory: $WorktreePath"
Write-Host ""

if ($WhatIfOnly) {
    Write-Host "-WhatIfOnly supplied; nothing was registered." -ForegroundColor Green
    return
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    throw "Task '$TaskName' already exists. Run remove_orb_shadow_scheduled_task.ps1 first."
}

# Two ORB automations on one machine would run the same session through the
# same repository at the same time. This registers the orchestrator alone;
# 'EGX ORB Full Shadow Automation' registers the wrapper that already calls it.
$wrapper = Get-ScheduledTask -TaskName "EGX ORB Full Shadow Automation" -ErrorAction SilentlyContinue
if ($wrapper -and -not $AllowAlongsideFullShadow) {
    throw ("'EGX ORB Full Shadow Automation' is registered, and it already runs " +
           "this orchestrator through scripts\run_daily_orb_automation.ps1. " +
           "Registering '$TaskName' as well would run the session twice. " +
           "Use install_orb_full_shadow_task.ps1 to manage that task, or pass " +
           "-AllowAlongsideFullShadow if you genuinely want both.")
}

$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $argumentList `
    -WorkingDirectory $WorktreePath

# Sunday-Thursday is the Cairo trading week. The orchestrator still performs
# the authoritative calendar check; this only avoids pointless wake-ups.
$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Sunday, Monday, Tuesday, Wednesday, Thursday `
    -At $StartTime

# Current user, no stored password, no elevation.
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours $MaxRuntimeHours) `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description ("ORB Shadow orchestrator (Research Only, production disabled). " +
                  "Observes the existing Rubix collector read-only; never starts it.") | Out-Null

Write-Host "Registered scheduled task '$TaskName'." -ForegroundColor Green
Write-Host ""
Write-Host "Notes:" -ForegroundColor Cyan
Write-Host "  - MultipleInstances=IgnoreNew, and the orchestrator additionally holds a"
Write-Host "    transactional database lease, so a second run cannot start."
Write-Host "  - Logs:    <worktree>\logs\orb_shadow\<YYYY-MM-DD>\"
Write-Host "  - Reports: <worktree>\reports\audits\strategies\orb_first_pullback\full_shadow\<YYYY-MM-DD>\"
Write-Host "  - Both are gitignored."
Write-Host "  - The task requires the user to be logged on. It stores no password."
Write-Host "  - Check status without starting anything:"
Write-Host "      $PythonExe $scriptRelative --rubix-db-path ""$RubixDbPath"" --status"
