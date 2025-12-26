@echo off
setlocal

REM ========================================
REM SMS Dashboard - Web Application Launcher
REM ========================================

cd /d "%~dp0"

echo.
echo ========================================
echo    SMS Dashboard - Starting...
echo ========================================
echo.

REM Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed.
    echo Please install Python from https://www.python.org/
    pause
    exit /b 1
)

REM Check and install Flask
python -c "import flask" 2>nul
if %errorlevel% neq 0 (
    echo Installing Flask...
    pip install flask --quiet
)

REM Create logs directory
if not exist "logs" mkdir logs

echo.
echo Dashboard is starting...
echo.
echo Open your browser and go to:
echo.
echo     http://localhost:5000
echo.
echo Press Ctrl+C to stop the server.
echo.

REM Open browser after 2 seconds
start /b cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:5000"

REM Start Flask app
python app.py

pause
