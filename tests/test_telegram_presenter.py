import asyncio
from typing import Any

from src.bridge_context import BridgeContext
from src.permission_gate import ApprovalDecision, ApprovalRequest
from src.telegram_presenter import DECISION_LABELS, RESTART_NOTE
from tests.conftest import ALPHA, USER
from tests.fakes import FakeBot, SentMessage


def approval(warning: str = "") -> ApprovalRequest:
    future: asyncio.Future[ApprovalDecision] = asyncio.get_running_loop().create_future()
    return ApprovalRequest("a1", ALPHA, "Bash", {"command": "ls"}, warning, future)


async def test_prompt_includes_the_policy_warning(bridge: BridgeContext, bot: FakeBot) -> None:
    await bridge.presenter.show(approval(warning="⚠️ <non analizzabile>"))
    assert bot.messages[-1].shown.endswith("⚠️ <non analizzabile>")


async def test_close_without_note_shows_the_decision_label(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    request = approval()
    await bridge.presenter.show(request)
    await bridge.presenter.close(request, ApprovalDecision.DENY, note="")
    assert bot.messages[-1].shown.endswith(DECISION_LABELS[ApprovalDecision.DENY])
    assert bot.messages[-1].reply_markup is None
    assert bridge.store.pop_pending_approvals() == []


async def test_finalize_of_an_unknown_prompt_is_harmless(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    await bridge.presenter.finalize("never-shown", "✅")
    assert bot.messages == []


class FailingEditBot(FakeBot):
    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str | None = None,
        reply_markup: Any = None,
    ) -> SentMessage:
        raise ConnectionError("telegram down")


async def test_restart_cleanup_still_notifies_when_old_prompts_cannot_be_edited(
    tmp_path: object,
) -> None:
    from pathlib import Path

    from tests.conftest import make_bridge

    bot = FailingEditBot()
    bridge = make_bridge(Path(str(tmp_path)), bot)
    bridge.store.add_pending_approval("old", ALPHA, USER, 1)
    assert await bridge.presenter.cancel_leftovers() == 1
    bridge.store.close()
    assert bot.messages[-1].shown == "⚠️ 1 richieste di approvazione annullate dal riavvio."
    assert RESTART_NOTE not in [m.text for m in bot.messages]
