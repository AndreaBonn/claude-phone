from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from telegram import (
    Audio,
    Chat,
    Document,
    Message,
    PhotoSize,
    Update,
    User,
    Video,
    VideoNote,
    Voice,
)
from telegram.error import BadRequest, NetworkError

from src.bridge_context import BRIDGE_KEY, BridgeContext
from src.file_intake import MAX_DOWNLOAD_BYTES, UPLOAD_DIR
from src.handlers import messages
from src.handlers.messages import NO_PROJECT, UNSUPPORTED
from tests.conftest import USER
from tests.fakes import FakeBot

NEW_USER = 7
SENT_AT = datetime(2026, 9, 26, 14, 30, 5, tzinfo=UTC)


def document(file_id: str, name: str | None, size: int | None = 4) -> SimpleNamespace:
    return SimpleNamespace(
        file_id=file_id, file_unique_id=f"u{file_id}", file_name=name, file_size=size
    )


def upload(
    bridge: BridgeContext,
    bot: FakeBot,
    doc: SimpleNamespace | None = None,
    photo: list[SimpleNamespace] | None = None,
    user: int = USER,
    caption: str | None = None,
) -> tuple[Update, Any]:
    message = SimpleNamespace(
        chat_id=user, document=doc, photo=photo or [], caption=caption, date=SENT_AT
    )
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=user))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={BRIDGE_KEY: bridge}), bot=bot)
    return cast(Update, update), cast(Any, context)


def uploads_of(bridge: BridgeContext) -> Path:
    return bridge.settings.approved_directory[0] / "alpha" / UPLOAD_DIR


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.telegram_io.RETRY_BASE_DELAY", 0)


async def test_document_is_saved_in_uploads_and_confirmed(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    bot.files["f1"] = b"%PDF"
    await messages.handle_attachment(*upload(bridge, bot, doc=document("f1", "report.pdf")))
    assert (uploads_of(bridge) / "report.pdf").read_bytes() == b"%PDF"
    assert [m.shown for m in bot.messages] == ["📎 Salvato in uploads/report.pdf"]


async def test_caption_starts_a_turn_that_names_the_file(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    bot.files["f1"] = b"%PDF"
    doc = document("f1", "report.pdf")
    await messages.handle_attachment(*upload(bridge, bot, doc=doc, caption=" riassumilo "))
    await bridge.sessions.stop_all()
    assert bot.messages[0].shown == "📎 Salvato in uploads/report.pdf"
    assert bot.messages[-1].shown == "echo: 📎 File ricevuto: uploads/report.pdf\n\nriassumilo"


async def test_blank_caption_starts_no_turn(bridge: BridgeContext, bot: FakeBot) -> None:
    bot.files["f1"] = b"x"
    await messages.handle_attachment(
        *upload(bridge, bot, doc=document("f1", "a.txt"), caption="  ")
    )
    assert [m.shown for m in bot.messages] == ["📎 Salvato in uploads/a.txt"]


async def test_same_name_twice_keeps_both_files(bridge: BridgeContext, bot: FakeBot) -> None:
    bot.files["f1"] = b"one"
    bot.files["f2"] = b"two"
    await messages.handle_attachment(*upload(bridge, bot, doc=document("f1", "a.txt")))
    await messages.handle_attachment(*upload(bridge, bot, doc=document("f2", "a.txt")))
    assert (uploads_of(bridge) / "a.txt").read_bytes() == b"one"
    assert (uploads_of(bridge) / "a (1).txt").read_bytes() == b"two"
    assert bot.messages[-1].shown == "📎 Salvato in uploads/a (1).txt"


async def test_hostile_name_stays_inside_uploads(bridge: BridgeContext, bot: FakeBot) -> None:
    bot.files["f1"] = b"x"
    await messages.handle_attachment(*upload(bridge, bot, doc=document("f1", "../../.bashrc")))
    assert [p.name for p in uploads_of(bridge).iterdir()] == ["bashrc"]


async def test_photo_downloads_the_largest_size(bridge: BridgeContext, bot: FakeBot) -> None:
    sizes = [
        SimpleNamespace(file_id=f"p{i}", file_unique_id=f"q{i}", file_size=i) for i in range(3)
    ]
    bot.files["p2"] = b"jpeg"
    await messages.handle_attachment(*upload(bridge, bot, photo=sizes))
    assert bot.file_requests == ["p2"]
    saved = uploads_of(bridge) / "photo_20260926_143005_q2.jpg"
    assert saved.read_bytes() == b"jpeg"


async def test_oversized_file_is_refused_before_any_download(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    big = document("f1", "video.mp4", size=MAX_DOWNLOAD_BYTES + 1)
    await messages.handle_attachment(*upload(bridge, bot, doc=big))
    assert bot.file_requests == []
    assert bot.messages[-1].shown == (
        "⚠️ video.mp4: troppo grande, un bot Telegram può scaricare al massimo 20 MB"
    )
    assert not uploads_of(bridge).exists()


async def test_file_too_big_for_telegram_gets_the_same_notice(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    bot.files["f1"] = BadRequest("File is too big")
    await messages.handle_attachment(*upload(bridge, bot, doc=document("f1", "x.zip", size=None)))
    assert "troppo grande" in bot.messages[-1].shown


async def test_failed_download_leaves_nothing_behind(bridge: BridgeContext, bot: FakeBot) -> None:
    bot.files["f1"] = NetworkError("connection reset")
    await messages.handle_attachment(*upload(bridge, bot, doc=document("f1", "a.txt")))
    assert bot.messages[-1].shown == "⚠️ a.txt: download non riuscito (NetworkError), riprova"
    assert not uploads_of(bridge).exists()


async def test_uploads_linked_outside_the_sandbox_is_refused(
    bridge: BridgeContext, bot: FakeBot, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    uploads_of(bridge).symlink_to(outside)
    bot.files["f1"] = b"x"
    await messages.handle_attachment(*upload(bridge, bot, doc=document("f1", "a.txt")))
    assert bot.messages[-1].shown.startswith("🚫 a.txt: la cartella uploads è fuori dal progetto")
    assert list(outside.iterdir()) == []


async def test_removed_project_is_reported(bridge: BridgeContext, bot: FakeBot) -> None:
    (bridge.settings.approved_directory[0] / "alpha").rmdir()
    bot.files["f1"] = b"x"
    await messages.handle_attachment(*upload(bridge, bot, doc=document("f1", "a.txt")))
    assert "non esiste" in bot.messages[-1].shown


async def test_upload_without_project_asks_to_choose_one(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    bot.files["f1"] = b"x"
    await messages.handle_attachment(
        *upload(bridge, bot, doc=document("f1", "a.txt"), user=NEW_USER)
    )
    assert bot.messages[-1].text == NO_PROJECT
    assert bot.messages[-1].reply_markup is not None
    assert bot.file_requests == []


async def test_message_without_attachment_is_ignored(bridge: BridgeContext, bot: FakeBot) -> None:
    await messages.handle_attachment(*upload(bridge, bot))
    assert bot.messages == []


def first_handler_for(bridge: BridgeContext, **content: Any) -> Any:
    """The group-0 handler python-telegram-bot would dispatch this private message to."""
    from src.bot import build_application

    app = build_application(bridge.settings)
    app.bot_data[BRIDGE_KEY].store.close()
    message = Message(
        message_id=1,
        date=SENT_AT,
        chat=Chat(id=USER, type=Chat.PRIVATE),
        from_user=User(id=USER, first_name="u", is_bot=False),
        **content,
    )
    update = Update(update_id=1, message=message)
    return next(h for h in app.handlers[0] if h.check_update(update))


@pytest.mark.parametrize(
    "content",
    [
        {"document": Document(file_id="f", file_unique_id="u", file_name="a.pdf")},
        {"photo": (PhotoSize(file_id="p", file_unique_id="q", width=1, height=1),)},
        {"document": Document(file_id="f", file_unique_id="u"), "caption": "riassumilo"},
    ],
)
def test_documents_and_photos_reach_the_upload_handler(
    bridge: BridgeContext, content: dict[str, Any]
) -> None:
    assert first_handler_for(bridge, **content).callback is messages.handle_attachment


def test_text_still_reaches_the_text_handler(bridge: BridgeContext) -> None:
    assert first_handler_for(bridge, text="ciao").callback is messages.handle_text


@pytest.mark.parametrize(
    "content",
    [
        {"video": Video(file_id="v", file_unique_id="w", width=1, height=1, duration=1)},
        {"audio": Audio(file_id="a", file_unique_id="b", duration=1)},
        {"voice": Voice(file_id="o", file_unique_id="p", duration=1)},
        {"video_note": VideoNote(file_id="n", file_unique_id="m", length=1, duration=1)},
    ],
)
def test_other_media_reach_the_unsupported_handler(
    bridge: BridgeContext, content: dict[str, Any]
) -> None:
    assert first_handler_for(bridge, **content).callback is messages.handle_unsupported_media


async def test_unsupported_media_explains_how_to_send_a_file(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    await messages.handle_unsupported_media(*upload(bridge, bot))
    assert bot.messages[-1].shown == UNSUPPORTED
    assert bot.file_requests == []
