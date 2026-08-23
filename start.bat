@echo off
title FBEM Studio - Facebook Automation Bridge
chcp 65001 > nul
cls

echo =======================================================
echo          ⚡ FBEM STUDIO - FACEBOOK AUTOMATION
echo =======================================================
echo.
echo  [1/3] Đang khởi động các Profile Chrome chạy ngầm...
powershell -Command "if (Test-Path '$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe') { $c = '$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe' } elseif (Test-Path 'C:\Program Files\Google\Chrome\Application\chrome.exe') { $c = 'C:\Program Files\Google\Chrome\Application\chrome.exe' } else { $c = 'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe' }; if (Test-Path $c) { Start-Process $c -ArgumentList '--profile-directory=Default', '--no-first-run', 'https://www.facebook.com/' -WindowStyle Minimized }" > nul 2>&1

echo  [2/3] Đang mở giao diện Web Dashboard tại http://127.0.0.1:47102/
start "" "http://127.0.0.1:47102/"

echo  [3/3] Đang khởi động Server Bridge & Telegram Bot...
echo.
echo  =======================================================
echo  Mọi Profile Chrome & Bot Telegram đã sẵn sàng hoạt động!
echo  (Để dừng hệ thống, bạn chỉ cần đóng cửa sổ này).
echo  =======================================================
echo.

uv run python -m fbem.bridge

pause
