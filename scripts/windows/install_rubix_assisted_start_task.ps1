<#
.SYNOPSIS
    Install the Rubix Assisted Start UI as a scheduled task. Collector only.

.DESCRIPTION
    Opens the assisted-start window at 09:10 Cairo so the user can perform their
    normal authentication export; the window then detects the frame, validates
    it with the existing official validator, and starts the existing headless
    collector supervisor - and nothing else.

    It never launches Streamlit or the Dashboard, never opens a websocket,
    never authenticates, and stores no credential. The window is the only thing
    this task starts.

    Timing: 09:10 assisted UI, collector healthy before ~09:30, ORB Shadow
    fires at 09:40 (a separate, already-installed task that is not modified).

.EXAMPLE
    .\install_rubix_assisted_start_task.ps1 `
        -WorktreePath "F:\EGX_AI_Trader" `
        -PythonwExe   "F:\EGX_AI_Trader\venv\Scripts\pythonw.exe" `
        -AdapterPath  "<rubix adapter directory>" `
        -WhatIfOnly
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorktreePath,
    [Parameter(Mandatory = $true)][string]$PythonwExe,
    [Parameter(Mandatory = $true)][string]$AdapterPath,
    [Parameter(Mandatory = $true)][string]$RuntimeRoot,

    [string]$TaskName = "EGX Rubix Assisted Start",
    # 09:10 is the start of a timed human procedure, not a slot that can be
    # slid. RUBIX_ASSISTED_START_RUNBOOK.md: the window opens at 09:10, the
    # export happens 09:10-09:25, validation lands ~09:15, and the window has
    # confirmed the feed is advancing before ~09:30 -- half an hour of margin
    # on the 10:00 open, because what it is waiting for is a person.
    #
    # The registered task had been moved to 09:45, leaving fifteen minutes for
    # a twenty-minute procedure, and nothing compared the two until
    # verify_scheduled_tasks.ps1 did. The declared value wins here: it is the
    # one with a documented reason and a test behind it.
    [string]$CairoStartTime = "09:10",
    [string]$LocalStartTimeOverride = "",
    # The user's existing export target, read in place. Overridable for testing
    # or a future relocation; the production default never changes.
    [string]$AuthFramePath = "C:\secure-temp\rubix-price-auth-frame.txt",
    # Two hours. It was cut to one to stop an abandoned window sitting on the
    # desktop until 11:30, which it did -- but that traded a cosmetic annoyance
    # for the only thing on this machine that can start the collector.
    #
    # This window is the human's way in: the supervisor needs an auth frame no
    # more than fifteen minutes old, and nothing in this repository can produce
    # one. If the collector drops at 10:30 and the window died at 10:10, the
    # session is over until somebody opens it by hand. On 2026-09-07 the
    # collector stopped at 10:08 and the window was terminated at 10:10:01 by
    # the one-hour limit; those turned out to be unrelated, but for two minutes
    # the recovery path and the failure overlapped, which is close enough.
    #
    # Two hours reaches 11:10 from a 09:10 start: past the open, past the
    # opening auction, and through the part of the session where a restart is
    # still worth something. The idle window costs nothing but pixels.
    [int]$MaxRuntimeHours = 2,
    [switch]$EnableAutoStart,
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

function Assert-PathExists {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path)) { throw "$Label not found: $Path" }
}

Write-Host "=== EGX Rubix Assisted Start - scheduled task install ===" -ForegroundColor Cyan
Write-Host "  COLLECTOR ONLY. NO DASHBOARD. NO TRADING. NO CREDENTIAL STORAGE." -ForegroundColor Yellow
Write-Host ""

Assert-PathExists -Path $WorktreePath -Label "Worktree"
Assert-PathExists -Path $PythonwExe   -Label "Windowed Python (pythonw.exe)"
Assert-PathExists -Path $AdapterPath  -Label "Rubix adapter directory"
Assert-PathExists -Path $RuntimeRoot  -Label "Canonical runtime root"

# --- Runtime root: where the collector's data actually lives -----------------
# The code may run from a dedicated runtime worktree, but the database, universe,
# PID file and lock must be the ONE canonical set the live collector already
# uses. Deriving them from the code location would point the supervisor at an
# empty database and at a PID/lock file the running supervisor does not hold -
# defeating single-instance protection and permitting a second collector.
$runtimeResolved = (Resolve-Path -LiteralPath $RuntimeRoot).ProviderPath
$runtimeDb = Join-Path $runtimeResolved (Join-Path "data" "rubix_live_market.db")
if (-not (Test-Path -LiteralPath $runtimeDb)) {
    throw ("Refusing to schedule: '$runtimeDb' does not exist. -RuntimeRoot must " +
           "be the canonical root the live collector already writes to; never " +
           "point it at a fresh worktree and never copy the database.")
}
$runtimePid = Join-Path $runtimeResolved (Join-Path "data" "rubix_supervisor.pid.json")
Write-Host "  Runtime root verified: $runtimeResolved"
Write-Host "    database  : $runtimeDb"
Write-Host "    pid file  : $runtimePid  (shared with the running supervisor)"

$scriptRelative = "scripts\run_rubix_assisted_start.py"
Assert-PathExists -Path (Join-Path $WorktreePath $scriptRelative) -Label "Assisted start entry point"

# The entry point must be the assisted UI, and must not be a dashboard launcher.
$entryText = Get-Content -LiteralPath (Join-Path $WorktreePath $scriptRelative) -Raw
# Match an actual invocation, not the word. The entry point's own documentation
# says it "never launches Streamlit", and a bare -match "streamlit" would refuse
# the very script it is meant to schedule.
foreach ($pattern in @("start_streamlit", "streamlit run", "streamlit\.web", "import streamlit")) {
    if ($entryText -match $pattern) {
        throw "Refusing to schedule: the assisted entry point invokes Streamlit ($pattern)."
    }
}
# The supervisor path is built in the logic module, not the entry script, so
# verify where the truth actually lives: the entry point must use the shared
# command builder, and that builder must target the supervisor.
if ($entryText -notmatch "build_collector_command") {
    throw "Refusing to schedule: the entry point does not use the shared collector command builder."
}
$logicRelative = Join-Path "services" "rubix_assisted_start.py"
$logicPath = Join-Path $WorktreePath $logicRelative
Assert-PathExists -Path $logicPath -Label "Assisted start logic module"
$logicText = Get-Content -LiteralPath $logicPath -Raw
if ($logicText -notmatch "rubix_collector_supervisor") {
    throw "Refusing to schedule: the command builder does not target the collector supervisor."
}
foreach ($pattern in @("start_streamlit", "streamlit run", "import streamlit")) {
    if ($logicText -match $pattern) {
        throw "Refusing to schedule: the command builder invokes Streamlit ($pattern)."
    }
}
Write-Host "  Entry point verified: builds the supervisor command, invokes no Streamlit."

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

# No credential, and no session date: the schedule is daily and the frame is a
# file the user refreshes each morning.
# The user's real, long-established export target. The window reads it in
# place: no inbox is involved, and nothing is copied, moved or deleted.
$argumentList = @(
    """$scriptRelative"""
    "--runtime-root"; """$runtimeResolved"""
    "--auth-frame"; """$AuthFramePath"""
    "--adapter-path"; """$AdapterPath"""
)
if ($EnableAutoStart) { $argumentList += "--auto-start" }
$argumentList = $argumentList -join " "

Write-Host "Schedule:"
Write-Host "  Cairo start      : $CairoStartTime"
Write-Host "  Local trigger    : $StartTime  ($note)"
Write-Host "  ORB Shadow runs separately at 09:40 and is NOT modified."
Write-Host "  Runtime data root: $runtimeResolved  (code may live elsewhere)"
Write-Host ""
Write-Host "Task command:"
Write-Host "  Executable       : $PythonwExe"
Write-Host "  Arguments        : $argumentList"
Write-Host "  Working directory: $WorktreePath"
Write-Host "  Auto-start       : $(if ($EnableAutoStart) { 'ENABLED (opt-in)' } else { 'disabled (default)' })"
Write-Host ""

if ($WhatIfOnly) {
    Write-Host "-WhatIfOnly supplied; nothing was registered." -ForegroundColor Green
    return
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    $action = $existing.Actions | Select-Object -First 1
    if ($action.Arguments -notmatch [regex]::Escape("run_rubix_assisted_start.py")) {
        throw ("A task named '$TaskName' exists and does NOT invoke " +
               "run_rubix_assisted_start.py. Refusing to overwrite an unrelated task.")
    }
    Write-Host "  Replacing the existing assisted-start task." -ForegroundColor Yellow
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
    -StartWhenAvailable -WakeToRun `
    -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description ("Rubix Assisted Start: opens the collector-only assisted window " +
                  "before the session. No dashboard, no trading, no stored credential.") | Out-Null

Write-Host "Registered scheduled task '$TaskName'." -ForegroundColor Green
Write-Host "  Requires a logged-in interactive session; no password is stored."
Write-Host "  The user still performs the normal authentication export."
