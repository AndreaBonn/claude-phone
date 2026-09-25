from pathlib import Path

import pytest
from telegram.error import BadRequest

from src import file_delivery
from src.file_delivery import resolve_attachments, send_documents
from src.project_manager import Sandbox
from tests.fakes import FakeBot

CHAT = 42


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    (tmp_path / "root" / "alpha").mkdir(parents=True)
    (tmp_path / "root" / "bridge").mkdir()
    root = (tmp_path / "root").resolve()
    return Sandbox(roots=(root,), excluded=(root / "bridge",))


def cwd(sandbox: Sandbox) -> Path:
    return sandbox.roots[0] / "alpha"


def test_relative_and_absolute_paths_resolve_inside_the_project(sandbox: Sandbox) -> None:
    (cwd(sandbox) / "report.pdf").write_bytes(b"%PDF")
    (cwd(sandbox) / "out").mkdir()
    chart = cwd(sandbox) / "out" / "chart.png"
    chart.write_bytes(b"png")
    paths, problems = resolve_attachments(["report.pdf", str(chart)], cwd(sandbox), sandbox)
    assert paths == [cwd(sandbox) / "report.pdf", chart]
    assert problems == []


def test_paths_outside_the_sandbox_are_refused(sandbox: Sandbox) -> None:
    (sandbox.roots[0] / "bridge" / ".env").write_text("TOKEN=x")
    raw = ["/etc/hostname", "../bridge/.env"]
    paths, problems = resolve_attachments(raw, cwd(sandbox), sandbox)
    assert paths == []
    assert problems == [
        "🚫 /etc/hostname: fuori dalle cartelle consentite",
        "🚫 ../bridge/.env: fuori dalle cartelle consentite",
    ]


def test_missing_empty_and_directory_paths_are_reported(sandbox: Sandbox) -> None:
    (cwd(sandbox) / "empty.txt").write_bytes(b"")
    paths, problems = resolve_attachments(["nope.txt", "empty.txt", "."], cwd(sandbox), sandbox)
    assert paths == []
    assert problems == [
        "⚠️ nope.txt: file non trovato",
        "⚠️ empty.txt: file vuoto, Telegram non lo accetta",
        "⚠️ .: file non trovato",
    ]


def test_files_over_the_telegram_limit_are_reported(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(file_delivery, "MAX_UPLOAD_BYTES", 3 * 1024 * 1024)
    big = cwd(sandbox) / "big.bin"
    with big.open("wb") as handle:
        handle.truncate(3 * 1024 * 1024 + 1)
    paths, problems = resolve_attachments(["big.bin"], cwd(sandbox), sandbox)
    assert paths == []
    assert problems == ["⚠️ big.bin: troppo grande per Telegram (limite 3 MB)"]


def test_the_same_file_is_listed_once(sandbox: Sandbox) -> None:
    (cwd(sandbox) / "a.txt").write_text("a")
    paths, _ = resolve_attachments(["a.txt", "./a.txt"], cwd(sandbox), sandbox)
    assert paths == [cwd(sandbox) / "a.txt"]


async def test_documents_are_sent_with_their_file_name(sandbox: Sandbox) -> None:
    report = cwd(sandbox) / "report.pdf"
    report.write_bytes(b"%PDF")
    bot = FakeBot()
    sent, problems = await send_documents(bot, CHAT, [report])
    assert bot.documents == [(CHAT, b"%PDF", "report.pdf")]
    assert (sent, problems) == ([report], [])


class FailingUploadBot(FakeBot):
    async def send_document(
        self, chat_id: int, document: object, filename: str | None = None
    ) -> None:
        raise BadRequest("Request Entity Too Large")


async def test_a_rejected_upload_is_reported_not_raised(sandbox: Sandbox) -> None:
    report = cwd(sandbox) / "a.txt"
    report.write_text("a")
    sent, problems = await send_documents(FailingUploadBot(), CHAT, [report])
    assert sent == []
    assert problems == ["⚠️ a.txt: invio non riuscito (Request Entity Too Large)"]


def test_files_beyond_the_cap_are_reported_not_dropped(sandbox: Sandbox) -> None:
    raw = []
    for index in range(file_delivery.MAX_FILES + 2):
        (cwd(sandbox) / f"f{index}.txt").write_text("x")
        raw.append(f"f{index}.txt")
    paths, problems = resolve_attachments(raw, cwd(sandbox), sandbox)
    assert len(paths) == file_delivery.MAX_FILES
    assert problems == ["⚠️ 2 file non inviati: massimo 10 per messaggio"]
