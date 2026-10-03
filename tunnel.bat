@echo off
title Cloudflare Public Tunnel - AI YouTube Studio
chcp 65001 >nul
cls

echo ======================================================================
echo    AI YOUTUBE STUDIO - DONG GOI VA CHIA SE WEB DASHBOARD RA NGOAI
echo ======================================================================
echo.
echo [1] Cloudflare Tunnel (Giao thuc HTTP/2 toc do cao - Mac dinh)
echo [2] SSH Tunnel Du Phong (localhost.run - Khong can cloudflared)
echo.
set /p choice="Chon phuong thuc [1 hoac 2, Enter = 1]: "

if "%choice%"=="2" (
    echo.
    echo Dang khoi dong SSH Tunnel...
    python tunnel.py --ssh
) else (
    echo.
    echo Dang khoi dong Cloudflare Tunnel...
    python tunnel.py
)

echo.
echo Da dong duong ham chia se.
pause
