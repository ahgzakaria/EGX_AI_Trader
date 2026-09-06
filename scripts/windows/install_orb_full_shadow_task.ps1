<#
.SYNOPSIS
    Install 'EGX ORB Full Shadow Automation' — the task that actually runs the
    ORB shadow session. Research only; places no order.

.DESCRIPTION
    This task existed on the production machine with no installer anywhere in
    the repository. The only ORB installer here registered a different task
    name ('ORB_Shadow_Orchestrator') running a narrower thing -- the
    orchestrator alone -- so the repository described an automation that was
    not installed while the one that was installed was described nowhere. A
    machine rebuilt from this repository would have come back without it.

    What runs is scripts\run_daily_orb_automation.ps1, a wrapper around three
    steps: the feed readiness gate (check_orb_session_readiness.py), the
    orchestrator itself (run_orb_shadow_orchestrator.py), and the session's
    microstructure banking (bank_daily_microstructure.py). The last runs
    whether or not the orchestrator succeeded, because a failed session is
    still a session worth describing.

    On the start time: 09:45 Cairo, before the 10:00 open. The wrapper's own
    comment is the reason it is safe to fire early -- "the orchestrator waits
    for the open itself" -- so this is a margin for the readiness gate and for
    a machine waking up, not a race against the bell. Unlike the daily
    finalizer, nothing here is gated on a clock boundary in the code, so there
    is no time to derive: the orchestrator decides for itself when to act, and
    exits SKIPPED_NON_TRADING_DAY on a day the calendar rejects.

.EXAMPLE
    .\install_orb_full_shadow_task.ps1 -WhatIfOnly

.EXAMPLE
    .\install_orb_full_shadow_task.ps1 -ProjectRoot "F:\EGX_AI_Trader"
#>

[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string]$TaskName    = "EGX ORB Full Shadow Automation",

    # Cairo. Early is safe here; see the description.
    [string]$CairoStartTime = "09:45",
    [string]$LocalStartTimeOverride = "",

    # 7, which is what the registered task carries. I wrote 8 here when this
    # installer was new, for no reason beyond it being a round number larger
    # than the session; the registered value is the one with a history of
    # working. From a 09:45 start it reaches 16:45, and 2026-09-06's full run
    # took four and a half hours.
    [int]$MaxRuntimeHours = 7,
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

function Assert-PathExists {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path)) { throw "$Label not found: $Path" }
}

Write-Host "=== EGX ORB Full Shadow Automation - scheduled task install ===" -ForegroundColor Cyan
Write-Host "  Research only. Places no order and manages no position." -ForegroundColor Yellow
Write-Host ""

# --- Gate 1: paths -----------------------------------------------------------
Assert-PathExists -Path $ProjectRoot -Label "Project root"

$wrapperRelative = "scripts\run_daily_orb_automation.ps1"
$wrapper = Join-Path $ProjectRoot $wrapperRelative
Assert-PathExists -Path $wrapper -Label "ORB daily automation wrapper"

# --- Gate 2: the wrapper must still be the three-step one --------------------
# If it no longer calls the orchestrator, this task is scheduling something
# other than what its name claims, and the name is what an operator reads.
$wrapperText = Get-Content -LiteralPath $wrapper -Raw
foreach ($marker in @("run_orb_shadow_orchestrator.py", "check_orb_session_readiness.py")) {
    if ($wrapperText -notmatch [regex]::Escape($marker)) {
        throw ("Refusing to schedule: $wrapperRelative no longer invokes $marker, " +
               "so it is not the automation this task is named for.")
    }
}
Write-Host "  Wrapper verified  : $wrapperRelative (readiness gate + orchestrator present)"

# --- Gate 3: timezone --------------------------------------------------------
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
    $conversionNote = "explicit -LocalStartTimeOverride"
}
elseif ($localTz.Id -eq $cairoTz.Id) {
    $StartTime = $CairoStartTime
    $conversionNote = "machine is already on Cairo time"
}
else {
    $parts = $CairoStartTime.Split(":")
    $cairoTomorrow = (Get-Date).AddDays(1).Date.AddHours([int]$parts[0]).AddMinutes([int]$parts[1])
    $unspecified = [DateTime]::SpecifyKind($cairoTomorrow, [DateTimeKind]::Unspecified)
    $utc   = [System.TimeZoneInfo]::ConvertTimeToUtc($unspecified, $cairoTz)
    $local = [System.TimeZoneInfo]::ConvertTimeFromUtc($utc, $localTz)
    $StartTime = $local.ToString("HH:mm")
    $conversionNote = "converted from $CairoStartTime Cairo"
    Write-Host "TIMEZONE MISMATCH" -ForegroundColor Yellow
    Write-Host "  Machine: $($localTz.Id)   Exchange: $($cairoTz.Id)"
    Write-Host "  Egypt observes DST; re-run this installer after a Cairo DST change." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Schedule:"
Write-Host "  Cairo start      : $CairoStartTime"
Write-Host "  Local trigger    : $StartTime  ($conversionNote)"
Write-Host "  Days             : Sunday-Thursday"
Write-Host ""
Write-Host "Task command:"
Write-Host "  Executable       : powershell.exe"
Write-Host "  Arguments        : -NoProfile -ExecutionPolicy Bypass -File ""$wrapper"""
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
    if ($existingAction.Arguments -notmatch [regex]::Escape("run_daily_orb_automation.ps1")) {
        throw ("A task named '$TaskName' already exists and does NOT invoke " +
               "run_daily_orb_automation.ps1. Refusing to overwrite an unrelated task.")
    }
    Write-Host "  Existing ORB automation task found; it will be replaced." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# --- Gate 5: refuse to stack two ORB automations -----------------------------
$orchestratorOnly = Get-ScheduledTask -TaskName "ORB_Shadow_Orchestrator" -ErrorAction SilentlyContinue
if ($orchestratorOnly) {
    throw ("'ORB_Shadow_Orchestrator' is registered and runs the same orchestrator " +
           "this wrapper calls. Remove it first with remove_orb_shadow_scheduled_task.ps1, " +
           "or the session would run twice.")
}

# -WindowStyle Hidden: every fire of this task opened a console window on the
# desktop, because a scheduled powershell.exe under an Interactive logon gets a
# visible console. Across the whole task set that was about twenty-three windows
# a day, several of them stealing focus mid-session. Hidden reduces it to a brief
# flash; only a non-interactive principal removes it, and setting one needs
# elevation.
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File ""$wrapper""" `
    -WorkingDirectory $ProjectRoot

$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Sunday, Monday, Tuesday, Wednesday, Thursday -At $StartTime

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited

# No repetition: this one runs for hours and waits for the open itself, so a
# second fire would collide with a live session rather than recover a missed
# one. IgnoreNew is the guard that matters here.
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours $MaxRuntimeHours) `
    -StartWhenAvailable `
    -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description ("ORB full shadow session: readiness gate, orchestrator, " +
                  "microstructure banking. Research only -- places no order.") | Out-Null

Write-Host "Registered scheduled task '$TaskName'." -ForegroundColor Green
Write-Host "  Requires a logged-in interactive session; no password is stored."
Write-Host "  The orchestrator waits for the 10:00 open on its own."
