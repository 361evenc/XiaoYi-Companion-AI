@echo off
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"
"C:\Users\ASUS\AppData\Local\Programs\Python\Python312\python.exe" -u app.py >> "%~dp0_run.log" 2>&1
