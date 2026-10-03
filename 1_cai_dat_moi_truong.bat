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

:: 1.2 Tự động nạp cấu hình bí mật nếu người dùng copy secrets_config.zip vào
if exist "secrets_config.zip" (
    echo.
    echo [✓] Tìm thấy file secrets_config.zip!
    echo ⏳ Đang tự động nạp .env, channels.yaml và token OAuth vào thư mục...
    tar -xf secrets_config.zip 2>nul || powershell -command "Expand-Archive -Path 'secrets_config.zip' -DestinationPath '.' -Force"
    echo [✓] Đã nạp thành công toàn bộ cấu hình và token YouTube!
)

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

:: 3. Khởi tạo môi trường ảo Python (venv) sạch cho máy này
echo.
echo ⏳ Đang kiểm tra / khởi tạo môi trường ảo Python (venv)...
if not exist "venv\Scripts\python.exe" (
    echo [i] Đang khởi tạo môi trường ảo tại thư mục venv...
    python -m venv venv 2>nul || py -m venv venv
)

:: 4. Cài đặt các thư viện Python
echo.
echo ⏳ Đang cài đặt các thư viện cần thiết từ requirements.txt...
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" -m pip install --upgrade pip
    "venv\Scripts\python.exe" -m pip install -r requirements.txt
) else (
    pip install -r requirements.txt
)

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
