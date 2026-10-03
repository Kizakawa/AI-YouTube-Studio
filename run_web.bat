@echo off
chcp 65001 > nul
title AI YouTube Studio - Web Only (Local + Wi-Fi)
echo ======================================================================
echo 🌐 KHỞI ĐỘNG WEB DASHBOARD LOCAL (PHÁT SÓNG MẠNG WI-FI / LAN)
echo 👉 Chế độ này KHÔNG kết nối Telegram Bot nên không lo bị trùng lặp bot!
echo 👉 Hỗ trợ truy cập cả trên máy tính lẫn điện thoại cùng mạng Wi-Fi.
echo ======================================================================

set "PATH=%SystemRoot%\System32;%SystemRoot%;%PATH%"

set "PY_CMD="
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" -c "exit(0)" >nul 2>&1
    if not errorlevel 1 (
        set "PY_CMD=venv\Scripts\python.exe"
    )
)
if not defined PY_CMD if exist "..\venv\Scripts\python.exe" (
    "..\venv\Scripts\python.exe" -c "exit(0)" >nul 2>&1
    if not errorlevel 1 (
        set "PY_CMD=..\venv\Scripts\python.exe"
    )
)

if defined PY_CMD (
    echo [✓] Đang khởi chạy với: %PY_CMD%
    "%PY_CMD%" run.py --web-only
) else (
    echo [!] Không tìm thấy môi trường ảo venv hoặc venv bị lỗi đường dẫn máy cũ.
    echo [i] Đang thử khởi chạy bằng Python hệ thống...
    py run.py --web-only 2>nul || python run.py --web-only
    if errorlevel 1 (
        echo.
        echo [X] Không thể khởi chạy! Vui lòng bấm vào "1_cai_dat_moi_truong.bat" để tạo môi trường.
    )
)

pause
