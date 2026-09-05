#!/usr/bin/env bash
# Start Inventory Hub on the local network so ESP32 devices can reach it.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

PORT="${PORT:-8000}"
echo "Inventory Hub starting on http://0.0.0.0:${PORT}"
echo "Open the dashboard at http://localhost:${PORT}"
echo "Point ESP32 devices at this computer's LAN IP, port ${PORT}."
exec ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
