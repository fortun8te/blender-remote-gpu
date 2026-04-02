@echo off
REM Windows batch file to start the render server on Tailscale
REM Double-click this file to start the server

echo.
echo ====================================
echo Remote GPU Render Server Startup
echo Tailscale Mode (VPN)
echo ====================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.10+ from python.org
    pause
    exit /b 1
)

REM Check if we're in the right directory
if not exist "server\server.py" (
    echo ERROR: server\server.py not found.
    echo Please run this from the blender-remote-gpu directory
    pause
    exit /b 1
)

REM Check if Tailscale is installed
where tailscale >nul 2>&1
if errorlevel 1 (
    echo WARNING: Tailscale not found. Install from https://tailscale.com/download
    echo.
)

REM Generate TLS certificate if not present
if not exist "C:\tmp\cert.pem" (
    echo Setting up TLS certificate...
    call setup_tls.bat
    if errorlevel 1 (
        pause
        exit /b 1
    )
)

REM Install dependencies if needed
echo Checking dependencies...
python -m pip install -q websockets msgpack pillow numpy

REM Start the server
echo.
echo Starting server on port 9876 (Tailscale) with TLS...
echo Press Ctrl+C to stop
echo.
echo Tailscale IP: Check 'tailscale ip' or 'tailscale status'
echo.

python server/server.py --port 9876 --use-tls

pause
