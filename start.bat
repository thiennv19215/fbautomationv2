@echo off
title FBEM Bridge - Headless Facebook Automation Daemon
chcp 65001 > nul
cls

echo =======================================================
echo     ⚡ FBEM BRIDGE - HEADLESS FACEBOOK AUTOMATION
echo =======================================================
echo.

echo [*] Đang dọn dẹp và giải phóng các cổng kết nối cũ (:47102 ^& :9224)...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :47102 ^| findstr LISTENING 2^>nul') do (
    taskkill /PID %%a /F /T >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :9224 ^| findstr LISTENING 2^>nul') do (
    taskkill /PID %%a /F /T >nul 2>&1
)

set "PY_CMD="
if exist ".venv\Scripts\python.exe" (
    set "PY_CMD=.venv\Scripts\python.exe"
) else (
    where uv >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set "PY_CMD=uv run python"
    ) else (
        where python >nul 2>nul
        if %ERRORLEVEL% equ 0 (
            set "PY_CMD=python"
        )
    )
)

if "%PY_CMD%"=="" (
    echo [!] Lỗi: Không tìm thấy Python hoặc môi trường ảo .venv!
    echo Vui lòng cài đặt Python hoặc chạy 'uv sync' trước.
    echo.
    pause
    exit /b 1
)

echo [*] Khởi động FBEM Bridge Daemon (HTTP API :47102 ^| WebSocket :9224)...
%PY_CMD% -m fbem.bridge

if %ERRORLEVEL% neq 0 (
    echo.
    echo [!] Server đã dừng với mã lỗi %ERRORLEVEL%.
    pause
)
