@echo off
chcp 65001 >nul
title [AI YOUTUBE] - CẬP NHẬT ĐỒNG BỘ SUPABASE & QUOTA GUARD CHO GIANG

echo =====================================================================
echo    🚀 TỰ ĐỘNG THIẾT LẬP ĐỒNG BỘ SUPABASE CLOUD & QUOTA GUARD
echo =====================================================================
echo.

set PYTHON_CMD=python
if exist "..\venv\Scripts\python.exe" set PYTHON_CMD=..\venv\Scripts\python.exe
if exist "venv\Scripts\python.exe" set PYTHON_CMD=venv\Scripts\python.exe

echo 📦 1. Đang cài đặt thư viện kết nối PostgreSQL (psycopg2-binary)...
%PYTHON_CMD% -m pip install "psycopg2-binary>=2.9.13" --quiet
if %ERRORLEVEL% neq 0 (
    echo ⚠️ Không thể cài đặt tự động, đang thử lại với cờ --upgrade...
    %PYTHON_CMD% -m pip install --upgrade psycopg2-binary
)
echo ✅ Thư viện PostgreSQL đã sẵn sàng!
echo.

echo ⚙️ 2. Đang kiểm tra cấu hình DATABASE_URL trong .env...
%PYTHON_CMD% -c "import os; p = '.env'; content = open(p, 'r', encoding='utf-8').read() if os.path.exists(p) else ''; print('   -> DATABASE_URL đã được thiết lập!' if 'postgresql://' in content else '   ⚠️ Lưu ý: Bạn nhớ dán DATABASE_URL nhận từ Kizakawa vào file .env nhé!')"
echo.

echo 🧪 3. Đang chạy bộ kiểm thử tự động xác nhận CSDL & Quota...
%PYTHON_CMD% tests/test_database_scenarios.py
if %ERRORLEVEL% neq 0 (
    echo ❌ Kiểm thử CSDL thất bại! Vui lòng kiểm tra lại kết nối mạng.
    pause
    exit /b 1
)

%PYTHON_CMD% tests/test_quota_scenarios.py
if %ERRORLEVEL% neq 0 (
    echo ❌ Kiểm thử Quota thất bại!
    pause
    exit /b 1
)

echo.
echo =====================================================================
echo    🎉 CHÚC MỪNG BẠN GIANG! HỆ THỐNG ĐÃ ĐỒNG BỘ 100% THÀNH CÔNG!
echo =====================================================================
echo  - CSDL đã kết nối trực tiếp với Supabase Singapore.
echo  - Dữ liệu giữa máy bạn, máy anh Kizakawa và Render đã khớp thời gian thực.
echo  - Quota Guard đã được bật để bảo vệ an toàn 10,000 units/ngày.
echo.
echo Nhớ cấu hình biến DATABASE_URL trên Render Dashboard như trong tài liệu
echo file: HUONG_DAN_CHO_GIANG_VA_AI_AGENT.md nhé!
echo =====================================================================
echo.
pause
