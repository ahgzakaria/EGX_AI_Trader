<#
.SYNOPSIS
    Install the scheduled task that builds the Rubix daily candle, with a
    trigger time derived from the finalizer's own boundary.

.DESCRIPTION
    On 2026-09-03 this task was firing at 14:42 against a 14:45 boundary. A
    session becomes finalizable at the 14:30 close plus CLOSE_SAFETY_MINUTES,
    and three minutes earlier the run exits SESSION_NOT_COMPLETED, returns 0,
    and builds nothing. It had no repetition, so there was no second chance
    that day. The candle was only ever built the following afternoon, by the
    backfill, every session, while the task reported success.

    Nothing could have caught it. The boundary lives in Python and the trigger
    lived in Task Scheduler, and no file in this repository held both.

    So this installer does not accept a start time. It asks the finalizer:

        python scripts\run_rubix_daily_finalizer.py --print-ready-time

    and starts -MarginMinutes after whatever comes back. Change the safety
    window in the code, re-run this, and the schedule follows. The two cannot
    drift apart, because there is only one of them.

    The repetition is the second half. A boundary cleared by five minutes is
    still one fire a day, and a machine asleep or busy at 14:50 loses the
    session exactly as before. Re-running the finalizer is a no-op -- a second
    run inserts 0 bars and skips the ones already stored -- so retrying costs
    ten seconds and buys back the whole afternoon.

    Records only: the finalizer places no order and changes no parameter.

.EXAMPLE
    .\install_rubix_daily_finalizer_task.ps1 -WhatIfOnly

.EXAMPLE
    .\install_rubix_daily_finalizer_task.ps1 `
        -ProjectRoot "F:\EGX_AI_Trader" `
        -PythonExe   "F:\EGX_AI_Trader\venv\Scripts\python.exe"
#>

[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string]$PythonExe   = "",
    [string]$TaskName    = "EGX Rubix Daily Finalizer",

    # How long after the session becomes finalizable to fire. Small enough that
    # the candle is there the same afternoon, large enough that a slow clock or
    # a late scheduler tick does not land inside the window.
    [int]$MarginMinutes = 5,

    # Same-day recovery for a machine that was asleep or busy at the first fire.
    [int]$RetryEveryMinutes = 30,
    [int]$RetryForHours     = 2,

    [int]$MaxRuntimeHours = 2,
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

function Assert-PathExists {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path)) { throw "$Label not found: $Path" }
}

Write-Host "=== EGX Rubix Daily Finalizer - scheduled task install ===" -ForegroundColor Cyan
Write-Host "  The trigger is derived from the code, not typed here." -ForegroundColor Yellow
Write-Host ""

# --- Gate 1: paths -----------------------------------------------------------
if (-not $PythonExe) { $PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe" }
Assert-PathExists -Path $ProjectRoot -Label "Project root"
Assert-PathExists -Path $PythonExe   -Label "Python executable"

$finalizerRelative = "scripts\run_rubix_daily_finalizer.py"
$finalizer = Join-Path $ProjectRoot $finalizerRelative
Assert-PathExists -Path $finalizer -Label "Rubix daily finalizer"

# --- Gate 2: ask the code for its own boundary -------------------------------
# The whole point of this installer. If this call fails, registering a task
# would mean guessing the time again, which is the bug.
Push-Location $ProjectRoot
try {
    $readyRaw = (& $PythonExe $finalizerRelative --print-ready-time 2>&1) | Select-Object -Last 1
} finally {
    Pop-Location
}
$ready = "$readyRaw".Trim()
if ($ready -notmatch '^([01]\d|2[0-3]):[0-5]\d$') {
    throw ("Could not read the finalizer's ready time (got: '$ready'). " +
           "Refusing to register a task whose trigger would be a guess.")
}

$readyTime = [datetime]::ParseExact($ready, "HH:mm", $null)
$startTime = $readyTime.AddMinutes($MarginMinutes)
$StartAt   = $startTime.ToString("HH:mm")

# --- Gate 3: the trigger must actually clear the boundary --------------------
# The assertion the old schedule would have failed.
if ($startTime -le $readyTime) {
    throw ("Trigger $StartAt does not clear the boundary $ready. " +
           "Raise -MarginMinutes.")
}

Write-Host "Schedule (Cairo):"
Write-Host "  Session finalizable at : $ready   (close + CLOSE_SAFETY_MINUTES, read from the code)"
Write-Host "  Trigger                : $StartAt   (+$MarginMinutes min)"
Write-Host "  Retries                : every $RetryEveryMinutes min for $RetryForHours h"
Write-Host "  Days                   : Sunday-Thursday"
Write-Host ""
Write-Host "Task command:"
Write-Host "  Executable       : $PythonExe"
Write-Host "  Arguments        : ""$finalizerRelative"""
Write-Host "  Working directory: $ProjectRoot"
Write-Host ""

if ($WhatIfOnly) {
    Write-Host "-WhatIfOnly supplied; nothing was registered." -ForegroundColor Green
    return
}

# --- Gate 4: never overwrite an unrelated task -------------------------------
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    $existingAction = $existing.Actions | Select-Object -First 1
    $isOurs = ($existingAction.Arguments -match [regex]::Escape("run_rubix_daily_finalizer.py")) -or
              ($existingAction.Arguments -match [regex]::Escape("rubix_daily_finalizer"))
    if (-not $isOurs) {
        throw ("A task named '$TaskName' already exists and does NOT invoke " +
               "run_rubix_daily_finalizer.py. Refusing to overwrite an unrelated task.")
    }
    Write-Host "  Existing finalizer task found; it will be replaced." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $PythonExe `
    -Argument """$finalizerRelative""" -WorkingDirectory $ProjectRoot

$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Sunday, Monday, Tuesday, Wednesday, Thursday -At $StartAt
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $StartAt `
    -RepetitionInterval (New-TimeSpan -Minutes $RetryEveryMinutes) `
    -RepetitionDuration (New-TimeSpan -Hours $RetryForHours)).Repetition

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited

# IgnoreNew, because the retries above would otherwise stack on a slow run.
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours $MaxRuntimeHours) `
    -StartWhenAvailable `
    -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description ("Builds the Rubix daily candle after the session settles. " +
                  "Trigger derived from --print-ready-time (+$MarginMinutes min); " +
                  "re-run this installer if CLOSE_SAFETY_MINUTES changes. " +
                  "Records only.") | Out-Null

Write-Host "Registered scheduled task '$TaskName'." -ForegroundColor Green
Write-Host "  Requires a logged-in interactive session; no password is stored."
Write-Host "  Re-running the finalizer is a no-op, so the retries are safe."
