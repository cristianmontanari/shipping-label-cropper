@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" if exist ".venv\Scripts\pythonw.exe" (
    ".venv\Scripts\python.exe" -c "import pymupdf, PIL" >nul 2>&1
    if not errorlevel 1 exit /b 0
)

set "PYTHON_EXE="
call :find_python

if not defined PYTHON_EXE (
    echo Python 3.10+ not found on this PC.
    where winget >nul 2>&1
    if not errorlevel 1 (
        echo Automatically installing Python 3.12 via winget...
        winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        call :find_python
    )
)

if not defined PYTHON_EXE (
    echo.
    echo [ERROR] Python 3.10+ not found on this PC.
    echo Download and install Python from: https://www.python.org/downloads/
    echo During installation, check "Add python.exe to PATH", then run this script again.
    exit /b 1
)

echo Found Python: %PYTHON_EXE%

if not exist ".venv\Scripts\python.exe" (
    if exist ".venv" rmdir /s /q ".venv" 2>nul
    echo Creating virtual environment...
    "%PYTHON_EXE%" -m venv .venv
    if errorlevel 1 (
        rmdir /s /q ".venv" 2>nul
        echo.
        echo [ERROR] Failed to create virtual environment .venv.
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [ERROR] Failed to create .venv: .venv\Scripts\python.exe not found.
    exit /b 1
)

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Dependency installation failed. Check your internet connection and try again.
    exit /b 1
)

".venv\Scripts\python.exe" -c "import pymupdf, PIL" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Dependencies were not installed properly in the virtual environment.
    exit /b 1
)

exit /b 0

:find_python
set "PYTHON_EXE="

rem 1. Windows Python Launcher (py -3)
py -3 -c "import sys, venv, tkinter; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    for /f "usebackq delims=" %%P in (`py -3 -c "import sys; print(sys.executable)" 2^>nul`) do (
        if not defined PYTHON_EXE call :try_exe "%%P"
    )
)
if defined PYTHON_EXE exit /b 0

rem 2. All python / python3 occurrences in PATH (skip the Microsoft Store stub)
for /f "usebackq delims=" %%P in (`where python 2^>nul`) do (
    if not defined PYTHON_EXE call :try_exe "%%P"
)
if defined PYTHON_EXE exit /b 0

for /f "usebackq delims=" %%P in (`where python3 2^>nul`) do (
    if not defined PYTHON_EXE call :try_exe "%%P"
)
if defined PYTHON_EXE exit /b 0

rem 3. Standard Windows installation paths (even if not in PATH or just installed)
call :try_exe "%LOCALAPPDATA%\Python\bin\python.exe"
if defined PYTHON_EXE exit /b 0

for /d %%D in ("%LOCALAPPDATA%\Python\pythoncore-3.*") do (
    if not defined PYTHON_EXE call :try_exe "%%~D\python.exe"
)
if defined PYTHON_EXE exit /b 0

for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if not defined PYTHON_EXE call :try_exe "%%~D\python.exe"
)
if defined PYTHON_EXE exit /b 0

call :scan_program_files "%ProgramFiles%"
if defined PYTHON_EXE exit /b 0

call :scan_program_files "%ProgramFiles(x86)%"
exit /b 0

:scan_program_files
if "%~1"=="" exit /b 0
for /d %%D in ("%~1\Python3*") do (
    if not defined PYTHON_EXE call :try_exe "%%~D\python.exe"
)
exit /b 0

:try_exe
if "%~1"=="" exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys, venv, tkinter; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
set "PYTHON_EXE=%~1"
exit /b 0
