@echo off
chcp 65001 > nul
title AI YouTube Studio - Web Only (Local + Wi-Fi)
echo ======================================================================
echo 🌐 KHỞI ĐỘNG WEB DASHBOARD LOCAL (PHÁT SÓNG MẠNG WI-FI / LAN)
echo 👉 Chế độ này KHÔNG kết nối Telegram Bot nên không lo bị trùng lặp bot!
echo 👉 Hỗ trợ truy cập cả trên máy tính lẫn điện thoại cùng mạng Wi-Fi.
echo ======================================================================

set "PATH=%SystemRoot%\System32;%SystemRoot%;%PATH%"
if exist "..\venv\Scripts\python.exe" (
    "..\venv\Scripts\python.exe" run.py --web-only
) else if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" run.py --web-only
) else (
    py run.py --web-only || python run.py --web-only
)

pause
