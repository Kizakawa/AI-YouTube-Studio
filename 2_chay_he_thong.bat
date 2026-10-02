@echo off
chcp 65001 > nul
title AI YouTube Studio - Multi-Channel Auto Shorts
echo ======================================================================
echo 🚀 KHỞI ĐỘNG HỆ THỐNG AI YOUTUBE STUDIO (TELEGRAM BOT + WEB STUDIO)
echo ======================================================================
echo.

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
    "%PY_CMD%" run.py
) else (
    echo [!] Không tìm thấy môi trường ảo venv hoặc venv bị lỗi đường dẫn máy cũ.
    echo [i] Đang thử khởi chạy bằng Python hệ thống...
    py run.py 2>nul || python run.py
    if errorlevel 1 (
        echo.
        echo [X] Không thể khởi chạy! Vui lòng bấm vào "1_cai_dat_moi_truong.bat" để tạo môi trường.
    )
)

pause
