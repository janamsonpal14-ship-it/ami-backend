#!/usr/bin/env zsh
set -euo pipefail
cd "$(dirname "$0")"

HOST="127.0.0.1"
if [[ "${1:-}" == "--lan" ]]; then
  HOST="0.0.0.0"
  LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || echo '<YOUR_MAC_IP>')"
  echo "AMICO Backend listening on LAN: http://${LAN_IP}:8000"
else
  echo "AMICO Backend listening locally: http://127.0.0.1:8000"
fi

exec .venv/bin/uvicorn app.main:app --host "$HOST" --port 8000 --reload
