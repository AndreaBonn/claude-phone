#!/usr/bin/env bash
# Render the user unit for this checkout and install it, leaving the tracked
# template untouched. The unit still has no [Install] section: it never starts
# by itself.
# Usage: systemd/install-unit.sh           install and reload the user manager
#        systemd/install-unit.sh --print   print the rendered unit, change nothing
set -euo pipefail

BRIDGE_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
TEMPLATE="$BRIDGE_DIR/systemd/telegram-claude-bridge.service"
UNIT_NAME="telegram-claude-bridge.service"
PLACEHOLDER="/path/to/telegram-claude-bridge"
PATH_KEY="Environment=PATH="
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [[ "$BRIDGE_DIR" =~ [[:space:]] ]]; then
    echo "Il percorso del bridge contiene spazi, non supportati dalla unit: $BRIDGE_DIR" >&2
    exit 1
fi

# systemd user services get a minimal PATH: put the directories of uv and
# claude (not their symlink targets, so node from the same bin dir is found) first.
tool_dirs=""
for tool in uv claude; do
    if ! tool_path="$(command -v "$tool")"; then
        echo "$tool non trovato nel PATH: installalo o aggiungilo prima di generare la unit." >&2
        exit 1
    fi
    tool_dirs+="$(dirname "$tool_path"):"
done

unit="$(<"$TEMPLATE")"
unit="${unit//"$PLACEHOLDER"/"$BRIDGE_DIR"}"
unit="${unit//"$PATH_KEY"/"$PATH_KEY$tool_dirs"}"

if [[ "${1:-}" == "--print" ]]; then
    printf '%s\n' "$unit"
    exit 0
fi

mkdir -p "$UNIT_DIR"
printf '%s\n' "$unit" > "$UNIT_DIR/$UNIT_NAME"
systemctl --user daemon-reload
echo "Unit installata in $UNIT_DIR/$UNIT_NAME"
echo "Avvio: systemctl --user start telegram-claude-bridge"
echo "Stop:  systemctl --user stop telegram-claude-bridge"
