@echo off
setlocal EnableExtensions EnableDelayedExpansion

for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"

set "VENV_PYTHON=%PROJECT_ROOT%\venv\Scripts\python.exe"
set "VENV_PYTHONW=%PROJECT_ROOT%\venv\Scripts\pythonw.exe"
set "LAUNCHER=%PROJECT_ROOT%\scripts\launch_rubix_production.py"
set "SUPERVISOR=%PROJECT_ROOT%\scripts\rubix_collector_supervisor.py"
set "BOOTSTRAP_LOG=%PROJECT_ROOT%\logs\rubix_launcher_bootstrap.log"

if not exist "%PROJECT_ROOT%\logs" mkdir "%PROJECT_ROOT%\logs"
if not exist "%PROJECT_ROOT%\backups" mkdir "%PROJECT_ROOT%\backups"

if not exist "%VENV_PYTHON%" (
  echo EGX AI Trader virtual environment was not found.
  echo Expected: %VENV_PYTHON%
  pause
  exit /b 10
)
if not exist "%VENV_PYTHONW%" (
  echo EGX AI Trader windowed Python executable was not found.
  echo Expected: %VENV_PYTHONW%
  pause
  exit /b 11
)
if not exist "%LAUNCHER%" (
  echo Rubix production launcher is missing.
  echo Expected: %LAUNCHER%
  pause
  exit /b 12
)
if not exist "%SUPERVISOR%" (
  echo Rubix collector supervisor is missing.
  echo Expected: %SUPERVISOR%
  pause
  exit /b 13
)

cd /d "%PROJECT_ROOT%"

if /I "%~1"=="--check" (
  "%VENV_PYTHON%" "%LAUNCHER%" --check
  set "RESULT=%ERRORLEVEL%"
  if not "!RESULT!"=="0" pause
  exit /b !RESULT!
)

if /I "%~1"=="--debug" (
  echo Starting EGX AI Trader Rubix Launcher in diagnostic mode...
  echo Project: %PROJECT_ROOT%
  echo Durable log: %PROJECT_ROOT%\logs\rubix_launcher.log
  echo.
  "%VENV_PYTHON%" "%LAUNCHER%" --debug
  set "RESULT=%ERRORLEVEL%"
  echo.
  echo Launcher exited with code !RESULT!.
  if not "!RESULT!"=="0" pause
  exit /b !RESULT!
)

rem Normal double-click mode has no console dependency. Fatal errors are shown
rem by the GUI and written to logs\rubix_launcher.log before the process exits.
start "" /D "%PROJECT_ROOT%" "%VENV_PYTHONW%" "%LAUNCHER%"
if errorlevel 1 (
  echo Failed to create the launcher process. See: %BOOTSTRAP_LOG%
  echo %DATE% %TIME% Failed to create launcher process.>>"%BOOTSTRAP_LOG%"
  pause
  exit /b 20
)

exit /b 0
