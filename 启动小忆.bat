@echo off
cd /d "%~dp0"
title XiaoYi Companion AI

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv not found: venv\Scripts\python.exe
    echo Fix:  python -m venv venv
    echo Then: venv\Scripts\python.exe -m pip install -r requirements_lite.txt
    pause
    exit /b 1
)

echo ============================================================
echo   XiaoYi Companion AI - starting, please wait ...
echo   The browser will open http://localhost:7100 automatically.
echo   Do NOT close this window. Closing it stops XiaoYi.
echo ============================================================
echo.

rem Open the browser after a short delay so the server is ready.
rem (ping is used as a portable sleep; it works even if PATH has MSYS tools.)
start "" /b cmd /c "ping 127.0.0.1 -n 7 >nul & start http://localhost:7100"

venv\Scripts\python.exe app.py --port 7100 --host 127.0.0.1

echo.
echo XiaoYi has stopped.
pause
