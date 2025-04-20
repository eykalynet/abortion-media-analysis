@echo off
title MSNBC + Fox News Scraper Launcher (via Tor with Auto-Install)

REM ============================================================================
REM INSTALL DEPENDENCIES
REM ============================================================================
echo [INFO] Installing Python packages...
pip install -r ../requirements.txt

if errorlevel 1 (
    echo [ERROR] Failed to install packages. Check requirements.txt.
    pause
    exit /b
)

REM ============================================================================
REM SCRAPER MENU
REM ============================================================================
echo.
echo ============================================
echo   Select which scraper(s) to run:
echo ============================================
echo   1. Fox News
echo   2. MSNBC
echo   3. BOTH
echo ============================================
set /p choice="Enter choice [1-3]: "

REM ============================================================================
REM START TOR (Relative Path from scripts/)
REM ============================================================================
set "TOR_PATH=../tor/tor/tor.exe"

if not exist "%TOR_PATH%" (
    echo [ERROR] Tor not found at: %TOR_PATH%
    pause
    exit /b
)

echo [INFO] Launching Tor in background...
start "" /B "%TOR_PATH%" --ControlPort 8999 --SocksPolicy "accept 127.0.0.1" --SocksPort 9001

echo [INFO] Waiting 10 seconds for Tor to initialize...
timeout /T 10 /NOBREAK > NUL

REM ============================================================================
REM RUN SELECTED SCRAPER(S)
REM ============================================================================
if "%choice%"=="1" (
    echo [INFO] Running Fox News scraper...
    python ../src/fox_news_data_scraper.py
) else if "%choice%"=="2" (
    echo [INFO] Running MSNBC scraper...
    python ../src/msnbc_data_scraper.py
) else if "%choice%"=="3" (
    echo [INFO] Running Fox News scraper...
    python ../src/fox_news_data_scraper.py
    echo [INFO] Running MSNBC scraper...
    python ../src/msnbc_data_scraper.py
) else (
    echo [ERROR] Invalid choice. Please enter 1, 2, or 3.
)

pause
