<#
    Generic launcher for an EXPECTED_RANGE_SCALPER orchestration stage.

    Activates the project venv, runs the given Python stage script, writes a dated
    log, and returns a non-zero exit code on real failure. Records only -- never
    places an order, never enables production.

    Usage (from Task Scheduler):
      run_ers_stage.ps1 -Stage pre_session    -Script run_expected_range_pre_session.py
      run_ers_stage.ps1 -Stage live_monitor   -Script run_expected_range_live_monitor.py
      run_ers_stage.ps1 -Stage finalizer      -Script run_expected_range_outcome_finalizer.py

    Weekend guard (Fri/Sat) is enforced here; configured EGX holidays are honored
    inside the Python stage (it reads settings and refuses non-trading days).
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Stage,
    [Parameter(Mandatory = $true)][string]$Script,
    [string]$ExtraArgs = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
Set-Location $Root

# Log appenders that write UTF-8 under Windows PowerShell 5.1, which is what
# the scheduled task runs. See the file for why Tee-Object is not usable here.
. (Join-Path $ScriptDir 'lib\utf8_log.ps1')

$LogDir = Join-Path $Root "logs\expected_range_paper"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd"
$LogFile = Join-Path $LogDir ("{0}_{1}.log" -f $Stage, $stamp)

function Log([string]$m) {
    $line = "{0}  [{1}]  {2}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"), $Stage, $m
    Add-Utf8LogLine -Path $LogFile -Value $line
    Write-Host $line
}

# Weekend guard (Cairo == local assumed on this host).
$dow = (Get-Date).DayOfWeek
if ($dow -eq "Friday" -or $dow -eq "Saturday") {
    Log "Non-trading weekday ($dow) -> nothing to do, exit 0"
    exit 0
}

$Py = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { Log "venv python missing at $Py -> exit 4"; exit 4 }

Log "starting stage '$Stage' ($Script) $ExtraArgs"
$argList = @((Join-Path "scripts" $Script))
if ($ExtraArgs) { $argList += $ExtraArgs.Split(" ") }

& $Py @argList 2>&1 | Write-Utf8Log -Path $LogFile
$rc = $LASTEXITCODE
if ($rc -ne 0) { Log "stage '$Stage' failed rc=$rc"; exit $rc }
Log "stage '$Stage' completed ok"
exit 0
