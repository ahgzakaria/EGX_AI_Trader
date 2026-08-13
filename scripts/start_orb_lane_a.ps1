# Start one ORB Lane A session for a trading day, but only if it can succeed.
#
# Order matters and is the whole point of this script:
#
#   1. refuse a non-trading day        (EGX runs Sunday-Thursday)
#   2. refuse a clock that will waste the session
#   3. refuse to start after the open  (a late start cannot see the opening
#      range form, and the run gets classified PARTIAL)
#   4. only then start Lane A
#
# Step 2 exists because of 2026-08-13: the machine clock was ~2s behind the
# exchange, every quote looked like clock skew, the opening range never reached
# its required coverage, and 224 symbols reported OPENING_RANGE_NOT_READY for
# the whole session. The feed itself was perfect. Nothing about that was
# visible until the window had already passed and become unrecoverable.
#
# Usage (before 10:00 Cairo on a trading day):
#   pwsh -File scripts\start_orb_lane_a.ps1
#   pwsh -File scripts\start_orb_lane_a.ps1 -SessionDate 2026-08-16
#   pwsh -File scripts\start_orb_lane_a.ps1 -Force      # skip the open check

param(
    [string]$SessionDate = (Get-Date -Format 'yyyy-MM-dd'),
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$root   = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'venv\Scripts\python.exe'
Set-Location $root

function Fail($message) { Write-Host "REFUSED  $message" -ForegroundColor Red; exit 1 }
function Note($message)  { Write-Host "  $message" }

Write-Host "ORB Lane A - $SessionDate" -ForegroundColor Cyan

# --- 1. trading day ----------------------------------------------------
$isTradingDay = & $python -c @"
import sys; sys.path.insert(0, r'$root')
from datetime import date
from core.egx_session import is_regular_trading_day
print('yes' if is_regular_trading_day(date.fromisoformat('$SessionDate')) else 'no')
"@
if ($isTradingDay.Trim() -ne 'yes') { Fail "$SessionDate is not an EGX trading day." }
Note "trading day        OK"

# --- 2. clock and feed -------------------------------------------------
& $python -u (Join-Path $root 'scripts\check_orb_session_readiness.py')
if ($LASTEXITCODE -ne 0) {
    Fail "readiness gate failed. Starting now would produce a session of OPENING_RANGE_NOT_READY."
}

# --- 3. before the open ------------------------------------------------
# The opening range is 10:00-10:15 Cairo. Starting inside or after it means
# the range is already forming from events this run never observed live.
$open = [datetime]::ParseExact("$SessionDate 10:00", 'yyyy-MM-dd HH:mm', $null)
if ((Get-Date) -ge $open -and -not $Force) {
    Fail ("it is {0} and the session opened at 10:00. A late start cannot observe the opening range; use -Force only if you accept a PARTIAL run." -f (Get-Date -Format 'HH:mm'))
}
Note "before the open    OK"

# --- 4. start ----------------------------------------------------------
$db  = "data/research/orb_full_shadow/orb_full_shadow_$SessionDate.db"
$out = "reports/orb_validation_$SessionDate"
$log = Join-Path $root "logs\orb_lane_a_$SessionDate.log"
New-Item -ItemType Directory -Force -Path (Join-Path $root 'data\research\orb_full_shadow'), (Join-Path $root $out), (Join-Path $root 'logs') | Out-Null

$argList = @(
    '-u', (Join-Path $root 'scripts\run_orb_shadow_session.py'),
    '--follow',
    '--rubix-db-path', (Join-Path $root 'data\rubix_live_market.db'),
    '--research-db-path', $db,
    '--session-date', $SessionDate,
    '--poll-seconds', '15.0',
    '--lateness-grace-seconds', '90.0',
    '--allowed-polling-gap-seconds', '300.0',
    '--minimum-heartbeats', '60',
    '--minimum-exchange-minutes', '200',
    '--active-universe-only',
    '--stop-at-continuous-end',
    '--output-dir', $out
)
$process = Start-Process -FilePath $python -ArgumentList $argList -WorkingDirectory $root `
    -RedirectStandardOutput $log -RedirectStandardError "$log.err" `
    -WindowStyle Hidden -PassThru

Write-Host ("STARTED  pid {0} at {1}" -f $process.Id, (Get-Date -Format 'HH:mm:ss')) -ForegroundColor Green
Note "database  $db"
Note "log       $log"
Note "stops itself at 14:15 (CONTINUOUS_END_REACHED)"
