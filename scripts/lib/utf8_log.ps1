<#
    Log appenders that actually write UTF-8, for the scheduled PowerShell entry
    points.

    The scheduled tasks run `powershell.exe` -- Windows PowerShell 5.1, not
    pwsh 7 -- and under 5.1 the two obvious ways to append to a log disagree
    with each other:

      Tee-Object -FilePath x -Append     writes UTF-16LE
      Add-Content -Path x -Encoding utf8 writes UTF-8 *with a BOM*

    Both were in use, sometimes in the same file: orb_automation logs alternate
    BOM'd UTF-8 lines from `Say` with UTF-16 blocks from the piped Python
    output. A UTF-16 log read as text shows every character separated by a
    null, which is why the finalizer log looked like spaced-out gibberish, and
    a file that changes encoding halfway through cannot be read correctly as
    either.

    `run_daily_orb_automation.ps1` already learned this for its status JSON and
    left a note saying so: UTF8Encoding($false), not `-Encoding utf8`, because
    Python's json.load rejects the BOM. These helpers apply the same rule to
    the logs.

    Dot-source from a script's own directory:

        . (Join-Path $PSScriptRoot 'lib\utf8_log.ps1')
#>

Set-StrictMode -Version Latest


function New-Utf8LogEncoding {
    # UTF8Encoding($false) is the BOM-less constructor. [System.Text.Encoding]::UTF8
    # emits a BOM and is not the same thing.
    New-Object System.Text.UTF8Encoding $false
}


function Add-Utf8LogLine {
    <#
    .SYNOPSIS
        Append one line to a log file as BOM-less UTF-8.

    .DESCRIPTION
        Replaces `Add-Content -Encoding utf8` at call sites that write a single
        formatted line. Creates the directory if it is missing, so a caller
        never has to guard the first write of the day.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value
    )

    $directory = Split-Path -Parent $Path
    if ($directory -and -not (Test-Path $directory)) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
    [System.IO.File]::AppendAllText($Path, $Value + [Environment]::NewLine,
                                    (New-Utf8LogEncoding))
}


function Write-Utf8Log {
    <#
    .SYNOPSIS
        Append pipeline input to a log file as BOM-less UTF-8, and pass it on.

    .DESCRIPTION
        A drop-in for `Tee-Object -FilePath <path> -Append`.

        The pass-through is not incidental. Call sites pipe a Python process's
        merged stdout and stderr through this, and what comes out the other end
        is the task's own stdout -- what Task Scheduler records and what a
        person sees when running the script by hand. Swallowing it would make
        every scheduled run silent.

        Objects are stringified the way Tee-Object would write them, so an
        ErrorRecord arriving from `2>&1` lands as its message rather than as a
        type name.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(ValueFromPipeline = $true)]$InputObject
    )

    begin {
        $encoding = New-Utf8LogEncoding
        $directory = Split-Path -Parent $Path
        if ($directory -and -not (Test-Path $directory)) {
            New-Item -ItemType Directory -Force -Path $directory | Out-Null
        }
    }

    process {
        $text = if ($null -eq $InputObject) { '' } else { [string]$InputObject }
        [System.IO.File]::AppendAllText($Path, $text + [Environment]::NewLine, $encoding)
        $InputObject
    }
}
