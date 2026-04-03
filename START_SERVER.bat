@echo off
title Remote GPU Render Server
color 0A
cls

echo ============================================================
echo   Remote GPU Render Server — b26
echo   Thunderbolt 4 / LAN
echo ============================================================
echo.

REM ── Python check ──────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo.
    echo Install Python 3.10+ from https://python.org
    echo Check "Add Python to PATH" during install, then re-run this.
    pause
    exit /b 1
)

REM ── Move to script folder ──────────────────────────────────────
cd /d "%~dp0"

REM ── Open firewall ports (needs admin — will prompt UAC) ────────
echo Opening firewall ports 9875-9880...
powershell -Command "Start-Process powershell -Verb RunAs -ArgumentList \"-NoProfile -Command \`\"netsh advfirewall firewall delete rule name='Remote GPU Render' >nul 2>&1; netsh advfirewall firewall add rule name='Remote GPU Render' dir=in action=allow protocol=TCP localport=9876-9880; netsh advfirewall firewall add rule name='Remote GPU Beacon' dir=in action=allow protocol=UDP localport=9875\`\"\" -Wait" >nul 2>&1
echo Firewall rules set.
echo.

REM ── Start server ──────────────────────────────────────────────
echo Server starting...
echo.
echo  Your Mac will find this machine automatically.
echo  In Blender: N-Panel ^> Remote GPU ^> Auto-discover
echo.
echo  Keep this window open while you work.
echo  Close it to stop the server.
echo.
echo ============================================================
echo.

python server.py

echo.
echo Server stopped.
pause
