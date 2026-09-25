import asyncio
from pathlib import Path

import pytest

from src.bridge_context import BridgeContext
from src.handlers.projects import project_token
from src.session_manager import TurnOutcome
from src.stream_parser import ResultEvent
from src.turn_runner import (
    ATTACHMENTS_ONLY,
    EMPTY_ANSWER,
    FRESH_SESSION_NOTICE,
    INTERRUPTED_NOTICE,
    NOTHING_TO_STOP,
    QUEUED_NOTICE,
    STOP_PREFIX,
    STOPPED_REPLY,
    TurnRequest,
    compose_answer,
    run_user_turn,
    stop_turn,
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


async def test_stop_button_interrupts_the_running_turn(
    bridge: BridgeContext, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "hang")
    running = asyncio.create_task(run_user_turn(bridge, bot, turn("hi")))
    await wait_until(lambda: bridge.sessions.is_busy(ALPHA) and bool(bot.messages), "turn started")
    stop = bot.messages[0].reply_markup.inline_keyboard[0][0]
    assert stop.text == "⏹️ Stop"
    assert stop.callback_data == f"{STOP_PREFIX}:{project_token(ALPHA)}"
    assert await stop_turn(bridge, ALPHA) == STOPPED_REPLY.format(project=ALPHA)
    await asyncio.wait_for(running, 5)
    progress, notice = bot.messages
    assert progress.text.startswith(f"⏹️ {ALPHA}: fermato")
    assert progress.reply_markup is None
    assert notice.text == INTERRUPTED_NOTICE
    assert bridge.store.recent_audit(limit=1)[0].event == "turn-stopped"


async def test_stop_turn_cancels_the_pending_approval(
    bridge: BridgeContext, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "hang")
    running = asyncio.create_task(run_user_turn(bridge, bot, turn("hi")))
    await wait_until(lambda: bridge.sessions.is_busy(ALPHA), "turn started")
    cwd = str(bridge.settings.approved_directory[0] / "alpha")
    payload = {"project": ALPHA, "tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": cwd}
    gate = asyncio.create_task(bridge.broker.handle_request(payload))
    await wait_until(lambda: bool(bridge.broker.pending(ALPHA)), "approval pending")
    await stop_turn(bridge, ALPHA)
    assert (await asyncio.wait_for(gate, 5))["decision"] == "deny"
    await asyncio.wait_for(running, 5)


async def test_stop_turn_with_nothing_running_says_so(bridge: BridgeContext) -> None:
    assert await stop_turn(bridge, ALPHA) == NOTHING_TO_STOP.format(project=ALPHA)


async def test_files_named_in_the_answer_arrive_as_attachments(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    project_dir = bridge.settings.approved_directory[0] / "alpha"
    (project_dir / "report.md").write_text("# Report")
    await run_user_turn(bridge, bot, turn("Here it is\n[[file: report.md]]\n[[file: gone.txt]]"))
    await bridge.sessions.stop_all()
    assert bot.documents == [(USER, b"# Report", "report.md")]
    answer, problems = bot.messages[-2:]
    assert answer.text == "echo: Here it is"
    assert problems.text == "⚠️ gone.txt: file non trovato"
    assert bridge.store.recent_audit(limit=1)[0].event == "file-sent"


def test_answer_with_only_attachments_is_not_reported_as_empty() -> None:
    result = ResultEvent(session_id="s", text="[[file: a.txt]]", is_error=False, subtype="success")
    answer = compose_answer(TurnOutcome(result=result))
    assert answer.text == ATTACHMENTS_ONLY
    assert answer.files == ["a.txt"]


async def test_a_written_deliverable_is_attached_without_a_file_line(
    bridge: BridgeContext, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "write")
    await run_user_turn(bridge, bot, turn("Genera un markdown con scritto chi sei"))
    await bridge.sessions.stop_all()
    assert bot.documents == [(USER, b"# Chi sono", "CHI_SONO.md")]
    assert bot.messages[-1].text == "Ho creato CHI_SONO.md."
