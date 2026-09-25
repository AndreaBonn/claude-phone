import logging
from functools import partial
from pathlib import Path
from typing import Any

from telegram.error import TelegramError

from src.project_manager import Sandbox, SandboxError
from src.telegram_io import with_retry

logger = logging.getLogger(__name__)

# Bot API limit for uploads by a bot through the public api.telegram.org server.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
BYTES_PER_MB = 1024 * 1024
MAX_FILES = 10
OUTSIDE = "🚫 {raw}: fuori dalle cartelle consentite"
MISSING = "⚠️ {raw}: file non trovato"
EMPTY = "⚠️ {raw}: file vuoto, Telegram non lo accetta"
TOO_LARGE = "⚠️ {raw}: troppo grande per Telegram (limite {limit} MB)"
OVER_CAP = "⚠️ {count} file non inviati: massimo {cap} per messaggio"
UPLOAD_FAILED = "⚠️ {name}: invio non riuscito ({error})"


def _check(raw: str, cwd: Path, sandbox: Sandbox) -> Path | str:
    """The resolved file, or the reason it cannot be sent."""
    try:
        path = sandbox.resolve(raw=raw, cwd=cwd)
    except SandboxError:
        return OUTSIDE.format(raw=raw)
    if not path.is_file():
        return MISSING.format(raw=raw)
    size = path.stat().st_size
    if size == 0:
        return EMPTY.format(raw=raw)
    if size > MAX_UPLOAD_BYTES:
        return TOO_LARGE.format(raw=raw, limit=MAX_UPLOAD_BYTES // BYTES_PER_MB)
    return path


def resolve_attachments(
    raw_paths: list[str], cwd: Path, sandbox: Sandbox
) -> tuple[list[Path], list[str]]:
    """Validate the files Claude asked to attach.

    Parameters
    ----------
    raw_paths : list[str]
        Paths from `[[file: ...]]` lines: absolute, `~`, or relative to `cwd`.
    cwd : Path
        The project directory Claude works in.
    sandbox : Sandbox
        Same sandbox as the gate: the bridge directory and anything outside
        the roots are never sent, even though Claude named them.

    Returns
    -------
    tuple[list[Path], list[str]]
        Files to send, without duplicates, and one user-facing line per refused path.
    """
    paths: list[Path] = []
    problems: list[str] = []
    for raw in raw_paths:
        checked = _check(raw, cwd, sandbox)
        if isinstance(checked, str):
            problems.append(checked)
        elif checked not in paths:
            paths.append(checked)
    if len(paths) > MAX_FILES:
        problems.append(OVER_CAP.format(count=len(paths) - MAX_FILES, cap=MAX_FILES))
    return paths[:MAX_FILES], problems


async def send_documents(bot: Any, chat_id: int, paths: list[Path]) -> tuple[list[Path], list[str]]:
    """Upload each file as a Telegram document; returns (sent, problems).

    Documents, not photos: Telegram keeps the original bytes and name, and the
    phone opens PDFs, images and HTML from the chat. A failed upload is
    reported, never raised: the answer text has already been delivered.
    """
    sent: list[Path] = []
    problems: list[str] = []
    for path in paths:
        try:
            upload = partial(bot.send_document, chat_id=chat_id, document=path, filename=path.name)
            await with_retry(upload)
        except TelegramError as exc:
            logger.warning("Could not send %s: %s", path, exc)
            problems.append(UPLOAD_FAILED.format(name=path.name, error=exc))
            continue
        sent.append(path)
    return sent, problems
