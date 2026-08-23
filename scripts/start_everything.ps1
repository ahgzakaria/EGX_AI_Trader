# Start the day's runtime: the collector, and the dashboard.
#
# This does NOT invent a way to start the collector. It launches the same
# Rubix Assisted Start the 09:45 scheduled task launches, with the same
# arguments, so there is exactly one start path. Two paths would drift, and the
# drift would surface as a duplicate collector or a morning where nothing runs.
#
# It cannot do the Rubix login for you. That step is yours, it always was, and
# the auth frame it writes is only good for fifteen minutes. What this removes
# is everything around it: the assisted-start window watches the file and
# starts the collector the moment a fresh frame appears.
#
# Pure ASCII on purpose. Windows PowerShell 5.1 reads a BOM-less UTF-8 file as
# ANSI, and one non-ASCII character in a string makes it a parse error.

[CmdletBinding()]
param(
    # Report what would happen and change nothing.
    [switch]$WhatIfOnly,
    # Bring up the collector only, leaving the dashboard alone.
    [switch]$NoDashboard,
    [int]$DashboardPort = 8501
)

function Complete-Run([int]$code) {
    # Hold the window open where there is a real console to hold, then leave.
    #
    # Every exit goes through here. A previous version pasted the hold before
    # each top-level "exit", which silently skipped the ones indented inside an
    # if block -- including the success path, the one that runs on a normal day.
    # IsInputRedirected is false only when a person is actually there, so an
    # automated run returns instead of waiting for a keypress nobody will make.
    try {
        if (-not [Console]::IsInputRedirected) {
            Write-Host "Press Enter to close this window..." -ForegroundColor DarkGray
            [void][Console]::ReadLine()
        }
    } catch { }
    exit $code
}

$ErrorActionPreference = 'Stop'
$root       = Split-Path -Parent $PSScriptRoot
$python     = Join-Path $root 'venv\Scripts\python.exe'
$pythonw    = Join-Path $root 'venv\Scripts\pythonw.exe'
$authFrame  = 'C:\secure-temp\rubix-price-auth-frame.txt'
$adapter    = 'C:\Users\ahgza\OneDrive\Documents\Scrapping\rubix_feed'
$stopFlag   = Join-Path $root 'data\runtime\stop_requested.flag'

function Get-AssistedStartProcess {
    Get-CimInstance Win32_Process -Filter "name='python.exe' OR name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*run_rubix_assisted_start*' }
}

function Get-DashboardProcess {
    Get-CimInstance Win32_Process -Filter "name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*streamlit run*' -and $_.CommandLine -like "*EGX_AI_Trader*" }
}

# --- What is true right now ------------------------------------------------
# Both verdicts come from the project's own checks, never from a second rule
# written here: the supervisor's PID record (which also matches the recorded
# process start time, so a reused PID is never mistaken for the collector) and
# the official auth-frame validator.
$raw = & $python (Join-Path $root 'scripts\runtime_status.py') --auth-frame $authFrame 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Could not read the runtime status. Nothing was started." -ForegroundColor Red
    Write-Host $raw
    Complete-Run 1
}
$status = $raw | ConvertFrom-Json

$collectorUp = $status.collector.running
$frameUsable = $status.auth_frame.usable
$dashboard   = @(Get-DashboardProcess)
$assisted    = @(Get-AssistedStartProcess)

Write-Host "Right now:" -ForegroundColor Cyan
if ($collectorUp -eq $true) {
    Write-Host "  collector : running (PID $($status.collector.pid))" -ForegroundColor Green
} elseif ($null -eq $collectorUp) {
    Write-Host "  collector : UNKNOWN -- $($status.collector.error)" -ForegroundColor Yellow
} else {
    Write-Host "  collector : not running"
}
if ($frameUsable -eq $true) {
    Write-Host "  auth frame: fresh, collector can start now" -ForegroundColor Green
} elseif ($null -eq $frameUsable) {
    Write-Host "  auth frame: UNKNOWN -- $($status.auth_frame.reason)" -ForegroundColor Yellow
} else {
    Write-Host "  auth frame: $($status.auth_frame.status) -- $($status.auth_frame.reason)" -ForegroundColor Yellow
}
Write-Host ("  dashboard : {0}" -f $(if ($dashboard.Count -gt 0) { "running (PID $($dashboard[0].ProcessId))" } else { "not running" }))
if ($assisted.Count -gt 0) {
    Write-Host "  assisted start window: already open (PID $($assisted[0].ProcessId))"
}

if ($WhatIfOnly) {
    Write-Host "`n-WhatIfOnly: nothing was started." -ForegroundColor Yellow
    Complete-Run 0
}

# --- The collector ---------------------------------------------------------
if ($collectorUp -eq $true) {
    Write-Host "`nCollector is already up. Not starting a second one." -ForegroundColor Green
} elseif ($null -eq $collectorUp) {
    # Unknown is not "no". Starting on an unreadable answer is how you get two
    # supervisors fighting over one database.
    Write-Host "`nCollector state could not be read, so it was NOT started." -ForegroundColor Red
    Write-Host "  Check data\rubix_supervisor.pid.json before starting anything by hand."
} elseif ($assisted.Count -gt 0) {
    # Two windows watching the same frame file both fire when it lands, and both
    # try to start a collector. The lock stops the second from running, but it
    # stops it by failing -- logging supervisor_duplicate_blocked at 09:10, the
    # one moment nothing should look wrong.
    Write-Host "`nAn assisted start window is already open. Not opening another." -ForegroundColor Green
    Write-Host "  It is watching for the frame. Do the Rubix export in that window's own flow."
} else {
    # A flag left behind by an interrupted stop. The supervisor already ignores
    # one written before it started, but clearing it removes the ambiguity.
    if (Test-Path $stopFlag) {
        Remove-Item $stopFlag -Force -ErrorAction SilentlyContinue
        Write-Host "`nCleared a leftover stop flag." -ForegroundColor Yellow
    }

    Write-Host "`nOpening Rubix Assisted Start..." -ForegroundColor Cyan
    $assistedArgs = @(
        (Join-Path $root 'scripts\run_rubix_assisted_start.py'),
        '--runtime-root', $root,
        '--auth-frame', $authFrame,
        '--adapter-path', $adapter,
        '--auto-start',
        '--countdown-seconds', '10'
    )
    Start-Process -FilePath $pythonw -ArgumentList $assistedArgs -WorkingDirectory $root | Out-Null

    if ($frameUsable -eq $true) {
        Write-Host "  The frame is fresh: it will start the collector on its countdown." -ForegroundColor Green
    } else {
        Write-Host "  Waiting for a fresh auth frame." -ForegroundColor Yellow
        Write-Host "  DO THE RUBIX EXPORT NOW. The window starts the collector by itself"
        Write-Host "  the moment the frame lands. It is good for 15 minutes."
    }
}

# --- The dashboard ---------------------------------------------------------
# Started separately because Assisted Start deliberately never launches it.
if ($NoDashboard) {
    Write-Host "`n-NoDashboard: the dashboard was left alone."
} elseif ($dashboard.Count -gt 0) {
    Write-Host "`nDashboard is already up: http://localhost:$DashboardPort" -ForegroundColor Green
} else {
    Write-Host "`nStarting the dashboard..." -ForegroundColor Cyan
    $dashArgs = @('-m', 'streamlit', 'run', 'app.py',
                  '--server.port', "$DashboardPort",
                  '--server.headless', 'true',
                  '--browser.gatherUsageStats', 'false')
    Start-Process -FilePath $python -ArgumentList $dashArgs -WorkingDirectory $root -WindowStyle Hidden | Out-Null

    # Report the port answering, not the process existing: a server that failed
    # to bind is still a process.
    $ready = $false
    foreach ($attempt in 1..20) {
        Start-Sleep -Milliseconds 800
        try {
            Invoke-WebRequest -Uri "http://localhost:$DashboardPort/_stcore/health" `
                -TimeoutSec 3 -UseBasicParsing | Out-Null
            $ready = $true
            break
        } catch { }
    }
    if ($ready) {
        Write-Host "  Ready: http://localhost:$DashboardPort" -ForegroundColor Green
    } else {
        Write-Host "  The dashboard did not answer on port $DashboardPort within 16s." -ForegroundColor Red
        Write-Host "  Something may already hold that port. Nothing else was changed."
    }
}

Write-Host ""

Complete-Run 0