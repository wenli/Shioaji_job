@echo off
setlocal
cd /d "%~dp0"

echo ======================================================================
echo   Futures Data Downloader ^& SMC/ORB Backtest Pro Launcher
echo ======================================================================
echo.

set "PY_CMD="

if exist ".venv\Scripts\python.exe" (
    set "PY_CMD=.venv\Scripts\python.exe"
    echo [INFO] Detected virtual environment: .venv
    goto :FOUND_PY
)

if exist "venv\Scripts\python.exe" (
    set "PY_CMD=venv\Scripts\python.exe"
    echo [INFO] Detected virtual environment: venv
    goto :FOUND_PY
)

where python >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    echo [INFO] Using system Python
    goto :FOUND_PY
)

echo [ERROR] Python not found! Please install Python or setup .venv.
echo.
pause
exit /b 1

:FOUND_PY
echo [INFO] Python interpreter: %PY_CMD%
%PY_CMD% --version
echo.

:: Check .env configuration file
if not exist ".env" (
    if exist ".env.example" (
        echo [WARN] .env not found. Copying from .env.example...
        copy ".env.example" ".env" >nul
        echo [WARN] Created .env file. Please configure your Shioaji API credentials.
    )
)

:: Check and cleanup Port 8000 if occupied by a previous process
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo [WARN] Port 8000 occupied by PID %%a, terminating lingering process...
    taskkill /F /PID %%a >nul 2>&1
)

echo.
echo ======================================================================
echo   Server URL  : http://127.0.0.1:8000/
echo   SMC Terminal: http://127.0.0.1:8000/live
echo   ORB Terminal: http://127.0.0.1:8000/orb_terminal
echo ======================================================================
echo.

:: Open default browser after 2 seconds in background
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:8000/"

:: Launch FastAPI / Uvicorn server
%PY_CMD% app/main.py

if %errorlevel% neq 0 (
    echo.
    echo ======================================================================
    echo   [ERROR] Server exited with error code %errorlevel%
    echo ======================================================================
) else (
    echo.
    echo [INFO] Server stopped normally.
)

echo.
pause
