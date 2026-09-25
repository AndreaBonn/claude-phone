import logging
import os
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePath
from typing import Any

from telegram.error import BadRequest, TelegramError

from src.project_manager import Sandbox, SandboxError
from src.telegram_io import with_retry

logger = logging.getLogger(__name__)

UPLOAD_DIR = "uploads"
# Below the 255-byte NAME_MAX of Linux filesystems, leaving room for " (n)".
MAX_NAME_BYTES = 200
# A longer "extension" is part of the name, not a type to preserve.
MAX_SUFFIX_BYTES = 16
MAX_COPIES = 999
FILE_MODE = 0o644
# Control, format (bidi overrides, zero-width), surrogate, private and unassigned
# characters, plus line and paragraph separators: none belongs in a file name, and
# the format ones would make the name read differently in an approval prompt.
STRIPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
EXCLUSIVE_CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
# getFile limit of the public Bot API server; bigger files need a local server.
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
BYTES_PER_MB = 1024 * 1024
TOO_BIG_MARKER = "too big"
PHOTO_NAME = "photo_{stamp}_{unique_id}.jpg"
DOCUMENT_FALLBACK = "document_{unique_id}"
STAMP_FORMAT = "%Y%m%d_%H%M%S"
TOO_LARGE = "⚠️ {name}: troppo grande, un bot Telegram può scaricare al massimo {limit} MB"
DOWNLOAD_FAILED = "⚠️ {name}: download non riuscito ({error}), riprova"
OUTSIDE = "🚫 {name}: la cartella {folder} è fuori dal progetto, non salvo il file"
WRITE_FAILED = "⚠️ {name}: salvataggio non riuscito ({error})"


@dataclass(frozen=True)
class Attachment:
    file_id: str
    # Optional in the Bot API: an unknown size is checked by Telegram itself.
    size: int | None
    name: str


class AttachmentError(Exception):
    """An upload that could not be saved; `notice` is the text for the user."""

    def __init__(self, notice: str) -> None:
        super().__init__(notice)
        self.notice = notice


def safe_filename(raw: str | None, fallback: str) -> str:
    """Reduce a sender-chosen file name to a plain, visible basename.

    Directories, invisible characters and leading dots are dropped (a dotfile
    such as `.envrc` is loaded by tools just by entering the directory); an
    empty result becomes `fallback`. Long names are cut to `MAX_NAME_BYTES`
    of UTF-8, keeping the extension.
    """
    name = unicodedata.normalize("NFC", raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch) not in STRIPPED_CATEGORIES)
    name = name.strip().lstrip(".").strip()
    return _truncate(name or fallback)


def _truncate(name: str) -> str:
    if len(name.encode()) <= MAX_NAME_BYTES:
        return name
    suffix = PurePath(name).suffix
    if len(suffix.encode()) > MAX_SUFFIX_BYTES:
        suffix = ""
    budget = MAX_NAME_BYTES - len(suffix.encode())
    stem = name.removesuffix(suffix).encode()[:budget].decode(errors="ignore")
    return stem + suffix


def _candidates(name: str) -> Iterator[str]:
    yield name
    path = PurePath(name)
    for copy in range(1, MAX_COPIES + 1):
        yield f"{path.stem} ({copy}){path.suffix}"


def write_unique(directory: Path, name: str, data: bytes, sandbox: Sandbox) -> Path:
    """Write `data` to `<directory>/uploads/<name>` without ever replacing a file.

    Parameters
    ----------
    directory : Path
        The project directory, already resolved through the sandbox.
    name : str
        Output of `safe_filename`: a basename with no separators.
    data : bytes
        The whole file; nothing is written before it is complete.
    sandbox : Sandbox
        `uploads` is checked after resolving symlinks, so a link to outside
        the roots or into the bridge is refused.

    Returns
    -------
    Path
        The created file; a taken name gets " (1)", " (2)", ... before the suffix.

    Raises
    ------
    SandboxError
        If `uploads` resolves anywhere but inside `directory` itself: a link
        to another project is inside the sandbox, yet still the wrong place.
    OSError
        If the directory cannot be created or the write fails; a partial
        file is removed first.
    """
    uploads = directory / UPLOAD_DIR
    uploads.mkdir(exist_ok=True)
    resolved = sandbox.resolve(raw=UPLOAD_DIR, cwd=directory)
    if resolved != directory.resolve() / UPLOAD_DIR:
        raise SandboxError(f"{UPLOAD_DIR} does not stay inside {directory}")
    dir_fd = os.open(resolved, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        created = _create_exclusive(dir_fd=dir_fd, name=name)
        _write_or_remove(dir_fd=dir_fd, name=created, data=data)
    finally:
        os.close(dir_fd)
    return resolved / created


def _create_exclusive(dir_fd: int, name: str) -> str:
    # O_EXCL fails on any existing entry, a symlink included, so concurrent
    # uploads of the same name each get their own copy and no link is followed.
    for candidate in _candidates(name):
        try:
            os.close(os.open(candidate, EXCLUSIVE_CREATE, FILE_MODE, dir_fd=dir_fd))
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError(f"No free name for {name} after {MAX_COPIES} copies")


def _write_or_remove(dir_fd: int, name: str, data: bytes) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
    except OSError:
        os.unlink(name, dir_fd=dir_fd)
        raise
    finally:
        os.close(fd)


def describe_attachment(message: Any) -> Attachment | None:
    """The document or photo of a message, or None for any other content.

    A photo arrives in several sizes, largest last, and has no name: it gets
    one from the message date and its unique id.
    """
    if message.document is not None:
        document = message.document
        fallback = DOCUMENT_FALLBACK.format(unique_id=document.file_unique_id)
        name = safe_filename(raw=document.file_name, fallback=fallback)
        return Attachment(file_id=document.file_id, size=document.file_size, name=name)
    if message.photo:
        photo = message.photo[-1]
        raw = PHOTO_NAME.format(
            stamp=message.date.strftime(STAMP_FORMAT), unique_id=photo.file_unique_id
        )
        name = safe_filename(raw=raw, fallback=raw)
        return Attachment(file_id=photo.file_id, size=photo.file_size, name=name)
    return None


async def download(bot: Any, attachment: Attachment) -> bytes:
    """Fetch the whole file into memory (at most `MAX_DOWNLOAD_BYTES`).

    The download URL embeds the bot token: it is never built, shown or logged
    here, and the user sees only the error type.
    """
    too_large = AttachmentError(
        TOO_LARGE.format(name=attachment.name, limit=MAX_DOWNLOAD_BYTES // BYTES_PER_MB)
    )
    if attachment.size is not None and attachment.size > MAX_DOWNLOAD_BYTES:
        raise too_large
    try:
        file = await with_retry(partial(bot.get_file, attachment.file_id))
        return bytes(await with_retry(file.download_as_bytearray))
    except BadRequest as exc:
        if TOO_BIG_MARKER in str(exc).lower():
            raise too_large from exc
        raise _download_failed(attachment=attachment, exc=exc) from exc
    except TelegramError as exc:
        raise _download_failed(attachment=attachment, exc=exc) from exc


def _download_failed(attachment: Attachment, exc: Exception) -> AttachmentError:
    logger.warning("Download of %s failed: %s", attachment.name, exc)
    return AttachmentError(DOWNLOAD_FAILED.format(name=attachment.name, error=type(exc).__name__))


async def receive_attachment(
    bot: Any, attachment: Attachment, directory: Path, sandbox: Sandbox
) -> Path:
    """Download an attachment and store it under `<directory>/uploads/`.

    Raises
    ------
    AttachmentError
        With the message for the user, for every expected failure.
    """
    data = await download(bot=bot, attachment=attachment)
    try:
        saved = write_unique(directory=directory, name=attachment.name, data=data, sandbox=sandbox)
    except SandboxError as exc:
        raise AttachmentError(OUTSIDE.format(name=attachment.name, folder=UPLOAD_DIR)) from exc
    except OSError as exc:
        logger.warning("Could not save %s in %s: %s", attachment.name, directory, exc)
        error = exc.strerror or type(exc).__name__
        raise AttachmentError(WRITE_FAILED.format(name=attachment.name, error=error)) from exc
    logger.info("Saved upload %s (%d bytes) in %s", saved.name, len(data), directory)
    return saved
