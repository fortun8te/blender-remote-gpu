@echo off
echo ============================================
echo   Blender Remote GPU Server
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Install Python 3.10+ from python.org
    pause
    exit /b 1
)

REM Install websockets if needed
echo Installing dependencies...
pip install -q websockets
echo.

REM Start server
echo Starting server on port 9876...
echo Press Ctrl+C to stop.
echo.
python server/server.py --port 9876
pause
