@echo off
chcp 65001 >nul
title Nạp Cấu Hình Dự Án AI YouTube

echo ========================================================
echo   ĐANG TỰ ĐỘNG NẠP CẤU HÌNH BÍ MẬT (SECRETS & API KEYS)
echo ========================================================
echo.

if exist "..\venv\Scripts\python.exe" (
    "..\venv\Scripts\python.exe" import_secrets.py
) else if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" import_secrets.py
) else (
    python import_secrets.py
)

echo.
echo ========================================================
pause
