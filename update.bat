@echo off
rem Kyudai course finder: refresh data, rebuild, and ask before publishing.
cd /d "%~dp0src"
set PYTHONUTF8=1
py run.py update %*
echo.
pause
