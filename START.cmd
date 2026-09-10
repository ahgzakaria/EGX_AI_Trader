@echo off
REM Double-click this to open the EGX AI Trader dashboard.
REM
REM It is a thin shortcut to scripts\start_egx_ai_trader.bat, which validates
REM the environment and opens the launcher window: a port, Start dashboard,
REM Open in browser, Stop.
REM
REM It does NOT update any data. That is RUN_DAILY.bat, in this same folder,
REM which you run after downloading history in MubasherTrade PRO.

cd /d "%~dp0"

call "scripts\start_egx_ai_trader.bat" %*
exit /b %ERRORLEVEL%
