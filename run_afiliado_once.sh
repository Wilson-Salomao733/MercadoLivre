#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/wilsonsalomo/Documentos/Afiliado"
LOG_DIR="$PROJECT_DIR/logs"
LOCK_FILE="/tmp/afiliado_bot.lock"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

# Evita duas execuções simultâneas caso uma rodada demore mais de 8h.
flock -n "$LOCK_FILE" bash -lc '
  export CRON_MODE=1
  python3 afiliado_bot.py >> "'"$LOG_DIR"'/cron.log" 2>&1
'
