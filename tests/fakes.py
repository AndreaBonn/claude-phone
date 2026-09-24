import asyncio
import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
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

    @property
    def shown(self) -> str:
        """Text as the user sees it: tags dropped and entities decoded for HTML."""
        if not self.parse_mode:
            return self.text
        return html.unescape(re.sub(r"<[^>]+>", "", self.text))


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
        message.parse_mode = parse_mode
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


class FakeCallbackQuery:
    """Callback query double: records answers, edits and dropped keyboards."""

    def __init__(self, data: str | None, chat_id: int, fail_markup_edit: bool = False) -> None:
        self.data = data
        self.message = SimpleNamespace(chat=SimpleNamespace(id=chat_id))
        self.answers: list[str | None] = []
        self.edited_text: str | None = None
        self.edited_markup: Any = None
        self.markup_dropped = False
        self._fail_markup_edit = fail_markup_edit

    async def answer(self, text: str | None = None) -> None:
        self.answers.append(text)

    async def edit_message_text(self, text: str, reply_markup: Any = None) -> None:
        self.edited_text = text
        self.edited_markup = reply_markup

    async def edit_message_reply_markup(self, reply_markup: Any = None) -> None:
        if self._fail_markup_edit:
            raise BadRequest("Message to edit not found")
        self.markup_dropped = reply_markup is None
