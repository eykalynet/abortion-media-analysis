@echo off
title Launch Tor + Run Fox News Scraper

set "TOR_PATH=tor\tor\tor.exe"

if not exist "%TOR_PATH%" (
    echo [ERROR] Tor binary not found in /tor directory.
    echo Please ensure you cloned the full repo with the /tor folder.
    pause
    exit /b
)

echo [INFO] Starting Tor on ControlPort 8999 and SocksPort 9001...
start "" "%TOR_PATH%" --ControlPort 8999 --SocksPort 9001

echo [INFO] Waiting 10 seconds for Tor to fully start...
timeout /t 10 > nul

echo [INFO] Launching scraper...
python "[1] fox_news_data_scraper.py"

pause
