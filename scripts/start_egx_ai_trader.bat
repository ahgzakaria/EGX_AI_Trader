@echo off
setlocal
rem DEPRECATED entry point — unified with the Rubix Production Launcher V2.
rem This now opens the SAME launcher as start_rubix_production.bat:
rem   Current Research: EODHD  ·  Live: Rubix  ·  Legacy: Frozen Yahoo Snapshot (backtest only)
rem There is no Yahoo operational startup or fallback.
for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
set "VENV_PYTHON=%PROJECT_ROOT%\venv\Scripts\python.exe"
set "VENV_PYTHONW=%PROJECT_ROOT%\venv\Scripts\pythonw.exe"
set "LAUNCHER=%PROJECT_ROOT%\scripts\launch_rubix_production.py"

if not exist "%VENV_PYTHONW%" (
    echo EGX AI Trader virtual environment was not found.
    echo Expected: %VENV_PYTHONW%
    pause
    exit /b 1
)

if /I "%~1"=="--check" (
    "%VENV_PYTHON%" "%LAUNCHER%" --check
    exit /b %ERRORLEVEL%
)

cd /d "%PROJECT_ROOT%"

if /I "%~1"=="--smoke-test" (
    "%VENV_PYTHON%" "%LAUNCHER%" --check
    exit /b %ERRORLEVEL%
)

start "EGX AI Trader Launcher" "%VENV_PYTHONW%" "%LAUNCHER%" %*
exit /b 0
