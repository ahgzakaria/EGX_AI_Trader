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
    [switch]$SkipReadiness
)

$ErrorActionPreference = 'Continue'
$root   = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'venv\Scripts\python.exe'
Set-Location $root

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
    # the automation run today?" — a question that previously had no answer
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
    # half-written status that a reader would trust — this machine lost mains
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
    Add-Content -Path $log -Value $line -Encoding utf8
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
# attempt, not a guarantee — the readiness gate below is what actually
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
    $readiness = & $python -u (Join-Path $root 'scripts\check_orb_session_readiness.py') `
        --rubix-db-path $RubixDbPath --require-fresh-feed 2>&1 | Out-String
    Add-Content -Path $log -Value $readiness -Encoding utf8
    Write-Host $readiness
    if ($LASTEXITCODE -ne 0) {
        $summary = ($readiness -split "`n" | Where-Object { $_ -match 'median lag|feed age|FAIL|negative' }) -join ' | '
        Refuse 'readiness' "readiness gate failed: $($summary -replace '\s+',' '). Starting now would produce a session of OPENING_RANGE_NOT_READY."
    }
    $steps['readiness'] = 'OK'
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
& $python @argList *>&1 | Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE

if ($code -eq 0) {
    $steps['orchestrator'] = 'OK'
    Say "orchestrator finished cleanly"
    Write-Status -Outcome 'COMPLETED' -Reason 'orchestrator finished cleanly' -Extra @{ exit_code = $code }
} else {
    $steps['orchestrator'] = "EXIT_$code"
    Say "orchestrator exited $code"
    Write-Status -Outcome 'FAILED' -Reason "orchestrator exited $code" -Extra @{ exit_code = $code }
}
exit $code
