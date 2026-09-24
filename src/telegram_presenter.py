import html
import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from src.message_formatter import format_approval_request
from src.permission_gate import ApprovalDecision, ApprovalRequest
from src.session_store import SessionStore
from src.telegram_io import edit_text, send_text, with_retry

logger = logging.getLogger(__name__)

APPROVAL_PREFIX = "ap"
DECISION_LABELS = {
    ApprovalDecision.APPROVE: "✅ Approvato",
    ApprovalDecision.DENY: "❌ Negato",
    ApprovalDecision.DENY_AND_STOP: "🚫 Negato, sessione fermata",
}
BUTTON_LABELS = {
    ApprovalDecision.APPROVE: "✅ Approva",
    ApprovalDecision.DENY: "❌ Nega",
    ApprovalDecision.DENY_AND_STOP: "🚫 Nega e stop sessione",
}
RESTART_NOTE = "⚠️ Richiesta annullata: il bot è stato riavviato."


def approval_keyboard(approval_id: str) -> InlineKeyboardMarkup:
    def button(decision: ApprovalDecision) -> InlineKeyboardButton:
        data = f"{APPROVAL_PREFIX}:{approval_id}:{decision.value}"
        return InlineKeyboardButton(BUTTON_LABELS[decision], callback_data=data)

    return InlineKeyboardMarkup(
        [
            [button(ApprovalDecision.APPROVE), button(ApprovalDecision.DENY)],
            [button(ApprovalDecision.DENY_AND_STOP)],
        ]
    )


class TelegramApprovalPresenter:
    """Shows approval prompts as inline-keyboard messages and closes them."""

    def __init__(self, bot: Any, store: SessionStore, default_chat_id: int) -> None:
        self._bot = bot
        self._store = store
        self._default_chat_id = default_chat_id
        # Chat that sent the last message for each project: prompts go there.
        self.project_chats: dict[str, int] = {}
        self._prompts: dict[str, tuple[int, int, str]] = {}

    async def show(self, request: ApprovalRequest) -> None:
        chat_id = self.project_chats.get(request.project, self._default_chat_id)
        text = format_approval_request(request.project, request.tool_name, request.tool_input)
        if request.warning:
            text += f"\n{html.escape(request.warning)}"
        message = await with_retry(
            lambda: self._bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=approval_keyboard(request.approval_id),
            )
        )
        self._prompts[request.approval_id] = (chat_id, message.message_id, text)
        self._store.add_pending_approval(
            request.approval_id, request.project, chat_id, message.message_id
        )

    async def close(self, request: ApprovalRequest, decision: ApprovalDecision, note: str) -> None:
        await self.finalize(request.approval_id, note or DECISION_LABELS[decision])

    async def finalize(self, approval_id: str, note: str) -> None:
        """Replace the buttons of a prompt with the outcome."""
        self._store.remove_pending_approval(approval_id)
        prompt = self._prompts.pop(approval_id, None)
        if prompt is None:
            return
        chat_id, message_id, text = prompt
        await edit_text(
            self._bot, chat_id, message_id, f"{text}\n\n<b>{html.escape(note)}</b>", True
        )

    async def cancel_leftovers(self) -> int:
        """Close prompts left open by a previous run; their hooks are long gone."""
        leftovers = self._store.pop_pending_approvals()
        for pending in leftovers:
            try:
                await edit_text(
                    self._bot, pending.chat_id, pending.message_id, RESTART_NOTE, html=False
                )
            except Exception:
                logger.exception("Could not close stale approval %s", pending.approval_id)
        for chat_id in sorted({pending.chat_id for pending in leftovers}):
            count = sum(1 for pending in leftovers if pending.chat_id == chat_id)
            await send_text(
                self._bot, chat_id, f"⚠️ {count} richieste di approvazione annullate dal riavvio."
            )
        return len(leftovers)
