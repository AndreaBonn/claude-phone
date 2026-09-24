import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from telegram import Update

from src.bridge_context import BRIDGE_KEY, BridgeContext
from src.handlers import commands, messages
from src.handlers.messages import NO_PROJECT
from tests.conftest import ALPHA, BETA, USER
from tests.fakes import FakeBot, wait_until

NEW_USER = 7


def command(
    bridge: BridgeContext, bot: FakeBot, *args: str, user: int = USER
) -> tuple[Update, Any]:
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user), effective_chat=SimpleNamespace(id=user)
    )
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={BRIDGE_KEY: bridge}), bot=bot, args=list(args)
    )
    return cast(Update, update), cast(Any, context)


def text_message(
    bridge: BridgeContext, bot: FakeBot, text: str | None, user: int = USER
) -> tuple[Update, Any]:
    message = None if text is None else SimpleNamespace(text=text, chat_id=user)
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=user))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={BRIDGE_KEY: bridge}), bot=bot)
    return cast(Update, update), cast(Any, context)


def last_text(bot: FakeBot) -> str:
    return bot.messages[-1].text


@pytest.fixture
async def busy_bridge(
    bridge: BridgeContext, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[BridgeContext]:
    """A bridge whose active project has a real turn in progress."""
    monkeypatch.setenv("FAKE_SCENARIO", "hang")
    monkeypatch.setattr("src.claude_session.STOP_GRACE_SECONDS", 0.2)

    async def ignore(_event: object) -> None:
        return None

    turn = asyncio.create_task(bridge.sessions.run_turn(ALPHA, "work", ignore))
    await wait_until(lambda: bridge.sessions.is_busy(ALPHA), "turn started")
    yield bridge
    await bridge.sessions.stop_all()
    await asyncio.gather(turn, return_exceptions=True)


async def test_start_lists_projects_with_buttons(bridge: BridgeContext, bot: FakeBot) -> None:
    await commands.start(*command(bridge, bot))
    reply = bot.messages[-1]
    assert reply.text.startswith("🤖 Bridge Claude Code attivo.")
    assert [row[0].text for row in reply.reply_markup.inline_keyboard] == [f"▶️ {ALPHA}", BETA]


async def test_projects_marks_the_active_project(bridge: BridgeContext, bot: FakeBot) -> None:
    await commands.projects(*command(bridge, bot))
    assert f"Attivo: {ALPHA}" in last_text(bot)


async def test_switch_without_argument_shows_usage(bridge: BridgeContext, bot: FakeBot) -> None:
    await commands.switch(*command(bridge, bot))
    assert last_text(bot).startswith("Uso: /switch")
    assert bridge.store.get_active_project(USER) == ALPHA


async def test_switch_changes_project(bridge: BridgeContext, bot: FakeBot) -> None:
    await commands.switch(*command(bridge, bot, BETA))
    assert bridge.store.get_active_project(USER) == BETA


async def test_switch_to_unknown_project_is_refused(bridge: BridgeContext, bot: FakeBot) -> None:
    await commands.switch(*command(bridge, bot, "sandbox/../etc"))
    assert last_text(bot).startswith("🚫")
    assert bridge.store.get_active_project(USER) == ALPHA


async def test_switch_is_refused_while_claude_works(
    busy_bridge: BridgeContext, bot: FakeBot
) -> None:
    await commands.switch(*command(busy_bridge, bot, BETA))
    assert last_text(bot).startswith("⏳")
    assert busy_bridge.store.get_active_project(USER) == ALPHA


async def test_new_session_forgets_the_saved_conversation(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    bridge.store.save_session(f"default::{ALPHA}", "old-session")
    await commands.new_session(*command(bridge, bot))
    assert bridge.sessions.session_id(ALPHA) is None
    assert last_text(bot).startswith("🆕")


async def test_new_session_without_project_asks_for_one(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    await commands.new_session(*command(bridge, bot, user=NEW_USER))
    assert last_text(bot) == "Nessun progetto attivo: usa /projects."


async def test_new_session_is_refused_while_claude_works(
    busy_bridge: BridgeContext, bot: FakeBot
) -> None:
    busy_bridge.store.save_session(f"default::{ALPHA}", "keep-me")
    await commands.new_session(*command(busy_bridge, bot))
    assert last_text(bot).startswith("⏳")
    assert busy_bridge.store.get_session(f"default::{ALPHA}") is not None


async def test_status_reports_project_session_and_profile(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    bridge.store.save_session(f"default::{ALPHA}", "abc-123")
    await commands.status(*command(bridge, bot))
    text = last_text(bot)
    assert f"📁 Progetto: {ALPHA}" in text
    assert "🧵 Sessione: abc-123" in text
    assert "⚙️ Processo Claude: spento, in attesa" in text
    assert "👤 Profilo Claude: default" in text


async def test_status_without_project_skips_session_lines(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    await commands.status(*command(bridge, bot, user=NEW_USER))
    assert "🧵" not in last_text(bot)
    assert "📁 Progetto: nessuno" in last_text(bot)


async def test_verbose_sets_a_valid_level(bridge: BridgeContext, bot: FakeBot) -> None:
    await commands.verbose(*command(bridge, bot, "2"))
    assert bridge.store.get_verbose(USER, default=1) == 2


@pytest.mark.parametrize("args", [(), ("3",), ("alto",)])
async def test_verbose_without_valid_level_shows_current_one(
    bridge: BridgeContext, bot: FakeBot, args: tuple[str, ...]
) -> None:
    await commands.verbose(*command(bridge, bot, *args))
    assert last_text(bot).startswith("Verbosità attuale: 1")
    assert bridge.store.get_verbose(USER, default=1) == 1


async def test_text_goes_to_claude_in_the_active_project(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    await messages.handle_text(*text_message(bridge, bot, "ciao"))
    await bridge.sessions.stop_all()
    assert last_text(bot) == "echo: ciao"


async def test_text_without_project_asks_to_choose_one(bridge: BridgeContext, bot: FakeBot) -> None:
    await messages.handle_text(*text_message(bridge, bot, "ciao", user=NEW_USER))
    assert last_text(bot) == NO_PROJECT
    assert bot.messages[-1].reply_markup is not None


async def test_update_without_text_is_ignored(bridge: BridgeContext, bot: FakeBot) -> None:
    await messages.handle_text(*text_message(bridge, bot, None))
    assert bot.messages == []


async def test_projects_without_any_project_says_so(bridge: BridgeContext, bot: FakeBot) -> None:
    for name in ("alpha", "beta"):
        (bridge.settings.approved_directory[0] / name).rmdir()
    await commands.projects(*command(bridge, bot))
    assert last_text(bot).startswith("Nessun progetto in ")
    assert bot.messages[-1].reply_markup is None
