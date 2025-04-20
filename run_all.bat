@echo off
title MSNBC + Fox News Scraper Launcher (via Tor)

echo.
echo ============================================
echo   Select which scraper(s) to run:
echo ============================================
echo   1. Fox News
echo   2. MSNBC
echo   3. BOTH
echo ============================================
set /p choice="Enter choice [1-3]: "

REM Launch Tor if not already running
set "TOR_PATH=tor\\tor.exe"

if not exist "%TOR_PATH%" (
    echo [ERROR] Tor not found at expected path: %TOR_PATH%
    pause
    exit /b
)

echo [INFO] Launching Tor...
start "" /B "%TOR_PATH%" --ControlPort 8999 --SocksPort 9001
timeout /T 10 /NOBREAK > NUL

if "%choice%"=="1" (
    python "[1] fox_news_data_scraper.py"
) else if "%choice%"=="2" (
    python "[2] msnbc_data_scraper.py"
) else if "%choice%"=="3" (
    python "[1] fox_news_data_scraper.py"
    python "[2] msnbc_data_scraper.py"
) else (
    echo [ERROR] Invalid choice. Please enter 1, 2, or 3.
)

pause
