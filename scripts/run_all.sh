#!/bin/bash
cd "$(dirname "$0")"

echo "[INFO] Installing Python packages..."
pip install -r ../requirements.txt || {
  echo "[ERROR] Failed to install dependencies."
  exit 1
}

echo
echo "============================================"
echo "  Select which scraper(s) to run:"
echo "============================================"
echo "  1. Fox News"
echo "  2. MSNBC"
echo "  3. BOTH"
echo "============================================"
read -p "Enter choice [1-3]: " choice

TOR_PATH="../tor/tor/tor"
if [ ! -f "$TOR_PATH" ]; then
  echo "[ERROR] Tor not found at $TOR_PATH"
  exit 1
fi

echo "[INFO] Launching Tor in background..."
"$TOR_PATH" --ControlPort 8999 --SocksPolicy "accept 127.0.0.1" --SocksPort 9001 &
TOR_PID=$!
sleep 10

if [ "$choice" == "1" ]; then
  echo "[INFO] Running Fox News scraper..."
  python3 ../src/[1]\ fox_news_data_scraper.py
elif [ "$choice" == "2" ]; then
  echo "[INFO] Running MSNBC scraper..."
  python3 ../src/[2]\ msnbc_data_scraper.py
elif [ "$choice" == "3" ]; then
  echo "[INFO] Running both scrapers..."
  python3 ../src/[1]\ fox_news_data_scraper.py
  python3 ../src/[2]\ msnbc_data_scraper.py
else
  echo "[ERROR] Invalid choice."
fi

kill $TOR_PID
