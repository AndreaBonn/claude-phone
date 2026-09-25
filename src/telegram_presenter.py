import html
import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from src.message_formatter import approval_overflow, format_approval_request
from src.permission_gate import ApprovalDecision, ApprovalRequest
from src.session_store import SessionStore
from src.telegram_io import edit_text, send_text, with_retry

logger = logging.getLogger(__name__)

APPROVAL_PREFIX = "ap"
DECISION_LABELS = {
    ApprovalDecision.APPROVE: "✅ Approvato",
    ApprovalDecision.APPROVE_ALWAYS: "🔁 Approvato per la sessione",
    ApprovalDecision.DENY: "❌ Negato",
    ApprovalDecision.DENY_AND_STOP: "🚫 Negato, sessione fermata",
}
BUTTON_LABELS = {
    ApprovalDecision.APPROVE: "✅ Approva",
    ApprovalDecision.DENY: "❌ Nega",
    ApprovalDecision.DENY_AND_STOP: "🚫 Nega e stop sessione",
}
ALWAYS_BASH_LABEL = "🔁 Sempre questo comando"
ALWAYS_TOOL_LABEL = "🔁 Sempre {tool} in questa sessione"
RESTART_NOTE = "⚠️ Richiesta annullata: il bot è stato riavviato."
TRUNCATED_NOTE = "⚠️ Contenuto troncato ({length} caratteri): quello completo è nell'allegato"
FULL_BODY_FILENAME = "approvazione-{approval_id}.txt"


def _always_label(tool_name: str) -> str:
    # Mirrors permission_gate.grant_key: Bash grants one command, the rest the tool.
    if tool_name == "Bash":
        return ALWAYS_BASH_LABEL
    return ALWAYS_TOOL_LABEL.format(tool=tool_name)


def approval_keyboard(
    approval_id: str, tool_name: str, allow_always: bool = True
) -> InlineKeyboardMarkup:
    def button(decision: ApprovalDecision, label: str | None = None) -> InlineKeyboardButton:
        data = f"{APPROVAL_PREFIX}:{approval_id}:{decision.value}"
        return InlineKeyboardButton(label or BUTTON_LABELS[decision], callback_data=data)

    rows = [[button(ApprovalDecision.APPROVE), button(ApprovalDecision.DENY)]]
    if allow_always:
        rows.append([button(ApprovalDecision.APPROVE_ALWAYS, _always_label(tool_name))])
    rows.append([button(ApprovalDecision.DENY_AND_STOP)])
    return InlineKeyboardMarkup(rows)


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
        # A cut body goes out in full first, and "always" is withheld: nobody
        # grants for good what they could not read. A failed upload raises,
        # and the broker denies the call.
        full_body = approval_overflow(request.tool_name, request.tool_input)
        if full_body is not None:
            await self._send_full_body(chat_id, request.approval_id, full_body)
            text += f"\n{html.escape(TRUNCATED_NOTE.format(length=len(full_body)))}"
        keyboard = approval_keyboard(
            request.approval_id, request.tool_name, allow_always=full_body is None
        )
        message = await with_retry(
            lambda: self._bot.send_message(
                chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=keyboard
            )
        )
        self._prompts[request.approval_id] = (chat_id, message.message_id, text)
        self._store.add_pending_approval(
            request.approval_id, request.project, chat_id, message.message_id
        )

    async def _send_full_body(self, chat_id: int, approval_id: str, body: str) -> None:
        filename = FULL_BODY_FILENAME.format(approval_id=approval_id)
        await with_retry(
            lambda: self._bot.send_document(
                chat_id=chat_id, document=body.encode(), filename=filename
            )
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
