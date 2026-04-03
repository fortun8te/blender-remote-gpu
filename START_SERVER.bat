@echo off
title Remote GPU Render Server
color 0A
cls

echo ============================================================
echo   Remote GPU Render Server
echo   Plug in Thunderbolt 4 cable, then run this on Windows.
echo ============================================================
echo.

REM Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Install Python 3.10+ from https://python.org
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

REM Move to the script's folder regardless of where it's launched from
cd /d "%~dp0"

echo Starting render server...
echo.
echo Your Mac will auto-discover this machine.
echo In Blender: N-Panel ^> Remote GPU ^> Auto-discover
echo.
echo Keep this window open while rendering.
echo Close it to stop the server.
echo.
echo ============================================================
echo.

python server.py

echo.
echo Server stopped.
pause
