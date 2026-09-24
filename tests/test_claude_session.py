import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from src.claude_session import (
    ClaudeCrashedError,
    ClaudeTimeoutError,
    SessionConfig,
    build_command,
)
from src.project_manager import ProjectManager, Sandbox
from src.session_manager import SessionManager
from src.session_store import SessionStore
from src.stream_parser import StreamEvent, ToolUseEvent

FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"
PROJECT = "sandbox/alpha"


class Harness:
    def __init__(self, tmp_path: Path, idle_timeout: float = 5.0) -> None:
        (tmp_path / "sandbox" / "alpha").mkdir(parents=True)
        self.record = tmp_path / "record.jsonl"
        self.store = SessionStore(tmp_path / "bridge.db")
        self.waiting = False
        self.events: list[StreamEvent] = []
        config = SessionConfig(
            claude_command=(sys.executable, str(FAKE_CLAUDE)),
            allowed_tools=("Read", "Bash"),
            gate_socket=tmp_path / "g.sock",
            approval_timeout=10,
            idle_timeout=idle_timeout,
            system_prompt="prompt",
        )
        self.manager = SessionManager(
            config=config,
            store=self.store,
            projects=ProjectManager(Sandbox(roots=((tmp_path / "sandbox").resolve(),))),
            is_waiting_for_user=lambda _project: self.waiting,
        )

    async def on_event(self, event: StreamEvent) -> None:
        self.events.append(event)

    def starts(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.record.read_text().splitlines()]


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    monkeypatch.setenv("FAKE_RECORD", str(tmp_path / "record.jsonl"))
    monkeypatch.setenv("FAKE_SCENARIO", "echo")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret")
    return Harness(tmp_path)


async def test_turn_streams_events_and_returns_result(harness: Harness) -> None:
    outcome = await harness.manager.run_turn(PROJECT, "hi", harness.on_event)
    await harness.manager.stop_all()
    assert outcome.result.text == "echo: hi"
    assert any(isinstance(event, ToolUseEvent) for event in harness.events)
    assert harness.store.get_session(PROJECT) == outcome.result.session_id


async def test_process_is_reused_across_turns(harness: Harness) -> None:
    await harness.manager.run_turn(PROJECT, "one", harness.on_event)
    outcome = await harness.manager.run_turn(PROJECT, "two", harness.on_event)
    await harness.manager.stop_all()
    assert outcome.result.text == "echo: two"
    assert len(harness.starts()) == 1


async def test_concurrent_messages_are_queued_in_order(harness: Harness) -> None:
    first, second = await asyncio.gather(
        harness.manager.run_turn(PROJECT, "one", harness.on_event),
        harness.manager.run_turn(PROJECT, "two", harness.on_event),
    )
    await harness.manager.stop_all()
    assert (first.result.text, second.result.text) == ("echo: one", "echo: two")
    assert len(harness.starts()) == 1


async def test_saved_session_is_resumed(harness: Harness) -> None:
    harness.store.save_session(PROJECT, "saved-id")
    outcome = await harness.manager.run_turn(PROJECT, "hi", harness.on_event)
    await harness.manager.stop_all()
    argv = harness.starts()[0]["argv"]
    assert argv[argv.index("--resume") + 1] == "saved-id"
    assert outcome.result.session_id == "saved-id"


async def test_missing_saved_session_falls_back_to_new_one(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "notfound")
    harness.store.save_session(PROJECT, "gone")
    outcome = await harness.manager.run_turn(PROJECT, "hi", harness.on_event)
    await harness.manager.stop_all()
    assert outcome.fresh_session is True
    assert outcome.result.text == "echo: hi"
    assert harness.store.get_session(PROJECT) not in (None, "gone")


async def test_crash_mid_turn_raises_with_stderr(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "crash")
    with pytest.raises(ClaudeCrashedError, match="boom"):
        await harness.manager.run_turn(PROJECT, "hi", harness.on_event)
    assert harness.manager.is_running(PROJECT) is False
    assert harness.store.get_session(PROJECT) is not None


async def test_idle_timeout_kills_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "hang")
    monkeypatch.setattr("src.claude_session.STOP_GRACE_SECONDS", 0.2)
    harness = Harness(tmp_path, idle_timeout=0.3)
    with pytest.raises(ClaudeTimeoutError):
        await harness.manager.run_turn(PROJECT, "hi", harness.on_event)
    assert harness.manager.is_running(PROJECT) is False


async def test_idle_timeout_ignores_time_waiting_for_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "hang")
    monkeypatch.setattr("src.claude_session.STOP_GRACE_SECONDS", 0.2)
    harness = Harness(tmp_path, idle_timeout=0.2)
    harness.waiting = True

    async def release_later() -> None:
        await asyncio.sleep(0.8)
        harness.waiting = False

    started = time.monotonic()
    releaser = asyncio.create_task(release_later())
    with pytest.raises(ClaudeTimeoutError):
        await harness.manager.run_turn(PROJECT, "hi", harness.on_event)
    elapsed = time.monotonic() - started
    await releaser
    assert elapsed >= 0.8


async def test_child_env_has_bridge_vars_and_no_bot_token(harness: Harness) -> None:
    await harness.manager.run_turn(PROJECT, "hi", harness.on_event)
    await harness.manager.stop_all()
    env = harness.starts()[0]["env"]
    assert "TELEGRAM_BOT_TOKEN" not in env
    assert env["BRIDGE_PROJECT"] == PROJECT
    assert env["BRIDGE_GATE_SOCKET"].endswith("g.sock")


async def test_request_stop_ends_process_after_turn(harness: Harness) -> None:
    harness.manager.get(PROJECT)
    harness.manager.request_stop(PROJECT)
    await harness.manager.run_turn(PROJECT, "hi", harness.on_event)
    assert harness.manager.is_running(PROJECT) is False


async def test_reset_forgets_session(harness: Harness) -> None:
    await harness.manager.run_turn(PROJECT, "hi", harness.on_event)
    await harness.manager.reset(PROJECT)
    assert harness.store.get_session(PROJECT) is None
    assert harness.manager.session_id(PROJECT) is None


def test_build_command_never_skips_permissions(tmp_path: Path) -> None:
    config = SessionConfig(("claude",), ("Read",), tmp_path / "s", 300, 300, "p")
    command = build_command(config, session_id="abc")
    assert not any("dangerously" in part for part in command)
    assert command[command.index("--permission-mode") + 1] == "default"
    assert "--strict-mcp-config" in command
    assert command[-2:] == ["--resume", "abc"]
    hooks = json.loads(command[command.index("--settings") + 1])["hooks"]["PreToolUse"]
    assert hooks[0]["matcher"] == "*"
    assert hooks[0]["hooks"][0]["timeout"] > 300


async def test_huge_stderr_line_does_not_block_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "noisy")
    monkeypatch.setattr("src.claude_session.STREAM_LIMIT", 64 * 1024)
    harness = Harness(tmp_path, idle_timeout=3.0)
    outcome = await harness.manager.run_turn(PROJECT, "hi", harness.on_event)
    await harness.manager.stop_all()
    assert outcome.result.text == "echo: hi"
