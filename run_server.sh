#!/usr/bin/env zsh
set -euo pipefail
cd "$(dirname "$0")"

HOST="0.0.0.0"
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || echo '<YOUR_MAC_IP>')"
echo "AMICO Backend listening on LAN: http://${LAN_IP}:8000"

exec .venv/bin/uvicorn app.main:app --host "$HOST" --port 8000 --reload
