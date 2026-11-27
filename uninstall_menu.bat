@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py --uninstall-menu
) else (
    reg delete "HKCU\Software\Classes\SystemFileAssociations\.pdf\shell\ShippingLabelCropper" /f >nul 2>&1
    reg delete "HKCU\Software\Classes\SystemFileAssociations\.pdf\shell\CropPDF10x15" /f >nul 2>&1
    if exist "%APPDATA%\Microsoft\Windows\SendTo\Crop Shipping Label.lnk" (
        del /f /q "%APPDATA%\Microsoft\Windows\SendTo\Crop Shipping Label.lnk" >nul 2>&1
    )
    if exist "%APPDATA%\Microsoft\Windows\SendTo\Crop Label 10x15.lnk" (
        del /f /q "%APPDATA%\Microsoft\Windows\SendTo\Crop Label 10x15.lnk" >nul 2>&1
    )
    if exist "%APPDATA%\Microsoft\Windows\SendTo\Ritaglia Etichetta 10x15.lnk" (
        del /f /q "%APPDATA%\Microsoft\Windows\SendTo\Ritaglia Etichetta 10x15.lnk" >nul 2>&1
    )
    echo Context menu removed successfully.
)
echo.
pause
