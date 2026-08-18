# One-time elevation step: let the daily automation fix the clock by itself.
#
# `w32tm /resync` requires administrator rights. The daily task runs as the
# interactive user at Limited level, so its resync attempt is refused and the
# clock is only ever as good as the last time a human remembered to correct it.
#
# That matters more here than it sounds. On 2026-08-13 the machine clock ran
# ~2s behind the exchange, every quote looked like clock skew, the opening
# range never reached its required coverage, and 224 symbols reported
# OPENING_RANGE_NOT_READY for the whole session. The feed was perfect. The
# session was unrecoverable by the time anything showed it.
#
# The drift is not a one-off. On 2026-08-18, over 45 minutes, the share of
# negative-lag samples went 1% -> 5% -> 10% on a machine that had synced
# cleanly that morning.
#
# Raising the task to Highest lets it run `w32tm /resync` at 09:40 each
# trading morning, before the gate that would otherwise refuse the session.
# The readiness gate still decides: this makes the refusal rarer, it does not
# make a bad clock acceptable.
#
# The second half of the problem is that a resync alone does not fix this
# machine. On 2026-08-18 the resync reported success, the clock was disciplined
# by pool.ntp.org, and four independent servers still agreed the machine was
# 0.61s behind. The stock configuration explains it exactly:
#
#   UpdateInterval        360000   phase correction applied ~once an hour
#   MinPollInterval       10       1024s
#   MaxPollInterval       15       32768s  (9 hours)
#   MaxAllowedPhaseOffset 1        under 1s is slewed, not stepped
#
# An offset of 0.61s is under the step threshold, so Windows slews it -- at a
# correction rate of once per hour. It never catches up. Those defaults target
# the 1-2 second accuracy that is fine for file timestamps and Kerberos, and
# useless for measuring feed lag in tenths of a second.
#
# So this also applies Microsoft's documented high-accuracy tuning. The
# previous values are saved to data/runtime/w32time_previous_config.json and
# `-Revert` puts them back.
#
# MaxAllowedPhaseOffset is deliberately left alone. Keeping sub-second
# corrections as a smooth slew rather than a step matters here: a session
# measures received_at against exchange timestamps continuously, and a clock
# that jumps backwards mid-session would corrupt that measurement in a way a
# gradual correction does not.
#
# Run once, from an ELEVATED PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File scripts\enable_orb_clock_selfheal.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\enable_orb_clock_selfheal.ps1 -Revert
#
# Idempotent -- running it again re-verifies and changes only what has drifted
# back to a default.

param(
    [string]$TaskName = 'EGX ORB Full Shadow Automation',
    # Both morning tasks fire at the same minute. The readiness gate inside
    # run_daily_orb_automation.ps1 polls until the feed is live rather than
    # judging once, so the collector starting alongside it is not a race.
    # Earlier is worse, not better: this machine is powered on by hand shortly
    # before the session, and a trigger the machine sleeps through becomes a
    # missed task that Windows may delay by up to ten minutes -- which can
    # land after the 10:00 open.
    [string]$StartTime = '09:45',
    [switch]$Revert
)

$ErrorActionPreference = 'Stop'

$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "REFUSED  this must run from an elevated PowerShell." -ForegroundColor Red
    Write-Host "         Right-click PowerShell -> Run as administrator, then:"
    Write-Host "         powershell -ExecutionPolicy Bypass -File $PSCommandPath"
    exit 1
}

# --- 1. let the task resync the clock ----------------------------------
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "REFUSED  scheduled task '$TaskName' does not exist." -ForegroundColor Red
    exit 1
}

if ($task.Principal.RunLevel -eq 'Highest') {
    Write-Host "  run level        already Highest"
} else {
    $userId = $task.Principal.UserId
    if (-not $userId) { $userId = "$env:COMPUTERNAME\$env:USERNAME" }
    $elevated = New-ScheduledTaskPrincipal -UserId $userId `
        -LogonType $task.Principal.LogonType -RunLevel Highest
    Set-ScheduledTask -TaskName $TaskName -Principal $elevated | Out-Null
    Write-Host "  run level        Limited -> Highest" -ForegroundColor Green
}

# Raising the task to Highest also means only an elevated shell can change its
# schedule from here on, so the trigger time is set in the same step.
$currentStart = $task.Triggers[0].StartBoundary.Substring(11, 5)
if ($currentStart -eq $StartTime) {
    Write-Host "  trigger          already $StartTime"
} else {
    $trigger = New-ScheduledTaskTrigger -Weekly -At $StartTime `
        -DaysOfWeek Sunday, Monday, Tuesday, Wednesday, Thursday
    Set-ScheduledTask -TaskName $TaskName -Trigger $trigger | Out-Null
    Write-Host "  trigger          $currentStart -> $StartTime (Sun-Thu)" -ForegroundColor Green
}

# --- 2. tune w32time for sub-second accuracy ---------------------------
$configKey = 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Config'
$clientKey = 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpClient'

# Microsoft's high-accuracy values. Each one is here because a stock default
# below it is what let 0.61s of error survive a successful resync.
$wanted = @(
    @{ Key = $configKey; Name = 'UpdateInterval';       Value = 100
       Why = 'apply phase correction ~every second instead of ~every hour' },
    @{ Key = $configKey; Name = 'FrequencyCorrectRate'; Value = 2
       Why = 'converge on the local oscillator drift faster (was 4)' },
    @{ Key = $configKey; Name = 'PhaseCorrectRate';     Value = 1
       Why = 'correct phase error at full rate' },
    @{ Key = $configKey; Name = 'MinPollInterval';      Value = 6
       Why = 'poll no less often than 64s (was 1024s)' },
    @{ Key = $configKey; Name = 'MaxPollInterval';      Value = 8
       Why = 'poll at least every 256s (was 32768s, over nine hours)' },
    # 256s rather than Microsoft's 64s: this is one machine against public
    # pool servers, and four minutes of drift at the measured 0.14s/hour is
    # about 10ms. Polite and far tighter than anything this needs.
    @{ Key = $clientKey; Name = 'SpecialPollInterval';  Value = 256
       Why = 'poll the configured peers every 256s (was 3600s)' }
)

$backupDir  = Join-Path (Split-Path -Parent $PSScriptRoot) 'data\runtime'
$backupPath = Join-Path $backupDir 'w32time_previous_config.json'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

if ($Revert) {
    if (-not (Test-Path $backupPath)) {
        Write-Host "REFUSED  no saved configuration at $backupPath." -ForegroundColor Red
        exit 1
    }
    $saved = Get-Content $backupPath -Raw | ConvertFrom-Json
    foreach ($entry in $saved.entries) {
        if ($null -eq $entry.previous) {
            Remove-ItemProperty -Path $entry.key -Name $entry.name -ErrorAction SilentlyContinue
            Write-Host "  restored         $($entry.name) -> (removed)"
        } else {
            Set-ItemProperty -Path $entry.key -Name $entry.name -Value ([int]$entry.previous) -Type DWord
            Write-Host "  restored         $($entry.name) -> $($entry.previous)"
        }
    }
    Restart-Service w32time
    Write-Host "reverted. w32time restarted." -ForegroundColor Green
    exit 0
}

$changed = @()
$record  = @()
foreach ($item in $wanted) {
    $current = (Get-ItemProperty -Path $item.Key -Name $item.Name -ErrorAction SilentlyContinue).$($item.Name)
    $record += [ordered]@{ key = $item.Key; name = $item.Name; previous = $current }
    if ($current -eq $item.Value) {
        Write-Host ("  {0,-22} already {1}" -f $item.Name, $item.Value)
        continue
    }
    Set-ItemProperty -Path $item.Key -Name $item.Name -Value $item.Value -Type DWord
    Write-Host ("  {0,-22} {1} -> {2}   ({3})" -f $item.Name, $current, $item.Value, $item.Why) -ForegroundColor Green
    $changed += $item.Name
}

# Only write the backup the first time, or a second run would record the
# already-tuned values as the thing to revert to.
if (-not (Test-Path $backupPath)) {
    @{ saved_at = (Get-Date).ToString('o'); entries = $record } |
        ConvertTo-Json -Depth 5 |
        ForEach-Object { [System.IO.File]::WriteAllText($backupPath, $_, (New-Object System.Text.UTF8Encoding $false)) }
    Write-Host "  previous values  saved to $backupPath"
}

if ($changed.Count) {
    Restart-Service w32time
    Write-Host "  w32time          restarted to pick up $($changed.Count) change(s)"
    Start-Sleep -Seconds 3
}

# --- 3. correct the clock now ------------------------------------------
# The task will do this itself from tomorrow; doing it here means the machine
# is correct before the next session rather than after it.
& w32tm.exe /resync /force 2>&1 | ForEach-Object { Write-Host "  resync           $_" }

Start-Sleep -Seconds 5

# --- 4. show what the clock actually says -------------------------------
Write-Host ""
Write-Host "verification" -ForegroundColor Cyan
$status = & w32tm.exe /query /status 2>&1
$status | Select-String 'Source|Last Successful Sync Time|Stratum' |
    ForEach-Object { Write-Host "  $_" }

$source = ($status | Select-String 'Source:') -replace '.*Source:\s*', ''
if ($source -match 'Local CMOS Clock') {
    Write-Host ""
    Write-Host "WARN  the clock is still undisciplined. w32time may not have " -ForegroundColor Yellow -NoNewline
    Write-Host "reached a server yet;" -ForegroundColor Yellow
    Write-Host "      re-run in a minute, and check that w32time is running." -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "OK  clock is disciplined by $($source.Trim())" -ForegroundColor Green
}

# --- 5. offset against several independent servers ----------------------
# One server can be wrong or the path to it asymmetric; agreement across four
# is what makes an offset a fact rather than a reading.
Write-Host ""
Write-Host "offset (positive = this machine is behind real time)" -ForegroundColor Cyan
$offsets = @()
foreach ($server in 'time.google.com', 'pool.ntp.org', 'time.windows.com', 'time.cloudflare.com') {
    $line = & w32tm.exe /stripchart /computer:$server /samples:2 /dataonly 2>&1 | Select-Object -Last 1
    Write-Host ("  {0,-22} {1}" -f $server, ($line -replace '\s+', ' '))
    if ("$line" -match '([+-]\d+\.\d+)s') { $offsets += [double]$Matches[1] }
}

if ($offsets.Count) {
    $worst = ($offsets | ForEach-Object { [Math]::Abs($_) } | Measure-Object -Maximum).Maximum
    Write-Host ""
    if ($worst -lt 0.10) {
        Write-Host ("OK  worst offset {0:N3}s. The clock will not manufacture negative lag." -f $worst) -ForegroundColor Green
    } elseif ($worst -lt 0.30) {
        Write-Host ("OK  worst offset {0:N3}s and still converging -- the slew is gradual by" -f $worst) -ForegroundColor Green
        Write-Host "    design. Re-run this in a few minutes to watch it settle."
    } else {
        Write-Host ("WARN  worst offset is still {0:N3}s." -f $worst) -ForegroundColor Yellow
        Write-Host "      Correction is a slew, not a jump, so give it several minutes and"
        Write-Host "      re-run. If it does not fall, the readiness gate will refuse the"
        Write-Host "      session rather than let it run on a clock that invents skew."
    }
}

Write-Host ""
Write-Host "'$TaskName' runs at level: $((Get-ScheduledTask -TaskName $TaskName).Principal.RunLevel)"
Write-Host "check any time with:  venv\Scripts\python.exe scripts\check_orb_session_readiness.py"
