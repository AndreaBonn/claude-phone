"""PreToolUse hook for Claude Code: forwards each tool call to the bridge gate.

Runs as a separate process spawned by `claude`, so it is stdlib-only and must
never import the bridge package. Every failure path denies the tool call:
the gate is fail-closed.
"""

import json
import os
import socket
import sys
from typing import Any

SOCKET_ENV = "BRIDGE_GATE_SOCKET"
PROJECT_ENV = "BRIDGE_PROJECT"
TIMEOUT_ENV = "BRIDGE_GATE_TIMEOUT"
DEFAULT_TIMEOUT_SECONDS = 360.0
READ_CHUNK = 65536
STOP_REASON = "Sessione fermata dall'utente da Telegram"


def build_hook_output(response: dict[str, Any]) -> dict[str, Any] | None:
    """Translate the bridge verdict into Claude Code's PreToolUse JSON output."""
    decision = response.get("decision")
    if decision == "passthrough":
        return None
    permission = "allow" if decision == "allow" else "deny"
    output: dict[str, Any] = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": permission,
            "permissionDecisionReason": str(response.get("reason") or ""),
        }
    }
    if permission == "deny" and response.get("stop"):
        output["continue"] = False
        output["stopReason"] = STOP_REASON
    return output


def ask_bridge(request: dict[str, Any], socket_path: str, timeout: float) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(socket_path)
        conn.sendall(json.dumps(request).encode() + b"\n")
        buffer = b""
        while not buffer.endswith(b"\n"):
            chunk = conn.recv(READ_CHUNK)
            if not chunk:
                break
            buffer += chunk
    return json.loads(buffer)


def main() -> int:
    try:
        event = json.load(sys.stdin)
        request = {
            "project": os.environ[PROJECT_ENV],
            "session_id": event.get("session_id"),
            "tool_name": event.get("tool_name"),
            "tool_input": event.get("tool_input") or {},
            "cwd": event.get("cwd"),
        }
        timeout = float(os.environ.get(TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS))
        response = ask_bridge(request, os.environ[SOCKET_ENV], timeout)
    except Exception as exc:  # fail-closed boundary: any error denies the tool
        response = {"decision": "deny", "reason": f"Gate del bridge non raggiungibile: {exc!r}"}
    output = build_hook_output(response)
    if output is not None:
        print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
