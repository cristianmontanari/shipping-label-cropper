@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0shipping_label_cropper\setup_env.bat"
if errorlevel 1 (
    echo.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py --install-menu
if errorlevel 1 (
    echo.
    echo [ERROR] Context menu installation failed.
    echo.
    pause
    exit /b 1
)
echo.
pause
