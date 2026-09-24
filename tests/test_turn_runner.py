import asyncio
from pathlib import Path

import pytest

from src.bridge_context import BridgeContext
from src.turn_runner import (
    EMPTY_ANSWER,
    FRESH_SESSION_NOTICE,
    QUEUED_NOTICE,
    TurnRequest,
    run_user_turn,
)
from tests.conftest import ALPHA, BETA, USER, make_bridge
from tests.fakes import FakeBot, wait_until


def turn(text: str, project: str = ALPHA) -> TurnRequest:
    return TurnRequest(chat_id=USER, user_id=USER, project=project, text=text)


async def test_idle_timeout_is_reported_with_resume_hint(
    tmp_path: Path, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "hang")
    monkeypatch.setattr("src.claude_session.STOP_GRACE_SECONDS", 0.2)
    bridge = make_bridge(tmp_path, bot, claude_timeout_seconds=1)
    await run_user_turn(bridge, bot, turn("hi"))
    bridge.store.close()
    progress, error = bot.messages
    assert progress.text.startswith(f"❌ {ALPHA}: interrotto")
    assert error.text.startswith("⏱️ Nessun output da Claude per 1s")
    assert "riprende la sessione" in error.text


async def test_deleted_project_is_reported_as_unusable(bridge: BridgeContext, bot: FakeBot) -> None:
    (bridge.settings.approved_directory[0] / "beta").rmdir()
    await run_user_turn(bridge, bot, turn("hi", project=BETA))
    assert bot.messages[-1].text.startswith(f"🚫 Progetto {BETA} non utilizzabile")
    assert bridge.store.recent_audit(limit=1)[0].event == "turn-error"


async def test_turn_without_text_explains_the_empty_answer(
    bridge: BridgeContext, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "silent")
    await run_user_turn(bridge, bot, turn("hi"))
    await bridge.sessions.stop_all()
    assert bot.messages[-1].shown == EMPTY_ANSWER


async def test_lost_session_is_announced_in_the_answer(
    bridge: BridgeContext, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "notfound")
    bridge.store.save_session(f"default::{ALPHA}", "gone")
    await run_user_turn(bridge, bot, turn("hi"))
    await bridge.sessions.stop_all()
    assert bot.messages[-1].text == FRESH_SESSION_NOTICE + "echo: hi"


async def test_message_sent_while_claude_works_is_queued_and_answered(
    bridge: BridgeContext, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "slow")
    first = asyncio.create_task(run_user_turn(bridge, bot, turn("one")))
    await wait_until(lambda: bridge.sessions.is_busy(ALPHA), "first turn running")
    await run_user_turn(bridge, bot, turn("two"))
    await first
    await bridge.sessions.stop_all()
    texts = [message.text for message in bot.messages]
    assert QUEUED_NOTICE in texts
    assert texts.index("echo: one") < texts.index("echo: two")
