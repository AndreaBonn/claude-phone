import asyncio
from types import SimpleNamespace
from typing import Any, cast

from telegram import Update

from src.bridge_context import BRIDGE_KEY, BridgeContext
from src.handlers import profile
from src.handlers.profile import BUSY_REPLY, select_profile
from tests.conftest import ALPHA, USER
from tests.fakes import FakeBot, wait_until


def command(bridge: BridgeContext, bot: FakeBot, *args: str) -> tuple[Update, Any]:
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=USER), effective_chat=SimpleNamespace(id=USER)
    )
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={BRIDGE_KEY: bridge}), bot=bot, args=list(args)
    )
    return cast(Update, update), cast(Any, context)


async def test_profile_command_lists_profiles_as_buttons(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    await profile.profile(*command(bridge, bot))
    reply = bot.messages[-1]
    assert reply.text.startswith("👤 Profilo Claude attivo: default")
    assert [row[0].text for row in reply.reply_markup.inline_keyboard] == ["▶️ default", "sales"]


async def test_profile_command_with_name_switches_directly(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    await profile.profile(*command(bridge, bot, "sales"))
    assert bridge.sessions.profile == "sales"
    assert bot.messages[-1].text.startswith("👤 Profilo Claude attivo: sales")


async def test_profile_switch_is_refused_while_claude_works(
    bridge: BridgeContext, bot: FakeBot, monkeypatch: Any
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO", "hang")
    monkeypatch.setattr("src.claude_session.STOP_GRACE_SECONDS", 0.2)

    async def ignore(_event: object) -> None:
        return None

    turn = asyncio.create_task(bridge.sessions.run_turn(ALPHA, "work", ignore))
    await wait_until(lambda: bridge.sessions.is_busy(ALPHA), "turn started")
    reply = await select_profile(bridge, USER, "sales")
    await bridge.sessions.stop_all()
    await asyncio.gather(turn, return_exceptions=True)
    assert reply == BUSY_REPLY
    assert bridge.sessions.profile == "default"
    assert bridge.store.get_profile(USER) is None
