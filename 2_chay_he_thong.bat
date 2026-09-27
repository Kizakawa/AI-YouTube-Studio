@echo off
chcp 65001 > nul
title AI YouTube Studio - Multi-Channel Auto Shorts
echo ======================================================================
echo 🚀 KHỞI ĐỘNG HỆ THỐNG AI YOUTUBE STUDIO (TELEGRAM BOT + WEB STUDIO)
echo ======================================================================
echo.

set "PATH=%SystemRoot%\System32;%SystemRoot%;%PATH%"
if exist "..\venv\Scripts\python.exe" (
    "..\venv\Scripts\python.exe" run.py
) else if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" run.py
) else (
    py run.py || python run.py
)

pause
