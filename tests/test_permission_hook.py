import io
import json
import socket
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from src import permission_hook
from src.permission_hook import STOP_REASON, build_hook_output

EVENT = {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "/w"}
Reply = Callable[[socket.socket, bytes], object]


class OneShotServer:
    """Real Unix-socket peer standing in for the bridge broker."""

    def __init__(self, path: Path, reply: Reply) -> None:
        self.received: list[dict[str, Any]] = []
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(path))
        self._server.listen(1)
        self._reply = reply
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        conn, _ = self._server.accept()
        with conn:
            data = conn.makefile("rb").readline()
            self.received.append(json.loads(data))
            self._reply(conn, data)

    def close(self) -> None:
        self._server.close()


@pytest.fixture
def socket_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="hook") as directory:
        yield Path(directory) / "g.sock"


def run_main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Any:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(EVENT)))
    assert permission_hook.main() == 0
    return json.loads(capsys.readouterr().out)


def decision(output: dict[str, Any]) -> str:
    return str(output["hookSpecificOutput"]["permissionDecision"])


def answer(payload: dict[str, Any]) -> Reply:
    def reply(conn: socket.socket, _data: bytes) -> None:
        conn.sendall(json.dumps(payload).encode() + b"\n")

    return reply


def test_build_hook_output_allow_carries_reason() -> None:
    output = build_hook_output({"decision": "allow", "reason": "ok"})
    assert output == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "ok",
        }
    }


@pytest.mark.parametrize("value", ["maybe", None, "", "ALLOW", "passthrough"])
def test_build_hook_output_unknown_decision_denies(value: object) -> None:
    assert decision(build_hook_output({"decision": value})) == "deny"


def test_build_hook_output_stop_ends_the_turn_only_on_deny() -> None:
    stopped = build_hook_output({"decision": "deny", "stop": True})
    assert (stopped["continue"], stopped["stopReason"]) == (False, STOP_REASON)
    assert "continue" not in build_hook_output({"decision": "allow", "stop": True})
    assert "continue" not in build_hook_output({"decision": "deny", "stop": False})


def test_main_forwards_the_tool_call_and_prints_the_verdict(
    socket_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server = OneShotServer(socket_path, answer({"decision": "allow", "reason": "ok"}))
    monkeypatch.setenv("BRIDGE_GATE_SOCKET", str(socket_path))
    monkeypatch.setenv("BRIDGE_PROJECT", "Root/alpha")
    output = run_main(monkeypatch, capsys)
    server.close()
    assert decision(output) == "allow"
    assert server.received == [
        {
            "project": "Root/alpha",
            "session_id": "s1",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "cwd": "/w",
        }
    ]


@pytest.mark.parametrize("missing", ["BRIDGE_GATE_SOCKET", "BRIDGE_PROJECT"])
def test_main_denies_when_bridge_env_is_missing(
    missing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BRIDGE_GATE_SOCKET", "/nonexistent.sock")
    monkeypatch.setenv("BRIDGE_PROJECT", "Root/alpha")
    monkeypatch.delenv(missing)
    assert decision(run_main(monkeypatch, capsys)) == "deny"


def test_main_denies_on_malformed_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    permission_hook.main()
    assert decision(json.loads(capsys.readouterr().out)) == "deny"


def test_main_denies_when_bridge_closes_without_answer(
    socket_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server = OneShotServer(socket_path, lambda _conn, _data: None)
    monkeypatch.setenv("BRIDGE_GATE_SOCKET", str(socket_path))
    monkeypatch.setenv("BRIDGE_PROJECT", "Root/alpha")
    output = run_main(monkeypatch, capsys)
    server.close()
    assert decision(output) == "deny"


def test_main_denies_when_bridge_does_not_answer_in_time(
    socket_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    release = threading.Event()
    server = OneShotServer(socket_path, lambda _conn, _data: release.wait())
    monkeypatch.setenv("BRIDGE_GATE_SOCKET", str(socket_path))
    monkeypatch.setenv("BRIDGE_PROJECT", "Root/alpha")
    monkeypatch.setenv("BRIDGE_GATE_TIMEOUT", "0.2")
    started = time.monotonic()
    output = run_main(monkeypatch, capsys)
    elapsed = time.monotonic() - started
    release.set()
    server.close()
    assert decision(output) == "deny"
    assert elapsed < 5
