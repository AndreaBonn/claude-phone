import logging
import secrets
from dataclasses import dataclass
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.bridge_context import BridgeContext
from src.claude_session import ClaudeCrashedError, ClaudeTimeoutError, EventCallback
from src.message_formatter import describe_event, extract_choices, truncate
from src.project_manager import SandboxError
from src.session_manager import TurnOutcome
from src.stream_parser import StreamEvent
from src.telegram_io import ProgressMessage, send_text

logger = logging.getLogger(__name__)

CHOICE_PREFIX = "ch"
AUDIT_DETAIL_MAX = 300
ERROR_DETAIL_MAX = 1500
QUEUED_NOTICE = "📥 Messaggio in coda: Claude sta ancora lavorando sul precedente."
FRESH_SESSION_NOTICE = "🔁 La sessione salvata non esiste più: ne ho aperta una nuova.\n\n"
EMPTY_ANSWER = "(Claude ha chiuso il turno senza testo: se hai negato un'azione è normale.)"


@dataclass(frozen=True)
class TurnRequest:
    chat_id: int
    user_id: int
    project: str
    text: str


def choice_keyboard(bridge: BridgeContext, options: list[str]) -> InlineKeyboardMarkup:
    token = secrets.token_hex(4)
    bridge.choices[token] = options
    rows = [
        [InlineKeyboardButton(label, callback_data=f"{CHOICE_PREFIX}:{token}:{index}")]
        for index, label in enumerate(options)
    ]
    return InlineKeyboardMarkup(rows)


def _progress_callback(progress: ProgressMessage, verbose: int) -> EventCallback:
    async def on_event(event: StreamEvent) -> None:
        line = describe_event(event, verbose)
        if line is not None:
            await progress.add_line(line)

    return on_event


def _failure_text(project: str, exc: Exception) -> str:
    if isinstance(exc, ClaudeTimeoutError):
        return f"⏱️ {exc}. Ho chiuso il processo: il prossimo messaggio riprende la sessione."
    if isinstance(exc, ClaudeCrashedError):
        return f"💥 {truncate(str(exc), ERROR_DETAIL_MAX)}"
    if isinstance(exc, SandboxError):
        return f"🚫 Progetto {project} non utilizzabile: {exc}"
    return f"❌ Errore inatteso: {type(exc).__name__}: {truncate(str(exc), ERROR_DETAIL_MAX)}"


def _answer_text(outcome: TurnOutcome) -> tuple[str, list[str]]:
    result = outcome.result
    text, options = extract_choices(result.text)
    if result.is_error:
        text = f"❌ Claude ha segnalato un errore ({result.subtype}).\n{text}".rstrip()
    elif not text.strip():
        text = EMPTY_ANSWER
    if outcome.fresh_session:
        text = FRESH_SESSION_NOTICE + text
    return text, options


async def run_user_turn(bridge: BridgeContext, bot: Any, request: TurnRequest) -> None:
    """Forward one user message to Claude and relay progress and answer to Telegram.

    This is the Telegram boundary of a turn: every failure ends with an explicit
    message to the user, never with a progress message stuck on 'working'.
    """
    project, chat_id = request.project, request.chat_id
    verbose = bridge.store.get_verbose(request.user_id, default=bridge.settings.verbose_level)
    if bridge.sessions.is_busy(project):
        await send_text(bot, chat_id, QUEUED_NOTICE)
    bridge.presenter.project_chats[project] = chat_id
    bridge.store.record_audit(
        event="prompt",
        project=project,
        detail=truncate(request.text, AUDIT_DETAIL_MAX),
        user_id=request.user_id,
    )
    progress = await ProgressMessage.create(bot, chat_id, f"⏳ {project}: sto lavorando…")
    try:
        bridge.choices.clear()
        outcome = await bridge.sessions.run_turn(
            project, request.text, _progress_callback(progress, verbose)
        )
    except Exception as exc:  # turn boundary: report every failure to the user
        logger.exception("Turn failed for project %s", project)
        bridge.store.record_audit(event="turn-error", project=project, detail=repr(exc))
        await progress.finish(f"❌ {project}: interrotto")
        await send_text(bot, chat_id, _failure_text(project, exc))
        return
    await _deliver(bridge, bot, request, progress, outcome, verbose)


async def _deliver(
    bridge: BridgeContext,
    bot: Any,
    request: TurnRequest,
    progress: ProgressMessage,
    outcome: TurnOutcome,
    verbose: int,
) -> None:
    text, options = _answer_text(outcome)
    if verbose == 0:
        await progress.delete()
    else:
        status = "⚠️" if outcome.result.is_error else "✅"
        await progress.finish(f"{status} {request.project}: completato")
    keyboard = choice_keyboard(bridge, options) if options else None
    await send_text(bot, request.chat_id, text, reply_markup=keyboard)
