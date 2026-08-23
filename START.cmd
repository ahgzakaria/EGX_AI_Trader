@echo off
REM Double-click this to open the Rubix Production Launcher.
REM
REM It is a thin shortcut to scripts\start_rubix_production.bat, which already
REM validates the environment and opens the launcher window you use: the one
REM with Browse for the auth file, Start Rubix & App, Stop, and Open Dashboard.
REM That launcher starts the same collector supervisor and the dashboard, so
REM there is nothing here to duplicate.
REM
REM An earlier version of this file opened the Assisted Start window instead --
REM the one the 09:45 scheduled task uses, which watches a fixed path and has no
REM Browse. That is the automated morning path, not the one you drive by hand.

cd /d "%~dp0"

call "scripts\start_rubix_production.bat" %*
exit /b %ERRORLEVEL%
