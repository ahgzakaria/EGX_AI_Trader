# The one entry point Task Scheduler calls each trading morning.
#
# It exists because of what the previous arrangement actually did. Two tasks
# pointed their working directory at `F:\EGX_ORB_automation_runtime_wt` and
# `F:\EGX_RUBIX_assisted_runtime_wt`. Those worktrees were removed. From that
# day on both tasks failed in under a second with 0x8007010B (the directory
# name is invalid), every trading morning, and said nothing to anybody. The
# operator went on starting sessions by hand and assumed that was the design.
# On 2026-08-17 nobody started anything and the whole session was lost.
#
# So this script fixes the class of failure, not just the instance:
#
#   * It derives the project root from its own location. A moved or renamed
#     checkout can no longer leave the schedule pointing at a path that
#     evaporated, because there is no path to get stale.
#   * It writes a status file on EVERY exit, including the failures. A
#     scheduled task that fails silently is indistinguishable from one that
#     was never scheduled, which is exactly how this went unnoticed for weeks.
#   * It refuses rather than producing a worthless session, and says why.
#
# Sequence:
#   1. trading day        (EGX runs Sunday-Thursday)
#   2. clock discipline   (resync if we are allowed to, then verify)
#   3. readiness gate     (clock skew + live feed freshness)
#   4. hand off to the orchestrator, which waits for the open, runs Lane A to
#      14:15, then Lane B, the cross-run comparison and the report
#
# Step 2 and 3 exist because of 2026-08-13: the machine clock ran ~2s behind
# the exchange, every quote looked like clock skew, the opening range never
# reached its required coverage, and 224 symbols reported
# OPENING_RANGE_NOT_READY for the entire session. The feed was perfect. None of
# it was visible until the window had passed and become unrecoverable.
#
# Research only. Nothing here places an order, and no state it produces can
# represent a trade.
#
#   pwsh -File scripts\run_daily_orb_automation.ps1
#   pwsh -File scripts\run_daily_orb_automation.ps1 -SkipReadiness   # diagnostics

param(
    [string]$SessionDate = (Get-Date -Format 'yyyy-MM-dd'),
    [string]$RubixDbPath,
    # How long the readiness gate may keep retrying. The continuous session
    # opens at 10:00; three minutes before it, a feed that is still not live
    # is not going to be live in time to see the opening range form.
    [string]$ReadyByTime = '09:57',
    [switch]$SkipReadiness
)

$ErrorActionPreference = 'Continue'
$root   = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'venv\Scripts\python.exe'
Set-Location $root

# Log appenders that write UTF-8 under Windows PowerShell 5.1, which is what
# the scheduled task runs. This log was the worst of the three: `Say` wrote
# BOM'd UTF-8 lines while the piped Python output arrived as UTF-16, in the
# same file.
. (Join-Path $PSScriptRoot 'lib\utf8_log.ps1')

# The readiness gate and the orchestrator must look at the same feed, or the
# gate is checking something the session will not use.
if (-not $RubixDbPath) { $RubixDbPath = Join-Path $root 'data\rubix_live_market.db' }

$statusDir = Join-Path $root 'data\automation_status'
$logDir    = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $statusDir, $logDir | Out-Null
$statusPath = Join-Path $statusDir "orb_automation_$SessionDate.json"
$log        = Join-Path $logDir "orb_automation_$SessionDate.log"

$started = Get-Date
$steps   = [ordered]@{}

function Write-Status {
    param([string]$Outcome, [string]$Reason, [hashtable]$Extra = @{})

    # Written on every exit path. The dashboard reads this file to answer "did
    # the automation run today?" -- a question that previously had no answer
    # short of digging through Task Scheduler's last-result codes.
    $payload = [ordered]@{
        session_date = $SessionDate
        outcome      = $Outcome
        reason       = $Reason
        started_at   = $started.ToString('o')
        finished_at  = (Get-Date).ToString('o')
        host         = $env:COMPUTERNAME
        steps        = $steps
        log          = $log
    }
    foreach ($k in $Extra.Keys) { $payload[$k] = $Extra[$k] }

    # Written through a temporary file so a power cut mid-write cannot leave a
    # half-written status that a reader would trust -- this machine lost mains
    # power mid-session on 2026-08-18.
    #
    # UTF8Encoding($false) rather than `-Encoding utf8`: Windows PowerShell 5.1
    # writes a BOM for the latter, and `json.load` on the Python side rejects
    # it. The task runs under 5.1 because its path never moves.
    $temporary = "$statusPath.partial"
    $json = $payload | ConvertTo-Json -Depth 6
    [System.IO.File]::WriteAllText($temporary, $json, (New-Object System.Text.UTF8Encoding $false))
    Move-Item -Path $temporary -Destination $statusPath -Force
}

function Say($message) {
    $line = "{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $message
    Write-Host $line
    Add-Utf8LogLine -Path $log -Value $line
}

function Refuse {
    param([string]$Step, [string]$Reason)
    $steps[$Step] = 'REFUSED'
    Say "REFUSED  $Reason"
    Write-Status -Outcome 'REFUSED' -Reason $Reason
    exit 1
}

Say "=== ORB daily automation - $SessionDate ==="
Say "root $root"
Write-Status -Outcome 'RUNNING' -Reason 'started'

# --- 1. trading day ----------------------------------------------------
$isTradingDay = & $python -c @"
import sys; sys.path.insert(0, r'$root')
from datetime import date
from core.egx_session import is_regular_trading_day
print('yes' if is_regular_trading_day(date.fromisoformat('$SessionDate')) else 'no')
"@ 2>&1
if ("$isTradingDay".Trim() -ne 'yes') {
    Refuse 'trading_day' "$SessionDate is not an EGX trading day."
}
$steps['trading_day'] = 'OK'
Say "trading day  OK"

# --- 2. clock discipline -----------------------------------------------
# A reboot leaves the clock undisciplined until w32time completes its first
# sync, and the machine has come back from a power cut mid-session before
# (2026-08-18, out 13:35 to 14:15). Resync needs elevation, so this is an
# attempt, not a guarantee -- the readiness gate below is what actually
# decides. Never fatal on its own: a clock that is already correct does not
# need the resync to have succeeded.
$resync = & w32tm.exe /resync /force 2>&1 | Out-String
if ($LASTEXITCODE -eq 0) {
    $steps['clock_resync'] = 'OK'
    Say "clock resync  OK"
} else {
    $steps['clock_resync'] = 'UNAVAILABLE'
    Say "clock resync  unavailable ($(($resync -split "`n" | Select-Object -Last 2) -join ' ' -replace '\s+',' '))"
    Say "              run the task with highest privileges to let it self-heal"
}

# --- 3. readiness gate -------------------------------------------------
if ($SkipReadiness) {
    $steps['readiness'] = 'SKIPPED'
    Say "readiness  SKIPPED by request"
} else {
    # Retry rather than judge once. Both things this gate measures need time
    # that a single attempt does not give them:
    #
    #   * The collector task starts at the same minute as this one. Asking it
    #     for a fresh quote the instant it launches would fail on a feed that
    #     is merely still connecting. Separating the two triggers by a guessed
    #     number of minutes would only move the race, not remove it.
    #   * A machine powered on minutes earlier is still disciplining its clock.
    #     Sub-second error is corrected by slewing, which took about six
    #     minutes to close 0.61s on this hardware. Judging at second zero would
    #     refuse a machine that is seconds away from being fit.
    #
    # So poll until both are true, or until it is too late for the answer to
    # matter. The deadline is what keeps this a gate and not a wait: past it,
    # one final attempt decides and the session either starts or is refused.
    $deadline = [datetime]::ParseExact("$SessionDate $ReadyByTime", 'yyyy-MM-dd HH:mm', $null)
    $attempt  = 0
    Say "readiness  polling until $($deadline.ToString('HH:mm')) or first pass"

    while ($true) {
        $attempt++
        $readiness = & $python -u (Join-Path $root 'scripts\check_orb_session_readiness.py') `
            --rubix-db-path $RubixDbPath --require-fresh-feed 2>&1 | Out-String
        $passed = ($LASTEXITCODE -eq 0)
        if ($passed -or (Get-Date) -ge $deadline) { break }

        $why = (($readiness -split "`n" | Where-Object { $_ -match 'FAIL|feed age|median lag' }) -join ' | ') -replace '\s+', ' '
        Say "           attempt $attempt not ready: $why"
        Start-Sleep -Seconds 20
    }

    Add-Utf8LogLine -Path $log -Value $readiness
    Write-Host $readiness

    if (-not $passed) {
        $summary = ($readiness -split "`n" | Where-Object { $_ -match 'median lag|feed age|FAIL|negative' }) -join ' | '
        Refuse 'readiness' ("readiness gate failed after $attempt attempts: " +
            "$($summary -replace '\s+',' '). Starting now would produce a session of " +
            "OPENING_RANGE_NOT_READY.")
    }
    $steps['readiness'] = if ($attempt -eq 1) { 'OK' } else { "OK_AFTER_$attempt" }
    Say "readiness  OK on attempt $attempt"
}

# --- 4. run the day ----------------------------------------------------
# The orchestrator waits for the open itself, so firing before 10:00 is
# correct and a few minutes of drift in the trigger costs nothing.
$argList = @(
    '-u', (Join-Path $root 'scripts\run_orb_shadow_orchestrator.py'),
    '--rubix-db-path',  $RubixDbPath,
    '--research-root',  'data\research\orb_full_shadow',
    '--session-date',   $SessionDate,
    '--no-network'
)
Say "starting orchestrator"
& $python @argList *>&1 | Write-Utf8Log -Path $log
$code = $LASTEXITCODE

if ($code -eq 0) {
    $steps['orchestrator'] = 'OK'
    Say "orchestrator finished cleanly"
} else {
    $steps['orchestrator'] = "EXIT_$code"
    Say "orchestrator exited $code"
}

# --- 5. bank the day's microstructure ----------------------------------
# Runs whether or not the orchestrator succeeded: it summarises the session
# that just happened from the Rubix feed, and that feed does not care whether
# the ORB engine had a good day.
#
# It has to happen here rather than as its own scheduled task. A separate task
# is a separate thing to fail silently, and silent failure is what left two
# tasks broken for weeks. This one shares the log, the status file and the
# dashboard banner that already exist.
#
# Rubix produces about 50 GB of quotes a year and nothing keeps them. What
# this writes is a few hundred kilobytes a year and is the only form in which
# the session's spreads and quote intensity survive. Takes 25-30 minutes.
Say "banking microstructure for $SessionDate"
& $python -u (Join-Path $root 'scripts\bank_daily_microstructure.py') `
    --session $SessionDate --rubix-db-path $RubixDbPath *>&1 |
    Write-Utf8Log -Path $log
$bankCode = $LASTEXITCODE

if ($bankCode -eq 0) {
    $steps['microstructure'] = 'OK'
    Say "microstructure banked"
} else {
    $steps['microstructure'] = "EXIT_$bankCode"
    Say "microstructure banking exited $bankCode"
}

if ($code -eq 0 -and $bankCode -eq 0) {
    Write-Status -Outcome 'COMPLETED' -Reason 'session and microstructure both finished cleanly' `
        -Extra @{ exit_code = $code; microstructure_exit_code = $bankCode }
} elseif ($code -eq 0) {
    Write-Status -Outcome 'FAILED' -Reason "orchestrator finished but microstructure banking exited $bankCode" `
        -Extra @{ exit_code = $code; microstructure_exit_code = $bankCode }
} else {
    Write-Status -Outcome 'FAILED' -Reason "orchestrator exited $code" `
        -Extra @{ exit_code = $code; microstructure_exit_code = $bankCode }
}
exit $code
