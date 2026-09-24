#!/usr/bin/env bash
# Manual start of the Telegram <-> Claude Code bridge. Never started automatically.
# Usage: ./start.sh              foreground, Ctrl+C to stop
#        ./start.sh --background detached, stop with ./stop.sh
set -euo pipefail
umask 077
cd "$(dirname "$(readlink -f "$0")")"

PID_FILE="data/bot.pid"
LOG_FILE="logs/bridge.log"
READY_BANNER="Bot attivo, in ascolto"
READY_TIMEOUT_SECONDS=60

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Il bot è già in esecuzione (PID $(cat "$PID_FILE")). Fermalo con ./stop.sh" >&2
    exit 1
fi
rm -f "$PID_FILE"

if [[ ! -f .env ]]; then
    echo "File .env mancante: copia .env.example in .env e compilalo." >&2
    exit 1
fi
command -v uv >/dev/null || { echo "uv non trovato nel PATH." >&2; exit 1; }

CLAUDE_BIN="$(grep -E '^CLAUDE_BIN=' .env | tail -n 1 | cut -d= -f2- || true)"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
# shellcheck disable=SC2086  # CLAUDE_BIN may carry arguments on purpose
if ! $CLAUDE_BIN auth status >/dev/null 2>&1; then
    echo "Claude Code non è autenticato ('$CLAUDE_BIN auth status' fallito). Esegui 'claude auth login'." >&2
    exit 1
fi

mkdir -p data logs
uv sync --frozen --quiet

if [[ "${1:-}" == "--background" ]]; then
    log_offset=$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)
    nohup uv run --frozen python -m src.bot >> logs/stdout.log 2>&1 &
    echo $! > "$PID_FILE"
    for _ in $(seq "$READY_TIMEOUT_SECONDS"); do
        if tail -c +"$((log_offset + 1))" "$LOG_FILE" 2>/dev/null | grep -q "$READY_BANNER"; then
            echo "$READY_BANNER (PID $(cat "$PID_FILE")). Log: tail -f $LOG_FILE"
            exit 0
        fi
        if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            rm -f "$PID_FILE"
            echo "Avvio fallito, ultime righe di log:" >&2
            tail -n 20 logs/stdout.log >&2
            exit 1
        fi
        sleep 1
    done
    echo "Il bot non ha confermato l'avvio entro ${READY_TIMEOUT_SECONDS}s: controlla $LOG_FILE" >&2
    exit 1
fi

echo $$ > "$PID_FILE"
echo "Avvio in foreground: attendi '$READY_BANNER', Ctrl+C per fermare."
# exec keeps this PID, so data/bot.pid points at the running bot.
exec uv run --frozen python -m src.bot
