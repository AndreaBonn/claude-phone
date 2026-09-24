#!/usr/bin/env bash
# Clean stop: SIGTERM lets the bot close Claude sessions and say goodbye on Telegram.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PID_FILE="data/bot.pid"
STOP_TIMEOUT_SECONDS=40

if [[ ! -f "$PID_FILE" ]]; then
    echo "Nessun bot in esecuzione (data/bot.pid assente)."
    exit 0
fi
pid="$(cat "$PID_FILE")"
if ! kill -0 "$pid" 2>/dev/null; then
    echo "PID $pid non più attivo: rimuovo il PID file."
    rm -f "$PID_FILE"
    exit 0
fi

kill -TERM "$pid"
for _ in $(seq $((STOP_TIMEOUT_SECONDS * 2))); do
    if ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$PID_FILE"
        echo "Bot fermato."
        exit 0
    fi
    sleep 0.5
done

echo "Il bot non si è fermato entro ${STOP_TIMEOUT_SECONDS}s: invio SIGKILL." >&2
# Claude children exit on their own: their stdin pipe closes with the bot.
kill -KILL "$pid" 2>/dev/null || true
rm -f "$PID_FILE"
exit 1
