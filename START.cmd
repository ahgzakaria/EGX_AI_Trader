@echo off
REM Double-click this to bring up the day's runtime.
REM
REM It opens the same Rubix Assisted Start the 09:45 scheduled task opens, so
REM there is one start path and not two. The Rubix login is still yours to do:
REM the window watches for the frame and starts the collector by itself the
REM moment a fresh one lands.
REM
REM The window stays open at the end so the result is readable.

cd /d "%~dp0"

where pwsh >nul 2>&1
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts\start_everything.ps1"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start_everything.ps1"
)

REM The window is held open by the PowerShell script itself, which keeps
REM working whichever way this file was launched.
