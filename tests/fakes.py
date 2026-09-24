import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from telegram.error import BadRequest, Forbidden


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
        self.commands: list[Any] = []
        # Chats that have not started the bot: Telegram answers Forbidden.
        self.blocked_chats: set[int] = set()

    async def set_my_commands(self, commands: list[Any]) -> bool:
        self.commands = list(commands)
        return True

    def _find(self, message_id: int) -> SentMessage:
        return next(m for m in self.messages if m.message_id == message_id)

    async def send_message(
        self, chat_id: int, text: str, parse_mode: str | None = None, reply_markup: Any = None
    ) -> SentMessage:
        if chat_id in self.blocked_chats:
            raise Forbidden("bot can't initiate conversation with a user")
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


async def wait_until(condition: Callable[[], bool], description: str, timeout: float = 5.0) -> None:
    """Poll `condition` every 10 ms; fail with `description` after `timeout` seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise AssertionError(f"timed out waiting for: {description}")
        await asyncio.sleep(0.01)
