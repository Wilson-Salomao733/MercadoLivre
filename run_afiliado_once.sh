#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/wilsonsalomo/Documentos/Afiliado"
LOG_DIR="$PROJECT_DIR/logs"
LOCK_FILE="/tmp/afiliado_bot.lock"
PYTHON="${PROJECT_DIR}/.venv/bin/python"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

if [[ ! -x "$PYTHON" ]]; then
  echo "$(date -Is) ERRO: $PYTHON não existe. Rode: cd $PROJECT_DIR && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >> "$LOG_DIR/cron.log"
  exit 1
fi

# Cron usa PATH mínimo — o venv garante schedule, moviepy, edge-tts, etc.
# flock -n: se outra rodada ainda estiver aberta, não empilha execução.
(
  flock -n 200 || { echo "$(date -Is) skip: outra instância ainda rodando" >> "$LOG_DIR/cron.log"; exit 0; }
  export CRON_MODE=1
  exec "$PYTHON" "$PROJECT_DIR/afiliado_bot.py"
) 200>"$LOCK_FILE" >>"$LOG_DIR/cron.log" 2>&1
