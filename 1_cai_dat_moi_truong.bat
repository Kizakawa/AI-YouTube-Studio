@echo off
chcp 65001 > nul
title Cai Dat Moi Truong - AI YouTube Studio
echo ======================================================================
echo 🛠️ CÀI ĐẶT MÔI TRƯỜNG CHẠY HỆ THỐNG AI YOUTUBE STUDIO
echo ======================================================================
echo.

:: 1. Kiểm tra Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] CHƯA TÌM THẤY PYTHON TRÊN MÁY TÍNH!
    echo 👉 Vui lòng tải và cài đặt Python (khuyên dùng Python 3.10, 3.11 hoặc 3.12):
    echo    https://www.python.org/downloads/
    echo ⚠️ LƯU Ý KHI CÀI: Nhớ tích chọn vào ô [x] "Add python.exe to PATH" !
    echo.
    pause
    exit /b
)
echo [✓] Đã tìm thấy Python:
python --version

:: 2. Kiểm tra FFmpeg
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [!] Chưa tìm thấy FFmpeg trên máy tính. Đang thử cài đặt tự động qua winget...
    winget install Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
    if %errorlevel% neq 0 (
        echo [!] Không thể tự cài FFmpeg qua winget.
        echo 👉 Vui lòng tải FFmpeg tại https://www.gyan.dev/ffmpeg/builds/ và thêm vào PATH.
    ) else (
        echo [✓] Cài đặt FFmpeg thành công!
    )
) else (
    echo [✓] Đã có FFmpeg trên hệ thống.
)

:: 3. Cài đặt các thư viện Python
echo.
echo ⏳ Đang cài đặt các thư viện cần thiết từ requirements.txt...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [X] Có lỗi khi cài đặt thư viện!
    pause
    exit /b
)

echo.
echo ======================================================================
echo 🎉 HOÀN TẤT CÀI ĐẶT! BÂY GIỜ BẠN CÓ THỂ BẤM VÀO: 2_chay_he_thong.bat
echo ======================================================================
pause
