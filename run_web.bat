@echo off
chcp 65001 > nul
title AI YouTube Studio - Web Only Dev Server
echo ======================================================================
echo 🌐 KHỞI ĐỘNG WEB DASHBOARD LOCAL (CHẾ ĐỘ WEB-ONLY)
echo 👉 Chế độ này KHÔNG kết nối Telegram Bot nên KHÔNG BAO GIỜ bị chập bot!
echo 👉 Địa chỉ Web Studio: http://localhost:8000
echo ======================================================================

set "PATH=%SystemRoot%\System32;%SystemRoot%;%PATH%"
if exist "..\venv\Scripts\python.exe" (
    "..\venv\Scripts\python.exe" run.py --web-only
) else if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" run.py --web-only
) else (
    python run.py --web-only
)

pause
