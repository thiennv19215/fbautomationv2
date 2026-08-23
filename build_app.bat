@echo off
title Build FBEM Studio Windows Executable
chcp 65001 > nul
cls

echo =======================================================
echo     📦 ĐÓNG GÓI FBEM STUDIO THÀNH APP WINDOWS (.EXE)
echo =======================================================
echo.

echo [1/3] Đang build giao diện Web Frontend...
cd frontend
call npm run build
cd ..

echo.
echo [2/3] Đang kiểm tra thư viện PyInstaller & PyWebView...
uv pip install pyinstaller pywebview

echo.
echo [3/3] Đang đóng gói file thực thi Windows bằng PyInstaller...
uv run pyinstaller --noconfirm --onedir --name "FBEM_Studio" --collect-all webview --add-data "fbem/bridge/static;fbem/bridge/static" --add-data "extension;extension" fbem_app.py

echo.
echo =======================================================
echo  🎉 ĐÓNG GÓI THÀNH CÔNG!
echo  Thư mục ứng dụng đã được tạo tại: dist\FBEM_Studio\
echo  File chạy: dist\FBEM_Studio\FBEM_Studio.exe
echo =======================================================
echo.
echo Đang mở thư mục chứa file app cho bạn...
explorer dist\FBEM_Studio
pause
