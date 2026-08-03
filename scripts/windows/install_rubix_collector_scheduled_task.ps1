<#
.SYNOPSIS
    Install a scheduled task that starts the EXISTING Rubix collector supervisor.

.DESCRIPTION
    Targets scripts/rubix_collector_supervisor.py - the official headless
    collector owner. It never targets the tkinter launcher GUI, because a
    scheduled GUI would open a window and wait for a human, and its
    "Start Rubix & App" button also launches the Streamlit Dashboard.

    This script creates NO collector, NO websocket client, NO adapter and NO
    subscription logic. It only schedules the one that already exists, and it
    never weakens that supervisor's SingleInstanceLock.

    KNOWN BLOCKER - read before using. The supervisor requires an
    authentication frame at most --AuthMaxAgeMinutes old, and nothing in this
    repository can produce or refresh one: services/rubix_auth_assistant.py
    states it "never authenticates, controls a browser, or persists frame
    data". A task firing at 09:20 therefore cannot obtain a fresh frame on its
    own. This installer refuses to register unless a currently-fresh frame is
    present, so it cannot manufacture the appearance of automation that will
    silently fail every morning.

    See docs/audits/providers/rubix/RUBIX_AUTOSTART_ARCHITECTURE_AUDIT.md.

.EXAMPLE
    .\install_rubix_collector_scheduled_task.ps1 `
        -ProjectRoot    "F:\EGX_AI_Trader" `
        -PythonExe      "F:\EGX_AI_Trader\venv\Scripts\python.exe" `
        -AdapterPath    "F:\path\to\rubix\adapter" `
        -AuthFrameFile  "C:\Users\me\AppData\Local\Temp\auth_frame.json" `
        -WhatIfOnly
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$PythonExe,
    [Parameter(Mandatory = $true)][string]$AdapterPath,
    [Parameter(Mandatory = $true)][string]$AuthFrameFile,

    [string]$TaskName = "EGX Rubix Collector Auto Start",
    [string]$CairoStartTime = "09:20",
    [string]$LocalStartTimeOverride = "",
    [string]$DatabasePath = "",
    [int]$BatchSize = 100,
    [int]$AuthMaxAgeMinutes = 15,
    [int]$MaxRuntimeHours = 8,
    [switch]$AllowStaleAuthFrame,
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

function Assert-PathExists {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path)) { throw "$Label not found: $Path" }
}

Write-Host "=== EGX Rubix Collector - scheduled task install ===" -ForegroundColor Cyan
Write-Host "  Schedules the EXISTING supervisor. Creates no collector." -ForegroundColor Yellow
Write-Host ""

# --- Gate 1: paths -----------------------------------------------------------
Assert-PathExists -Path $ProjectRoot -Label "Project root"
Assert-PathExists -Path $PythonExe   -Label "Python executable"
Assert-PathExists -Path $AdapterPath -Label "Rubix adapter directory"

$supervisorRelative = "scripts\rubix_collector_supervisor.py"
$supervisor = Join-Path $ProjectRoot $supervisorRelative
Assert-PathExists -Path $supervisor -Label "Rubix collector supervisor"

if (-not $DatabasePath) { $DatabasePath = Join-Path $ProjectRoot "data\rubix_live_market.db" }

# --- Gate 2: the launcher must be the official one, with a real lock ---------
# Refuse an unverified launcher, and refuse one without single-instance
# protection. A collector without a lock can be started twice, and two
# websockets on one credential is the failure this whole design avoids.
$supervisorText = Get-Content -LiteralPath $supervisor -Raw
foreach ($marker in @("SingleInstanceLock", "InstanceAlreadyRunning")) {
    if ($supervisorText -notmatch [regex]::Escape($marker)) {
        throw ("Refusing to schedule: $supervisorRelative does not contain " +
               "$marker, so it has no verified single-instance protection.")
    }
}
if ($supervisorText -match "tkinter") {
    throw "Refusing to schedule: the target must be headless, but it imports tkinter."
}
Write-Host "  Launcher verified : $supervisorRelative (SingleInstanceLock present, headless)"

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
    Write-Host ""
}

# --- Gate 4: the authentication frame ---------------------------------------
# This is the gate that currently blocks unattended operation.
$authFresh = $false
$authDetail = "not found"
if (Test-Path -LiteralPath $AuthFrameFile) {
    $ageMinutes = ((Get-Date) - (Get-Item -LiteralPath $AuthFrameFile).LastWriteTime).TotalMinutes
    $authFresh = ($ageMinutes -le $AuthMaxAgeMinutes)
    $authDetail = "{0:N1} min old (limit {1})" -f $ageMinutes, $AuthMaxAgeMinutes
}
Write-Host "  Auth frame        : $authDetail"

if (-not $authFresh -and -not $AllowStaleAuthFrame) {
    Write-Host ""
    Write-Host "BLOCKED - the collector cannot start unattended." -ForegroundColor Red
    Write-Host "  The supervisor requires an authentication frame at most $AuthMaxAgeMinutes minutes"
    Write-Host "  old, and nothing in this repository can produce or refresh one."
    Write-Host "  Scheduling it now would register a task that fails every morning."
    Write-Host ""
    Write-Host "  See docs/audits/providers/rubix/RUBIX_AUTOSTART_ARCHITECTURE_AUDIT.md"
    Write-Host "  Re-run with -AllowStaleAuthFrame only if a longer-lived credential"
    Write-Host "  has been arranged with the provider and documented."
    throw "Refusing to register a task that cannot authenticate."
}

# --- The exact action -------------------------------------------------------
# No credential appears here: the auth frame is a file path the human refreshes,
# and no session date is embedded, because the schedule is daily.
$argumentList = @(
    """$supervisorRelative"""
    "--adapter";          """$AdapterPath"""
    "--auth-frame-file";  """$AuthFrameFile"""
    "--database";         """$DatabasePath"""
    "--batch-size";       "$BatchSize"
    "--auth-max-age-minutes"; "$AuthMaxAgeMinutes"
) -join " "

Write-Host ""
Write-Host "Schedule:"
Write-Host "  Cairo start      : $CairoStartTime"
Write-Host "  Local trigger    : $StartTime  ($conversionNote)"
Write-Host ""
Write-Host "Task command:"
Write-Host "  Executable       : $PythonExe"
Write-Host "  Arguments        : $argumentList"
Write-Host "  Working directory: $ProjectRoot"
Write-Host ""

if ($WhatIfOnly) {
    Write-Host "-WhatIfOnly supplied; nothing was registered." -ForegroundColor Green
    return
}

# --- Gate 5: never overwrite an unrelated task ------------------------------
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    $action = $existing.Actions | Select-Object -First 1
    $isOurs = ($action.Arguments -match [regex]::Escape("rubix_collector_supervisor.py"))
    if (-not $isOurs) {
        throw ("A task named '$TaskName' already exists and does NOT invoke " +
               "rubix_collector_supervisor.py. Refusing to overwrite an unrelated task.")
    }
    Write-Host "  Existing ORB-owned task found; it will be replaced." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $PythonExe -Argument $argumentList -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Sunday, Monday, Tuesday, Wednesday, Thursday -At $StartTime
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours $MaxRuntimeHours) `
    -StartWhenAvailable `
    -WakeToRun `
    -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description ("Starts the existing Rubix collector supervisor before the ORB " +
                  "Shadow run. Creates no collector, no websocket and no dashboard.") | Out-Null

Write-Host "Registered scheduled task '$TaskName'." -ForegroundColor Green
Write-Host "  Requires a logged-in interactive session; no password is stored."
Write-Host "  The supervisor's SingleInstanceLock remains the real duplicate guard."
