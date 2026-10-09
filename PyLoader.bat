@echo off
rem One-click start: prepares .venv when needed, then opens PyLoader as an app window.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [PyLoader] First run: creating virtual environment...
    where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
    if not exist ".venv\Scripts\python.exe" (
        echo [PyLoader] Python 3.10+ was not found. Install it from https://www.python.org/downloads/ and run again.
        pause
        exit /b 1
    )
)

rem Reinstall dependencies whenever requirements.txt changes.
fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul
if errorlevel 1 (
    echo [PyLoader] Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        pause
        exit /b 1
    )
    copy /y requirements.txt ".venv\requirements.installed" >nul
    if not exist "%USERPROFILE%\Desktop\PyLoader.lnk" ".venv\Scripts\python.exe" scripts\create_shortcut.py
)

start "" ".venv\Scripts\pythonw.exe" launcher.pyw
