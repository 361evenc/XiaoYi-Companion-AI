@echo off
cd /d "%~dp0"
title XiaoYi Vision - 24h frame source run

echo ============================================================
echo   XiaoYi Vision - 24 hour acceptance run
echo.
echo   Log file : run_24h.log
echo   Duration : 24 hours, then stops itself
echo.
echo   DO NOT CLOSE THIS WINDOW.
echo   Closing it = the run ends early.
echo ============================================================
echo.

rem 优先用脚本同级的 venv，其次用 PATH 里的 python
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%~dp0..\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" frame_source.py --interval 2 --duration 24 --log run_24h.log

echo.
echo ============================================================
echo   Run finished. Check run_24h.log
echo ============================================================
pause
