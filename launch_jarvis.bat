@echo off
title Jarvis AI Assistant
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo Python was not found on PATH.
    echo Install Python 3.11+ from https://www.python.org/downloads/
    echo Make sure "Add python.exe to PATH" is checked.
    pause
    exit /b 1
)

REM Ensure GUI dependency is present (common first-run failure)
python -c "import PyQt6" >nul 2>&1
if errorlevel 1 (
    echo Installing missing Python packages for Jarvis...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo Starting Jarvis...
echo If browser tools fail, run: python -m playwright install chromium
python main.py
if errorlevel 1 (
    echo.
    echo Jarvis exited with an error.
    pause
)
