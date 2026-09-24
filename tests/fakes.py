from dataclasses import dataclass, field
from typing import Any

from telegram.error import BadRequest


@dataclass
class SentMessage:
    chat_id: int
    message_id: int
    text: str
    parse_mode: str | None = None
    reply_markup: Any = None
    deleted: bool = False
    edits: list[str] = field(default_factory=list)


class FakeBot:
    """In-memory Bot double recording what would have reached Telegram."""

    def __init__(self, reject_html: bool = False) -> None:
        self.reject_html = reject_html
        self.messages: list[SentMessage] = []

    def _find(self, message_id: int) -> SentMessage:
        return next(m for m in self.messages if m.message_id == message_id)

    async def send_message(
        self, chat_id: int, text: str, parse_mode: str | None = None, reply_markup: Any = None
    ) -> SentMessage:
        if parse_mode and self.reject_html:
            raise BadRequest("Can't parse entities")
        message = SentMessage(chat_id, len(self.messages) + 1, text, parse_mode, reply_markup)
        self.messages.append(message)
        return message

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str | None = None,
        reply_markup: Any = None,
    ) -> SentMessage:
        message = self._find(message_id)
        if message.text == text and message.reply_markup == reply_markup:
            raise BadRequest("Message is not modified")
        message.text = text
        message.reply_markup = reply_markup
        message.edits.append(text)
        return message

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        self._find(message_id).deleted = True
        return True

    def visible(self) -> list[SentMessage]:
        return [m for m in self.messages if not m.deleted]
