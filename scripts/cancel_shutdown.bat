@echo off
chcp 65001 >nul
echo ====================================================
echo   HUY LENH SHUTDOWN LAPTOP VA DUNG WATCHDOG
echo ====================================================
shutdown /a
if %errorlevel% equ 0 (
    echo [OK] Da huy bo lenh hen gio shutdown Windows thanh cong!
) else (
    echo [INFO] Khong co lenh shutdown nao dang cho.
)

powershell -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'watchdog_shutdown' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('[OK] Da dung tien trinh watchdog PID ' + $_.ProcessId) }"
echo ====================================================
echo Hoan tat!
pause
