@echo off
cd /d "%~dp0"
title XiaoYi Companion AI

echo ============================================================
echo   XiaoYi Companion AI - One-click launcher
echo   Backend  : FastAPI on http://127.0.0.1:8010
echo   Frontend : Vite auto-starts with backend (default 5174)
echo   Browser will open http://localhost:5174 automatically.
echo   Press Ctrl+C or close this window to stop BOTH services.
echo ============================================================
echo.

rem Open the browser after a short delay so the services are ready.
start "" /b cmd /c "ping 127.0.0.1 -n 12 >nul & start http://localhost:5174"

python run.py

echo.
echo XiaoYi has stopped.
pause
