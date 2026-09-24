import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, TypeVar

from telegram import InlineKeyboardMarkup, Message
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter

from src.message_formatter import render_progress
from src.telegram_text import TELEGRAM_MAX_LENGTH, markdown_to_telegram_html, split_message

logger = logging.getLogger(__name__)

T = TypeVar("T")
RETRY_ATTEMPTS = 6
RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY = 30.0
PROGRESS_EDIT_INTERVAL = 1.5
NOT_MODIFIED = "message is not modified"


def _retry_after_seconds(exc: RetryAfter) -> float:
    value = exc.retry_after
    return value.total_seconds() if isinstance(value, timedelta) else float(value)


async def with_retry(
    action: Callable[[], Awaitable[T]],
    attempts: int = RETRY_ATTEMPTS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run a Telegram call, retrying flood limits and network errors with backoff.

    BadRequest and Forbidden are permanent (they subclass NetworkError in
    python-telegram-bot, hence the explicit re-raise before the retry branch).
    """
    for attempt in range(1, attempts + 1):
        try:
            return await action()
        except (BadRequest, Forbidden):
            raise
        except RetryAfter as exc:
            delay = _retry_after_seconds(exc)
        except NetworkError as exc:
            if attempt == attempts:
                raise
            delay = min(RETRY_BASE_DELAY * 2 ** (attempt - 1), RETRY_MAX_DELAY)
            logger.warning("Telegram call failed (%s), retry %d in %.1fs", exc, attempt, delay)
        await sleep(delay)
    raise NetworkError(f"Telegram call failed after {attempts} attempts")


async def _send_chunk(
    bot: Any, chat_id: int, chunk: str, markup: InlineKeyboardMarkup | None
) -> Message:
    html = markdown_to_telegram_html(chunk)

    async def send_html() -> Message:
        if len(html) > TELEGRAM_MAX_LENGTH:
            raise BadRequest("HTML chunk too long after escaping")
        return await bot.send_message(
            chat_id=chat_id, text=html, parse_mode=ParseMode.HTML, reply_markup=markup
        )

    async def send_plain() -> Message:
        return await bot.send_message(chat_id=chat_id, text=chunk, reply_markup=markup)

    try:
        return await with_retry(send_html)
    except BadRequest as exc:
        logger.warning("HTML rejected (%s), sending plain text", exc)
        return await with_retry(send_plain)


async def send_text(
    bot: Any, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> list[Message]:
    """Send Markdown-ish text as one or more HTML messages; markup goes on the last one.

    A chunk whose HTML Telegram refuses to parse is re-sent as plain text, so a
    formatting glitch never swallows Claude's answer.
    """
    chunks = split_message(text) or ["(risposta vuota)"]
    last = len(chunks) - 1
    return [
        await _send_chunk(bot, chat_id, chunk, reply_markup if index == last else None)
        for index, chunk in enumerate(chunks)
    ]


async def edit_text(bot: Any, chat_id: int, message_id: int, text: str, html: bool) -> None:
    """Edit a message, ignoring the harmless 'message is not modified' error."""
    try:
        await with_retry(
            lambda: bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.HTML if html else None,
            )
        )
    except BadRequest as exc:
        if NOT_MODIFIED not in str(exc).lower():
            raise


class ProgressMessage:
    """The single 'working…' message edited in place while Claude runs.

    Edits are throttled to one every PROGRESS_EDIT_INTERVAL seconds to stay
    under Telegram's edit rate limit; a pending update is flushed later.
    """

    def __init__(self, bot: Any, chat_id: int, message_id: int, header: str) -> None:
        self._bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.header = header
        self.lines: list[str] = []
        self._last_edit = 0.0
        self._flush_task: asyncio.Task[None] | None = None

    @classmethod
    async def create(cls, bot: Any, chat_id: int, header: str) -> "ProgressMessage":
        message = await with_retry(lambda: bot.send_message(chat_id=chat_id, text=header))
        return cls(bot, chat_id, message.message_id, header)

    def render(self) -> str:
        return render_progress(self.header, self.lines, limit=TELEGRAM_MAX_LENGTH)

    async def add_line(self, line: str) -> None:
        self.lines.append(line)
        wait = PROGRESS_EDIT_INTERVAL - (time.monotonic() - self._last_edit)
        if wait <= 0:
            await self._edit()
        elif self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._delayed_edit(wait))

    async def _delayed_edit(self, wait: float) -> None:
        await asyncio.sleep(wait)
        await self._edit()

    async def _edit(self) -> None:
        self._last_edit = time.monotonic()
        try:
            await edit_text(self._bot, self.chat_id, self.message_id, self.render(), html=False)
        except Exception:
            # Progress is best effort: a failed edit must not abort Claude's turn.
            logger.exception("Could not update progress message")

    def drop_last(self, line: str | None) -> None:
        """Forget the latest line if it equals `line` (not yet shown by finish)."""
        if line is not None and self.lines and self.lines[-1] == line:
            self.lines.pop()

    async def finish(self, header: str) -> None:
        self._cancel_flush()
        self.header = header
        await self._edit()

    async def delete(self) -> None:
        self._cancel_flush()
        try:
            await with_retry(
                lambda: self._bot.delete_message(chat_id=self.chat_id, message_id=self.message_id)
            )
        except Exception:
            logger.exception("Could not delete progress message")

    def _cancel_flush(self) -> None:
        if self._flush_task is not None:
            self._flush_task.cancel()
            self._flush_task = None
