<#
    EGX Scalping Session Validator -- scheduled launcher.

    Runs the automated post-session value-progression validator once, after the
    EGX continuous session + closing auction have completed. Designed for Windows
    Task Scheduler at 14:40 Africa/Cairo, Sun-Thu.

    Guarantees:
      * Single instance (named mutex) -- never overlaps a prior still-running run.
      * Activates the project venv.
      * Verifies the Rubix collector DB is readable BEFORE running.
      * Skips weekends (Fri/Sat) -- the validator itself also refuses non-trading dates.
      * Dated log file under logs/session_validation/.
      * Non-zero exit on failure so the scheduler records it.
      * Never prints or logs any credential/token.

    Exit codes: 0 ok/skipped-non-trading, 2 already-running, 3 db-unreadable,
                4 venv-missing, 5 validator-failed.
#>

[CmdletBinding()]
param(
    [string]$Date = "",            # optional YYYY-MM-DD; default = latest completed session
    [switch]$Force                 # pass --force-rebuild
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# --- resolve project root (this script lives in <root>/scripts) --------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent $ScriptDir
Set-Location $Root

# Log appenders that write UTF-8 under Windows PowerShell 5.1, which is what
# the scheduled task runs. See the file for why Tee-Object is not usable here.
. (Join-Path $ScriptDir 'lib\utf8_log.ps1')

# --- dated log ---------------------------------------------------------------
$LogDir = Join-Path $Root "logs\session_validation"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$stamp  = Get-Date -Format "yyyy-MM-dd_HHmmss"
$LogFile = Join-Path $LogDir "validation_$stamp.log"

function Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"), $msg
    Add-Utf8LogLine -Path $LogFile -Value $line
    Write-Host $line
}

Log "EGX Scalping Session Validator starting (root=$Root)"

# --- single-instance mutex ---------------------------------------------------
$mutex = New-Object System.Threading.Mutex($false, "Global\EGX_Scalping_Session_Validator")
if (-not $mutex.WaitOne(0)) {
    Log "Another validator instance is already running -> exit 2"
    exit 2
}

try {
    # --- weekend guard (Cairo local == server local assumed) -----------------
    $dow = (Get-Date).DayOfWeek
    if ($dow -eq "Friday" -or $dow -eq "Saturday") {
        Log "Non-trading weekday ($dow) -> nothing to validate, exit 0"
        exit 0
    }

    # --- venv ----------------------------------------------------------------
    $Py = Join-Path $Root "venv\Scripts\python.exe"
    if (-not (Test-Path $Py)) {
        Log "venv python not found at $Py -> exit 4"
        exit 4
    }

    # --- DB readable check (read-only; no credentials involved) --------------
    Log "Checking Rubix collector DB readability..."
    & $Py "scripts\run_scalping_session_validation.py" --check-db 2>&1 | Write-Utf8Log -Path $LogFile
    if ($LASTEXITCODE -ne 0) {
        Log "Rubix collector DB is not readable -> exit 3"
        exit 3
    }

    # --- run the validator ---------------------------------------------------
    $vargs = @("scripts\run_scalping_session_validation.py")
    if ($Date)  { $vargs += @("--date", $Date) }
    if ($Force) { $vargs += "--force-rebuild" }

    Log ("Running validator: python {0}" -f ($vargs -join ' '))
    & $Py @vargs 2>&1 | Write-Utf8Log -Path $LogFile
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        Log "Validator failed with exit code $rc -> exit 5"
        exit 5
    }

    Log "Validator completed successfully -> exit 0"
    exit 0
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
