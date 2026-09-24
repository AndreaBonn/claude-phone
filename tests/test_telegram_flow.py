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
ALPHA = "sandbox/alpha"
BETA = "sandbox/beta"


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
    (tmp_path / "profiles" / "sales").mkdir(parents=True)
    settings = Settings.model_validate(
        {
            "telegram_bot_token": "1:x",
            "allowed_users": str(USER),
            "approved_directory": str(tmp_path / "sandbox"),
            "claude_bin": f"{sys.executable} {FAKE_CLAUDE}",
            "db_path": str(tmp_path / "bridge.db"),
            "gate_socket_path": str(tmp_path / "g.sock"),
            "approval_timeout_seconds": 5,
            "claude_profiles_dir": str(tmp_path / "profiles"),
        }
    )
    bridge = build_bridge(settings, bot)
    bridge.store.set_active_project(USER, ALPHA)
    yield bridge
    bridge.store.close()


def turn(text: str) -> TurnRequest:
    return TurnRequest(chat_id=USER, user_id=USER, project=ALPHA, text=text)


async def test_turn_shows_progress_then_answer(bridge: BridgeContext, bot: FakeBot) -> None:
    await run_user_turn(bridge, bot, turn("hello"))
    await bridge.sessions.stop_all()
    progress, answer = bot.messages
    assert progress.text.startswith(f"✅ {ALPHA}: completato")
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
    assert [c.labels for c in bridge.choices.values()] == [["SQLite", "Postgres"]]


async def test_crashed_turn_is_reported_not_left_working(
    bridge: BridgeContext, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "crash")
    await run_user_turn(bridge, bot, turn("hello"))
    progress, error = bot.messages
    assert progress.text.startswith(f"❌ {ALPHA}: interrotto")
    assert "boom" in error.text


async def test_switch_project_sets_active_and_rejects_traversal(bridge: BridgeContext) -> None:
    reply = await switch_project(bridge, USER, BETA)
    assert bridge.store.get_active_project(USER) == BETA
    assert "Nuova sessione" in reply
    bridge.store.save_session(f"default::{ALPHA}", "abcdef123456")
    assert "abcdef12" in await switch_project(bridge, USER, ALPHA)
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
    cwd = str(bridge.settings.approved_directory[0] / "alpha")
    payload = {"project": ALPHA, "tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": cwd}
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
    bridge.store.add_pending_approval("old1", ALPHA, USER, prompt.message_id)
    assert await bridge.presenter.cancel_leftovers() == 1
    assert prompt.text == RESTART_NOTE
    assert "1 richieste" in bot.messages[-1].text
    assert await bridge.presenter.cancel_leftovers() == 0


def test_build_application_registers_guard_first(bridge: BridgeContext) -> None:
    from telegram.ext import TypeHandler

    from src.bot import build_application

    app = build_application(bridge.settings)
    assert isinstance(app.handlers[-1][0], TypeHandler)
    assert len(app.handlers[0]) == 13
    app.bot_data[BRIDGE_KEY].store.close()


class BrokenBot(FakeBot):
    async def send_message(self, *args: Any, **kwargs: Any) -> Any:
        from telegram.error import Forbidden

        raise Forbidden("bot was blocked by the user")


async def test_turn_survives_telegram_failure_on_progress(bridge: BridgeContext) -> None:
    await run_user_turn(bridge, BrokenBot(), turn("hello"))
    assert bridge.store.recent_audit(limit=1)[0].event == "turn-error"


async def test_choice_button_goes_to_its_own_project(bridge: BridgeContext, bot: FakeBot) -> None:
    await run_user_turn(bridge, bot, turn("Pick\n[[option: Keep A]]"))
    button = bot.messages[-1].reply_markup.inline_keyboard[0][0].callback_data
    await switch_project(bridge, USER, BETA)
    update, context, _ = callback(bridge, bot, button)
    await callbacks.handle_choice(update, context)
    await bridge.sessions.stop_all()
    assert bridge.store.recent_audit(limit=1)[0].project == ALPHA
    assert bot.messages[-1].text == "echo: Keep A"


async def test_turn_on_other_project_keeps_existing_choices(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    await run_user_turn(bridge, bot, turn("Pick\n[[option: Keep A]]"))
    other = TurnRequest(chat_id=USER, user_id=USER, project=BETA, text="hi")
    await run_user_turn(bridge, bot, other)
    await bridge.sessions.stop_all()
    assert len(bridge.choices) == 1


async def test_malformed_callback_data_is_answered(bridge: BridgeContext, bot: FakeBot) -> None:
    for data, handler in [
        ("ap:broken", callbacks.handle_approval),
        ("ch:x", callbacks.handle_choice),
    ]:
        update, context, query = callback(bridge, bot, data)
        await handler(update, context)
        assert query.answers, data


async def test_project_button_switches_via_short_token(bridge: BridgeContext, bot: FakeBot) -> None:
    from src.handlers.projects import project_keyboard

    keyboard = project_keyboard(bridge.projects.list_projects(), active=ALPHA)
    assert keyboard is not None
    data = keyboard.inline_keyboard[1][0].callback_data
    assert isinstance(data, str) and len(data.encode()) <= 64

    class PickQuery(FakeQuery):
        edited: str = ""

        async def edit_message_text(self, text: str) -> None:
            self.edited = text

    query = PickQuery(data)
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=USER))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={BRIDGE_KEY: bridge}), bot=bot)
    await callbacks.handle_project_pick(cast(Update, update), cast(Any, context))
    assert bridge.store.get_active_project(USER) == BETA
    assert BETA in query.edited


def test_project_keyboard_paginates_under_telegram_button_limit() -> None:
    from src.handlers.projects import PAGE_PREFIX, PAGE_SIZE, project_keyboard

    projects = [f"Root/p{i:03d}" for i in range(113)]
    first = project_keyboard(projects, active=None)
    last = project_keyboard(projects, active=None, page=99)
    assert first is not None and last is not None
    buttons = [b for row in first.inline_keyboard for b in row]
    assert len(buttons) <= PAGE_SIZE + 2
    assert buttons[0].text == "Root/p000"
    assert first.inline_keyboard[-1][-1].callback_data == f"{PAGE_PREFIX}:1"
    last_buttons = [b.text for row in last.inline_keyboard for b in row]
    assert "Root/p112" in last_buttons
    assert all(b.callback_data != f"{PAGE_PREFIX}:6" for row in last.inline_keyboard for b in row)


async def test_page_button_edits_the_project_list(bridge: BridgeContext, bot: FakeBot) -> None:
    class PageQuery(FakeQuery):
        markup: Any = None

        async def edit_message_text(self, text: str, reply_markup: Any = None) -> None:
            self.markup = reply_markup

    query = PageQuery("pg:0")
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=USER))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={BRIDGE_KEY: bridge}), bot=bot)
    await callbacks.handle_project_page(cast(Update, update), cast(Any, context))
    assert [row[0].text for row in query.markup.inline_keyboard] == [f"▶️ {ALPHA}", BETA]


async def test_error_result_is_reported_once_without_subtype_noise(
    bridge: BridgeContext, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "autherror")
    await run_user_turn(bridge, bot, turn("chi sei?"))
    await bridge.sessions.stop_all()
    progress, answer = bot.messages
    assert "(success)" not in answer.text
    assert answer.text.startswith("❌ Claude ha segnalato un errore")
    assert "401" in answer.text
    assert "401" not in progress.text


async def test_final_text_is_not_repeated_in_progress(bridge: BridgeContext, bot: FakeBot) -> None:
    monkeypatch_text = "echo: hello"
    await run_user_turn(bridge, bot, turn("hello"))
    await bridge.sessions.stop_all()
    assert monkeypatch_text not in bot.messages[0].text
    assert "💬 working" in bot.messages[0].text


async def test_profile_button_switches_claude_profile(bridge: BridgeContext, bot: FakeBot) -> None:
    from src.handlers.profile import profile_keyboard

    keyboard = profile_keyboard(bridge.profiles.list_profiles(), active="default")
    assert [row[0].text for row in keyboard.inline_keyboard] == ["▶️ default", "sales"]

    class PickQuery(FakeQuery):
        edited: str = ""

        async def edit_message_text(self, text: str) -> None:
            self.edited = text

    query = PickQuery(str(keyboard.inline_keyboard[1][0].callback_data))
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=USER))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={BRIDGE_KEY: bridge}), bot=bot)
    await callbacks.handle_profile_pick(cast(Update, update), cast(Any, context))
    assert bridge.sessions.profile == "sales"
    assert bridge.store.get_profile(USER) == "sales"
    assert "sales" in query.edited


async def test_initial_profile_prefers_last_choice_then_settings(bridge: BridgeContext) -> None:
    from src.handlers.profile import apply_initial_profile

    await apply_initial_profile(bridge)
    assert bridge.sessions.profile == "default"
    bridge.store.set_profile(USER, "sales")
    await apply_initial_profile(bridge)
    assert bridge.sessions.profile == "sales"
    bridge.store.set_profile(USER, "deleted-profile")
    await apply_initial_profile(bridge)
    assert bridge.sessions.profile == "default"


async def test_auth_error_tells_how_to_log_in_again(
    bridge: BridgeContext, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "autherror")
    await run_user_turn(bridge, bot, turn("chi sei?"))
    await bridge.sessions.stop_all()
    answer = bot.messages[-1].text
    assert "/profile" in answer and "/login" in answer and "default" in answer
