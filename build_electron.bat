@echo off
title Build FBEM Studio Electron Executable
chcp 65001 > nul
cls

echo =======================================================
echo     📦 ĐÓNG GÓI FBEM STUDIO THÀNH APP ELECTRON (.EXE)
echo =======================================================
echo.

echo [1/3] Đang build giao diện Web Frontend...
cd frontend
call npm run build
cd ..

echo.
echo [2/3] Đang kiểm tra thư viện Electron...
call npm install

echo.
echo [3/3] Đang đóng gói ứng dụng Electron bằng electron-builder...
call npm run dist

echo.
echo =======================================================
echo  🎉 ĐÓNG GÓI THÀNH CÔNG!
echo  Thư mục cài đặt / file EXE tại: dist-electron\
echo =======================================================
echo.
if exist "dist-electron" (
    explorer dist-electron
)
pause
