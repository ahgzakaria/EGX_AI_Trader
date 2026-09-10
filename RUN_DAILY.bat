@echo off
REM ===========================================================================
REM  Double-click this after you have downloaded history in MubasherTrade PRO.
REM
REM  It reads the terminal's own databases into the measured store, rebuilds
REM  the sector liquidity history from it, and then checks that the daily
REM  candle actually reached the last completed session.
REM
REM  It is safe to run twice. Nothing here places an order.
REM
REM  The window stays open at the end so the summary can be read. That is the
REM  whole reason this file exists rather than a command to type: a run whose
REM  output scrolls past and closes is a run nobody checks.
REM ===========================================================================

setlocal
cd /d "%~dp0"

REM UTF-8, so the console does not mangle the report and python does not raise
REM UnicodeEncodeError writing it. 65001 is the code page; PYTHONIOENCODING is
REM what python itself reads.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

if not exist "venv\Scripts\python.exe" (
    echo.
    echo   Cannot find venv\Scripts\python.exe under "%CD%".
    echo   This file has to sit in the project root next to the venv folder.
    echo.
    pause
    exit /b 2
)

REM Called directly, with no PowerShell in between. The script writes its own
REM UTF-8 log to logs\daily_update.log; piping it through Tee-Object instead
REM produced a UTF-16 file that reads as "A L L   G O O D", one
REM space-separated character at a time, because Windows PowerShell 5.1's
REM Tee-Object has no -Encoding switch.
"venv\Scripts\python.exe" "scripts\run_daily_update.py" %*

set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
    echo   Finished cleanly. Nothing else to do today.
) else (
    echo   Finished with problems - see the report above. Exit code %RC%.
    echo   A full log is in logs\daily_update.log
)
echo.
pause
endlocal
exit /b %RC%
