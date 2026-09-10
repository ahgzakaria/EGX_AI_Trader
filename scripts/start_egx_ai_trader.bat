@echo off
setlocal
rem Opens the EGX AI Trader dashboard launcher.
rem
rem This used to open the Rubix Production Launcher, which started a price
rem collector alongside the dashboard. The feed was retired on 2026-09-10; the
rem launcher now opens the dashboard and nothing else, and the daily data
rem update is a separate one-click step: RUN_DAILY.bat in the project root.
for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
set "VENV_PYTHON=%PROJECT_ROOT%\venv\Scripts\python.exe"
set "VENV_PYTHONW=%PROJECT_ROOT%\venv\Scripts\pythonw.exe"
set "LAUNCHER=%PROJECT_ROOT%\scripts\launch_dashboard.py"

if not exist "%VENV_PYTHONW%" (
    echo EGX AI Trader virtual environment was not found.
    echo Expected: %VENV_PYTHONW%
    pause
    exit /b 1
)
if not exist "%LAUNCHER%" (
    echo The dashboard launcher is missing.
    echo Expected: %LAUNCHER%
    pause
    exit /b 12
)

cd /d "%PROJECT_ROOT%"

if /I "%~1"=="--check" (
    "%VENV_PYTHON%" "%LAUNCHER%" --check
    exit /b %ERRORLEVEL%
)
if /I "%~1"=="--smoke-test" (
    "%VENV_PYTHON%" "%LAUNCHER%" --check
    exit /b %ERRORLEVEL%
)

rem Windowed, so a double-click has no console behind it. Failures reach a
rem dialog and logs\dashboard_launcher.log.
start "EGX AI Trader" "%VENV_PYTHONW%" "%LAUNCHER%" %*
exit /b 0
