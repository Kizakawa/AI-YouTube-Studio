@echo off
chcp 65001 > nul
title Xac Thuc Google Cloud Project 2 (20.000 Quota)
echo ======================================================================
echo   HE THONG QUOTA POOL - TANG HAN NGACH YOUTUBE LEN 20.000 UNITS
echo   Du an moi: effective-cacao-511414-q3
echo ======================================================================
echo.
call venv\Scripts\activate.bat
python auth_channel.py
pause
