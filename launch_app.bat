@echo off
chcp 65001 >nul
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    call "%~dp0shipping_label_cropper\setup_env.bat"
    if errorlevel 1 (
        echo.
        pause
        exit /b 1
    )
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py" %*
