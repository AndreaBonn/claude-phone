import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from src.bridge_context import get_bridge
from src.handlers.projects import switch_project
from src.permission_gate import ApprovalDecision
from src.project_manager import SandboxError
from src.telegram_presenter import DECISION_LABELS
from src.turn_runner import TurnRequest, run_user_turn

logger = logging.getLogger(__name__)

EXPIRED = "Richiesta scaduta o già gestita"
STALE_CHOICE = "Scelta non più valida"
INVALID_BUTTON = "Bottone non valido"


def _parse(data: str | None, parts: int) -> list[str] | None:
    """Split `prefix:a:b` callback data; None if it does not have `parts` fields."""
    fields = (data or "").split(":", maxsplit=parts - 1)
    return fields if len(fields) == parts and all(fields) else None


async def _drop_buttons(update: Update) -> None:
    query = update.callback_query
    assert query is not None
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        logger.debug("Buttons already removed", exc_info=True)


async def handle_approval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`ap:<id>:<decision>` — the user's answer to a permission prompt."""
    bridge = get_bridge(context)
    query = update.callback_query
    assert query is not None
    fields = _parse(query.data, parts=3)
    if fields is None or fields[2] not in set(ApprovalDecision):
        await query.answer(INVALID_BUTTON)
        return
    approval_id, decision = fields[1], ApprovalDecision(fields[2])
    if bridge.broker.resolve(approval_id, decision) is None:
        await query.answer(EXPIRED)
        await _drop_buttons(update)
        return
    await query.answer(DECISION_LABELS[decision])
    await bridge.presenter.finalize(approval_id, DECISION_LABELS[decision])


async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`ch:<token>:<index>` — a choice button under Claude's answer."""
    bridge = get_bridge(context)
    query = update.callback_query
    user = update.effective_user
    assert query is not None and user is not None
    fields = _parse(query.data, parts=3)
    choice = bridge.choices.pop(fields[1], None) if fields else None
    if fields is None or choice is None or not fields[2].isdigit():
        await query.answer(STALE_CHOICE)
        await _drop_buttons(update)
        return
    index = int(fields[2])
    if index >= len(choice.labels):
        await query.answer(STALE_CHOICE)
        return
    # The answer belongs to the project that asked, even after a /switch.
    project, label = choice.project, choice.labels[index]
    await query.answer(f"➡️ {label}")
    await _drop_buttons(update)
    chat_id = query.message.chat.id if query.message is not None else user.id
    request = TurnRequest(chat_id=chat_id, user_id=user.id, project=project, text=label)
    await run_user_turn(bridge, context.bot, request)


async def handle_project_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`pj:<name>` — a project button from /start or /projects."""
    bridge = get_bridge(context)
    query = update.callback_query
    user = update.effective_user
    assert query is not None and user is not None
    fields = _parse(query.data, parts=2)
    if fields is None:
        await query.answer(INVALID_BUTTON)
        return
    name = fields[1]
    try:
        reply = await switch_project(bridge, user.id, name)
    except SandboxError as exc:
        reply = f"🚫 {exc}"
    await query.answer()
    await query.edit_message_text(reply)
