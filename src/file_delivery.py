import logging
from functools import partial
from pathlib import Path, PurePath
from typing import Any

from telegram.error import TelegramError

from src.project_manager import Sandbox, SandboxError
from src.stream_parser import StreamEvent, ToolResultEvent, ToolUseEvent
from src.telegram_io import with_retry

logger = logging.getLogger(__name__)

# Bot API limit for uploads by a bot through the public api.telegram.org server.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
BYTES_PER_MB = 1024 * 1024
MAX_FILES = 10
# Files a person opens on a phone; a Write of any other type is ordinary coding work.
DELIVERABLE_SUFFIXES = frozenset(
    {
        ".md",
        ".txt",
        ".pdf",
        ".html",
        ".htm",
        ".csv",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".docx",
        ".xlsx",
        ".pptx",
    }
)
OUTSIDE = "🚫 {raw}: fuori dalle cartelle consentite"
MISSING = "⚠️ {raw}: file non trovato"
EMPTY = "⚠️ {raw}: file vuoto, Telegram non lo accetta"
TOO_LARGE = "⚠️ {raw}: troppo grande per Telegram (limite {limit} MB)"
OVER_CAP = "⚠️ {count} file non inviati: massimo {cap} per messaggio"
UPLOAD_FAILED = "⚠️ {name}: invio non riuscito ({error})"


class WrittenFiles:
    """Deliverables created by a successful Write during the current turn.

    Claude does not reliably end its answer with the `[[file: ...]]` lines the
    system prompt asks for (observed with a user config that prescribes its own
    closing lines), so the bridge attaches what it saw being written.
    """

    def __init__(self) -> None:
        self.paths: list[str] = []
        self._pending: dict[str, str] = {}

    def observe(self, event: StreamEvent) -> None:
        if isinstance(event, ToolUseEvent) and event.name == "Write":
            path = str(event.input.get("file_path", ""))
            if PurePath(path).suffix.lower() in DELIVERABLE_SUFFIXES:
                self._pending[event.tool_use_id] = path
        elif isinstance(event, ToolResultEvent):
            # A denied or failed Write comes back as an error result: nothing to send.
            written = self._pending.pop(event.tool_use_id, None)
            if written is not None and not event.is_error and written not in self.paths:
                self.paths.append(written)


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
