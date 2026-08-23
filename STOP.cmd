@echo off
REM Double-click this to stop everything the project is running.
REM
REM The window stays open at the end so the result is readable. Closing a
REM console window is exactly what used to kill the collector without a clean
REM shutdown, so this file never relies on you closing it to do its work --
REM by the time you see the summary, the work is done.

cd /d "%~dp0"

REM PowerShell 7 if it is here, Windows PowerShell 5.1 otherwise. The script is
REM pure ASCII so 5.1 cannot mis-read it as ANSI and fail to parse.
where pwsh >nul 2>&1
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts\stop_everything.ps1"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\stop_everything.ps1"
)

echo.
pause
