import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

from src import telegram_io
from src.telegram_io import ProgressMessage, send_text, with_retry
from tests.fakes import FakeBot, SentMessage


class RecordingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def flaky(errors: list[Exception]) -> Callable[[], Awaitable[str]]:
    async def action() -> str:
        if errors:
            raise errors.pop(0)
        return "ok"

    return action


async def test_with_retry_recovers_from_network_errors() -> None:
    sleep = RecordingSleep()
    assert await with_retry(flaky([NetworkError("down"), TimedOut()]), sleep=sleep) == "ok"
    assert sleep.delays == [1.0, 2.0]


async def test_with_retry_honours_retry_after() -> None:
    sleep = RecordingSleep()
    error = RetryAfter(retry_after=timedelta(seconds=7))
    assert await with_retry(flaky([error]), sleep=sleep) == "ok"
    assert sleep.delays == [7.0]


async def test_with_retry_does_not_retry_bad_request() -> None:
    sleep = RecordingSleep()
    with pytest.raises(BadRequest):
        await with_retry(flaky([BadRequest("bad"), BadRequest("bad")]), sleep=sleep)
    assert sleep.delays == []


async def test_with_retry_gives_up_after_attempts() -> None:
    with pytest.raises(NetworkError):
        await with_retry(flaky([NetworkError("x")] * 3), attempts=3, sleep=RecordingSleep())


async def test_send_text_splits_and_puts_markup_on_last_chunk() -> None:
    bot = FakeBot()
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("x", callback_data="x")]])
    await send_text(bot, chat_id=1, text="a\n" * 3000, reply_markup=keyboard)
    assert len(bot.messages) > 1
    assert bot.messages[-1].reply_markup is keyboard
    assert all(m.reply_markup is None for m in bot.messages[:-1])
    assert all(m.parse_mode == "HTML" for m in bot.messages)


async def test_send_text_falls_back_to_plain_text() -> None:
    bot = FakeBot(reject_html=True)
    await send_text(bot, chat_id=1, text="**hi** <x>")
    assert [(m.text, m.parse_mode) for m in bot.messages] == [("**hi** <x>", None)]


async def test_progress_message_throttles_and_flushes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telegram_io, "PROGRESS_EDIT_INTERVAL", 0.2)
    bot = FakeBot()
    progress = await ProgressMessage.create(bot, chat_id=1, header="⏳")
    for index in range(5):
        await progress.add_line(f"line {index}")
    assert len(bot.messages[0].edits) == 1
    await asyncio.sleep(0.3)
    assert bot.messages[0].text.endswith("line 4")
    await progress.finish("✅ done")
    assert bot.messages[0].text.startswith("✅ done")


async def test_with_retry_gives_up_after_repeated_flood_limits() -> None:
    errors: list[Exception] = [RetryAfter(retry_after=timedelta(seconds=1))] * 3
    with pytest.raises(NetworkError):
        await with_retry(flaky(errors), attempts=3, sleep=RecordingSleep())


async def test_send_text_falls_back_to_plain_when_escaping_overflows() -> None:
    bot = FakeBot()
    text = "<" * 3000
    await send_text(bot, chat_id=1, text=text)
    assert [(m.text, m.parse_mode) for m in bot.messages] == [(text, None)]


class FailingEditBot(FakeBot):
    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str | None = None,
        reply_markup: Any = None,
    ) -> SentMessage:
        raise BadRequest("Message to edit not found")

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        raise BadRequest("Message can't be deleted")


async def test_edit_text_propagates_real_errors() -> None:
    with pytest.raises(BadRequest, match="not found"):
        await telegram_io.edit_text(FailingEditBot(), chat_id=1, message_id=1, text="x", html=False)


async def test_edit_text_ignores_message_not_modified() -> None:
    bot = FakeBot()
    sent = await bot.send_message(chat_id=1, text="same")
    await telegram_io.edit_text(bot, chat_id=1, message_id=sent.message_id, text="same", html=False)
    assert sent.edits == []


async def test_progress_failures_never_abort_the_turn() -> None:
    bot = FailingEditBot()
    progress = await ProgressMessage.create(bot, chat_id=1, header="⏳")
    await progress.add_line("line")
    await progress.finish("✅")
    await progress.delete()
    assert progress.lines == ["line"]
