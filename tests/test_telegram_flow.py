import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from telegram import Update

from src.bot import build_bridge
from src.bridge_context import BRIDGE_KEY, BridgeContext
from src.config import Settings
from src.handlers import callbacks
from src.handlers.projects import switch_project
from src.project_manager import SandboxError
from src.telegram_presenter import RESTART_NOTE
from src.turn_runner import TurnRequest, run_user_turn
from tests.fakes import FakeBot

FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"
USER = 42


@pytest.fixture
def bot() -> FakeBot:
    return FakeBot()


@pytest.fixture
def bridge(
    tmp_path: Path, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
) -> Iterator[BridgeContext]:
    monkeypatch.setenv("FAKE_SCENARIO", "echo")
    (tmp_path / "sandbox" / "alpha").mkdir(parents=True)
    (tmp_path / "sandbox" / "beta").mkdir()
    settings = Settings.model_validate(
        {
            "telegram_bot_token": "1:x",
            "allowed_users": str(USER),
            "approved_directory": str(tmp_path / "sandbox"),
            "claude_bin": f"{sys.executable} {FAKE_CLAUDE}",
            "db_path": str(tmp_path / "bridge.db"),
            "gate_socket_path": str(tmp_path / "g.sock"),
            "approval_timeout_seconds": 5,
        }
    )
    bridge = build_bridge(settings, bot)
    bridge.store.set_active_project(USER, "alpha")
    yield bridge
    bridge.store.close()


def turn(text: str) -> TurnRequest:
    return TurnRequest(chat_id=USER, user_id=USER, project="alpha", text=text)


async def test_turn_shows_progress_then_answer(bridge: BridgeContext, bot: FakeBot) -> None:
    await run_user_turn(bridge, bot, turn("hello"))
    await bridge.sessions.stop_all()
    progress, answer = bot.messages
    assert progress.text.startswith("✅ alpha: completato")
    assert "📖 Read: a.py" in progress.text
    assert answer.text == "echo: hello"
    assert bridge.store.recent_audit(limit=1)[0].detail == "hello"


async def test_turn_at_verbose_zero_deletes_progress(bridge: BridgeContext, bot: FakeBot) -> None:
    bridge.store.set_verbose(USER, 0)
    await run_user_turn(bridge, bot, turn("hello"))
    await bridge.sessions.stop_all()
    assert [m.text for m in bot.visible()] == ["echo: hello"]


async def test_turn_renders_choices_as_buttons(bridge: BridgeContext, bot: FakeBot) -> None:
    await run_user_turn(bridge, bot, turn("Pick one\n[[option: SQLite]]\n[[option: Postgres]]"))
    await bridge.sessions.stop_all()
    answer = bot.messages[-1]
    assert answer.text == "echo: Pick one"
    labels = [row[0].text for row in answer.reply_markup.inline_keyboard]
    assert labels == ["SQLite", "Postgres"]
    assert list(bridge.choices.values()) == [["SQLite", "Postgres"]]


async def test_crashed_turn_is_reported_not_left_working(
    bridge: BridgeContext, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "crash")
    await run_user_turn(bridge, bot, turn("hello"))
    progress, error = bot.messages
    assert progress.text.startswith("❌ alpha: interrotto")
    assert "boom" in error.text


async def test_switch_project_sets_active_and_rejects_traversal(bridge: BridgeContext) -> None:
    reply = await switch_project(bridge, USER, "beta")
    assert bridge.store.get_active_project(USER) == "beta"
    assert "Nuova sessione" in reply
    bridge.store.save_session("alpha", "abcdef123456")
    assert "abcdef12" in await switch_project(bridge, USER, "alpha")
    with pytest.raises(SandboxError):
        await switch_project(bridge, USER, "../..")


class FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.answers: list[str | None] = []
        self.message = SimpleNamespace(chat=SimpleNamespace(id=USER))

    async def answer(self, text: str | None = None) -> None:
        self.answers.append(text)

    async def edit_message_reply_markup(self, reply_markup: Any = None) -> None:
        return None


def callback(bridge: BridgeContext, bot: FakeBot, data: str) -> tuple[Update, Any, FakeQuery]:
    query = FakeQuery(data)
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=USER))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={BRIDGE_KEY: bridge}), bot=bot)
    return cast(Update, update), context, query


async def test_approve_button_unblocks_the_gate(bridge: BridgeContext, bot: FakeBot) -> None:
    cwd = str(bridge.settings.approved_directory / "alpha")
    payload = {"project": "alpha", "tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": cwd}
    gate = asyncio.create_task(bridge.broker.handle_request(payload))
    await asyncio.sleep(0.05)
    prompt = bot.messages[-1]
    approve = prompt.reply_markup.inline_keyboard[0][0].callback_data
    assert bridge.store.pop_pending_approvals() != []
    update, context, query = callback(bridge, bot, approve)
    await callbacks.handle_approval(update, context)
    assert (await asyncio.wait_for(gate, 5))["decision"] == "allow"
    assert prompt.text.endswith("<b>✅ Approvato</b>")
    assert query.answers == ["✅ Approvato"]


async def test_stale_approval_button_is_rejected(bridge: BridgeContext, bot: FakeBot) -> None:
    update, context, query = callback(bridge, bot, "ap:deadbeef:approve")
    await callbacks.handle_approval(update, context)
    assert query.answers == [callbacks.EXPIRED]


async def test_leftover_approvals_are_cancelled_on_restart(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    prompt = await bot.send_message(chat_id=USER, text="🔐 Bash")
    bridge.store.add_pending_approval("old1", "alpha", USER, prompt.message_id)
    assert await bridge.presenter.cancel_leftovers() == 1
    assert prompt.text == RESTART_NOTE
    assert "1 richieste" in bot.messages[-1].text
    assert await bridge.presenter.cancel_leftovers() == 0


def test_build_application_registers_guard_first(bridge: BridgeContext) -> None:
    from telegram.ext import TypeHandler

    from src.bot import build_application

    app = build_application(bridge.settings)
    assert isinstance(app.handlers[-1][0], TypeHandler)
    assert len(app.handlers[0]) == 10
    app.bot_data[BRIDGE_KEY].store.close()
