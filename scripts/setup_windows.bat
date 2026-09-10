@echo off
setlocal
cd /d "%~dp0\.."

where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher was not found. Install Python 3.11 or later, then retry.
  pause
  exit /b 1
)

if not exist "venv\Scripts\python.exe" (
  py -3 -m venv venv
  if errorlevel 1 goto :failed
)

"venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
if exist "requirements-lock.txt" (
  "venv\Scripts\python.exe" -m pip install -r requirements-lock.txt
) else (
  "venv\Scripts\python.exe" -m pip install -r requirements.txt
)
if errorlevel 1 goto :failed

"venv\Scripts\python.exe" -c "import streamlit,plotly,pandas,numpy,yfinance,websockets,ta,sklearn,xgboost"
if errorlevel 1 goto :failed

for %%D in (data logs reports backups config) do if not exist "%%D" mkdir "%%D"
if not exist "config\settings.json" copy /y "config\settings.example.json" "config\settings.json" >nul

echo.
echo EGX AI Trader setup completed.
echo Start with scripts\start_egx_ai_trader.bat
choice /M "Create an EGX AI Trader desktop shortcut"
if errorlevel 2 goto :done
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\EGX AI Trader.lnk');$s.TargetPath='%CD%\scripts\start_egx_ai_trader.bat';$s.WorkingDirectory='%CD%';$s.Save()"
:done
pause
exit /b 0

:failed
echo.
echo Setup failed. Review the message above; no trading configuration was changed.
pause
exit /b 1
