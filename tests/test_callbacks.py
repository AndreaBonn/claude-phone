from types import SimpleNamespace
from typing import Any, cast

import pytest
from telegram import Update

from src.bridge_context import BRIDGE_KEY, BridgeContext, ChoiceSet
from src.handlers import callbacks
from src.handlers.callbacks import INVALID_BUTTON, STALE_CHOICE
from src.handlers.projects import project_token
from tests.conftest import ALPHA, BETA, USER
from tests.fakes import FakeBot, FakeCallbackQuery


def press(
    bridge: BridgeContext, bot: FakeBot, data: str | None, fail_markup_edit: bool = False
) -> tuple[Update, Any, FakeCallbackQuery]:
    query = FakeCallbackQuery(data, chat_id=USER, fail_markup_edit=fail_markup_edit)
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=USER))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={BRIDGE_KEY: bridge}), bot=bot)
    return cast(Update, update), cast(Any, context), query


@pytest.mark.parametrize(
    ("handler", "data"),
    [
        (callbacks.handle_approval, "ap:id1:maybe"),
        (callbacks.handle_approval, None),
        (callbacks.handle_project_pick, "pj"),
        (callbacks.handle_project_page, "pg:two"),
        (callbacks.handle_profile_pick, "pf:"),
    ],
)
async def test_malformed_buttons_are_answered_as_invalid(
    bridge: BridgeContext, bot: FakeBot, handler: Any, data: str | None
) -> None:
    update, context, query = press(bridge, bot, data)
    await handler(update, context)
    assert query.answers == [INVALID_BUTTON]


async def test_choice_with_out_of_range_index_is_stale(bridge: BridgeContext, bot: FakeBot) -> None:
    bridge.choices["tok"] = ChoiceSet(project=ALPHA, labels=["only"])
    update, context, query = press(bridge, bot, "ch:tok:5")
    await callbacks.handle_choice(update, context)
    assert query.answers == [STALE_CHOICE]
    assert bot.messages == []


async def test_stale_choice_survives_a_message_that_can_no_longer_be_edited(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    update, context, query = press(bridge, bot, "ch:gone:0", fail_markup_edit=True)
    await callbacks.handle_choice(update, context)
    assert query.answers == [STALE_CHOICE]


async def test_project_button_for_a_removed_project_explains_it(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    update, context, query = press(bridge, bot, f"pj:{project_token('sandbox/deleted')}")
    await callbacks.handle_project_pick(update, context)
    assert query.edited_text == "🚫 Progetto non più disponibile: usa /projects"
    assert bridge.store.get_active_project(USER) == ALPHA


async def test_project_button_switches_project(bridge: BridgeContext, bot: FakeBot) -> None:
    update, context, query = press(bridge, bot, f"pj:{project_token(BETA)}")
    await callbacks.handle_project_pick(update, context)
    assert bridge.store.get_active_project(USER) == BETA
    assert query.edited_text is not None and query.edited_text.startswith("📁 Progetto attivo")


async def test_profile_button_for_unknown_profile_is_refused(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    update, context, query = press(bridge, bot, "pf:ghost")
    await callbacks.handle_profile_pick(update, context)
    assert query.edited_text == "🚫 Profilo sconosciuto: 'ghost'"
    assert bridge.sessions.profile == "default"
