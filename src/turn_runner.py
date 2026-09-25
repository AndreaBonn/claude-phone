import logging
import secrets
from dataclasses import dataclass
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.bridge_context import BridgeContext, ChoiceSet
from src.claude_session import (
    ClaudeCrashedError,
    ClaudeTimeoutError,
    EventCallback,
    TurnInterruptedError,
)
from src.file_delivery import WrittenFiles, resolve_attachments, send_documents
from src.handlers.projects import project_token
from src.message_formatter import describe_event, extract_choices, extract_files, truncate
from src.project_manager import SandboxError
from src.session_manager import TurnOutcome
from src.stream_parser import StreamEvent, TextEvent
from src.telegram_io import ProgressMessage, send_text

logger = logging.getLogger(__name__)

CHOICE_PREFIX = "ch"
STOP_PREFIX = "sp"
STOP_BUTTON = "⏹️ Stop"
STOPPED_REPLY = "⏹️ Fermo Claude su {project}."
NOTHING_TO_STOP = "Nessun turno in corso su {project}."
INTERRUPTED_NOTICE = "⏹️ Turno interrotto. La sessione resta: il prossimo messaggio la riprende."
AUDIT_DETAIL_MAX = 300
ERROR_DETAIL_MAX = 1500
QUEUED_NOTICE = "📥 Messaggio in coda: Claude sta ancora lavorando sul precedente."
FRESH_SESSION_NOTICE = "🔁 La sessione salvata non esiste più: ne ho aperta una nuova.\n\n"
AUTH_ERROR_MARKERS = ("401", "authenticat", "oauth")
AUTH_HINT = (
    "\n\n🔑 Il login del profilo {profile} non è valido. Sul PC apri Claude Code con quel "
    "profilo e rifai /login (profilo default: `claude`; profilo cloak: `claude -a <nome>`), "
    "oppure scegli un altro profilo con /profile."
)
ATTACHMENTS_ONLY = "📎 File allegati qui sotto."
EMPTY_ANSWER = "(Claude ha chiuso il turno senza testo: se hai negato un'azione è normale.)"


@dataclass(frozen=True)
class Answer:
    text: str
    options: list[str]
    # Raw paths from `[[file: ...]]` lines, validated only when sent.
    files: list[str]


@dataclass(frozen=True)
class TurnRequest:
    chat_id: int
    user_id: int
    project: str
    text: str


def choice_keyboard(
    bridge: BridgeContext, project: str, options: list[str]
) -> InlineKeyboardMarkup:
    token = secrets.token_hex(4)
    bridge.choices[token] = ChoiceSet(project=project, labels=options)
    rows = [
        [InlineKeyboardButton(label, callback_data=f"{CHOICE_PREFIX}:{token}:{index}")]
        for index, label in enumerate(options)
    ]
    return InlineKeyboardMarkup(rows)


def stop_keyboard(project: str) -> InlineKeyboardMarkup:
    # Digest, not the id: project ids can overflow the 64-byte callback_data.
    data = f"{STOP_PREFIX}:{project_token(project)}"
    return InlineKeyboardMarkup([[InlineKeyboardButton(STOP_BUTTON, callback_data=data)]])


async def stop_turn(bridge: BridgeContext, project: str) -> str:
    """Interrupt the project's running turn; returns the reply for the user.

    Pending approvals are denied first, so their prompts are closed too.
    """
    if not bridge.sessions.is_busy(project):
        return NOTHING_TO_STOP.format(project=project)
    await bridge.broker.cancel(project)
    if not await bridge.sessions.interrupt(project):
        return NOTHING_TO_STOP.format(project=project)
    return STOPPED_REPLY.format(project=project)


def _progress_callback(
    progress: ProgressMessage, verbose: int, written: WrittenFiles
) -> EventCallback:
    async def on_event(event: StreamEvent) -> None:
        written.observe(event)
        line = describe_event(event, verbose)
        if line is not None:
            await progress.add_line(line)

    return on_event


def _failure_text(project: str, exc: Exception) -> str:
    if isinstance(exc, TurnInterruptedError):
        return INTERRUPTED_NOTICE
    if isinstance(exc, ClaudeTimeoutError):
        return f"⏱️ {exc}. Ho chiuso il processo: il prossimo messaggio riprende la sessione."
    if isinstance(exc, ClaudeCrashedError):
        return f"💥 {truncate(str(exc), ERROR_DETAIL_MAX)}"
    if isinstance(exc, SandboxError):
        return f"🚫 Progetto {project} non utilizzabile: {exc}"
    return f"❌ Errore inatteso: {type(exc).__name__}: {truncate(str(exc), ERROR_DETAIL_MAX)}"


def compose_answer(outcome: TurnOutcome) -> Answer:
    """Split Claude's final text into the message, its choice buttons and its attachments."""
    result = outcome.result
    text, files = extract_files(result.text)
    text, options = extract_choices(text)
    if result.is_error:
        # The CLI reports some errors (auth, API) with subtype "success".
        detail = f" ({result.subtype})" if result.subtype != "success" else ""
        text = f"❌ Claude ha segnalato un errore{detail}.\n{text}".rstrip()
    elif not text.strip():
        text = ATTACHMENTS_ONLY if files else EMPTY_ANSWER
    if outcome.fresh_session:
        text = FRESH_SESSION_NOTICE + text
    return Answer(text=text, options=options, files=files)


def _verbosity(bridge: BridgeContext, request: TurnRequest) -> int:
    return bridge.store.get_verbose(request.user_id, default=bridge.settings.verbose_level)


async def run_user_turn(bridge: BridgeContext, bot: Any, request: TurnRequest) -> None:
    """Forward one user message to Claude and relay progress and answer to Telegram.

    This is the Telegram boundary of a turn: every failure, Telegram ones
    included, is logged and audited, and the user gets an explicit message
    whenever Telegram is reachable.
    """
    project = request.project
    bridge.presenter.project_chats[project] = request.chat_id
    bridge.store.record_audit(
        event="prompt",
        project=project,
        detail=truncate(request.text, AUDIT_DETAIL_MAX),
        user_id=request.user_id,
    )
    progress: ProgressMessage | None = None
    try:
        if bridge.sessions.is_busy(project):
            await send_text(bot, request.chat_id, QUEUED_NOTICE)
        progress = await ProgressMessage.create(
            bot, request.chat_id, f"⏳ {project}: sto lavorando…", stop_keyboard(project)
        )
        bridge.forget_choices(project)
        written = WrittenFiles()
        on_event = _progress_callback(progress, _verbosity(bridge, request), written)
        outcome = await bridge.sessions.run_turn(project, request.text, on_event)
    except Exception as exc:  # turn boundary: report every failure to the user
        await _report_failure(bridge, bot, request, progress, exc)
        return
    await _deliver(bridge, bot, request, progress, outcome, written.paths)


async def _report_failure(
    bridge: BridgeContext,
    bot: Any,
    request: TurnRequest,
    progress: ProgressMessage | None,
    exc: Exception,
) -> None:
    stopped = isinstance(exc, TurnInterruptedError)
    if stopped:
        logger.info("Turn stopped by the user for project %s", request.project)
    else:
        logger.exception("Turn failed for project %s", request.project, exc_info=exc)
    event = "turn-stopped" if stopped else "turn-error"
    bridge.store.record_audit(event=event, project=request.project, detail=repr(exc))
    header = "⏹️ {}: fermato" if stopped else "❌ {}: interrotto"
    try:
        if progress is not None:
            await progress.finish(header.format(request.project))
        await send_text(bot, request.chat_id, _failure_text(request.project, exc))
    except Exception:
        logger.exception("Could not report the failure to Telegram either")


async def _deliver(
    bridge: BridgeContext,
    bot: Any,
    request: TurnRequest,
    progress: ProgressMessage,
    outcome: TurnOutcome,
    written: list[str],
) -> None:
    answer = compose_answer(outcome)
    text = answer.text
    if outcome.result.is_error and any(m in text.lower() for m in AUTH_ERROR_MARKERS):
        text += AUTH_HINT.format(profile=bridge.sessions.profile)
    verbose = _verbosity(bridge, request)
    # The final text also streamed as a progress line: keep it only in the answer.
    progress.drop_last(describe_event(TextEvent(text=outcome.result.text), verbose))
    if verbose == 0:
        await progress.delete()
    else:
        status = "⚠️" if outcome.result.is_error else "✅"
        await progress.finish(f"{status} {request.project}: completato")
    options = answer.options
    keyboard = choice_keyboard(bridge, request.project, options) if options else None
    await send_text(bot, request.chat_id, text, reply_markup=keyboard)
    # Duplicates between the two lists are dropped when the paths are resolved.
    files = answer.files + written
    if files:
        await _deliver_files(bridge, bot, request, files)


async def _deliver_files(
    bridge: BridgeContext, bot: Any, request: TurnRequest, raw_paths: list[str]
) -> None:
    cwd = bridge.projects.resolve_project(request.project)
    paths, problems = resolve_attachments(raw_paths, cwd, bridge.projects.sandbox)
    sent, failed = await send_documents(bot, request.chat_id, paths)
    for path in sent:
        bridge.store.record_audit(
            event="file-sent", project=request.project, detail=str(path), user_id=request.user_id
        )
    if problems or failed:
        await send_text(bot, request.chat_id, "\n".join(problems + failed))
