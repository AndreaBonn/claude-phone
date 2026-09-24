import logging
import secrets
from dataclasses import dataclass
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.bridge_context import BridgeContext, ChoiceSet
from src.claude_session import ClaudeCrashedError, ClaudeTimeoutError, EventCallback
from src.message_formatter import describe_event, extract_choices, truncate
from src.project_manager import SandboxError
from src.session_manager import TurnOutcome
from src.stream_parser import StreamEvent, TextEvent
from src.telegram_io import ProgressMessage, send_text

logger = logging.getLogger(__name__)

CHOICE_PREFIX = "ch"
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
EMPTY_ANSWER = "(Claude ha chiuso il turno senza testo: se hai negato un'azione è normale.)"


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
        # The CLI reports some errors (auth, API) with subtype "success".
        detail = f" ({result.subtype})" if result.subtype != "success" else ""
        text = f"❌ Claude ha segnalato un errore{detail}.\n{text}".rstrip()
    elif not text.strip():
        text = EMPTY_ANSWER
    if outcome.fresh_session:
        text = FRESH_SESSION_NOTICE + text
    return text, options


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
            bot, request.chat_id, f"⏳ {project}: sto lavorando…"
        )
        bridge.forget_choices(project)
        on_event = _progress_callback(progress, _verbosity(bridge, request))
        outcome = await bridge.sessions.run_turn(project, request.text, on_event)
    except Exception as exc:  # turn boundary: report every failure to the user
        await _report_failure(bridge, bot, request, progress, exc)
        return
    await _deliver(bridge, bot, request, progress, outcome)


async def _report_failure(
    bridge: BridgeContext,
    bot: Any,
    request: TurnRequest,
    progress: ProgressMessage | None,
    exc: Exception,
) -> None:
    logger.exception("Turn failed for project %s", request.project, exc_info=exc)
    bridge.store.record_audit(event="turn-error", project=request.project, detail=repr(exc))
    try:
        if progress is not None:
            await progress.finish(f"❌ {request.project}: interrotto")
        await send_text(bot, request.chat_id, _failure_text(request.project, exc))
    except Exception:
        logger.exception("Could not report the failure to Telegram either")


async def _deliver(
    bridge: BridgeContext,
    bot: Any,
    request: TurnRequest,
    progress: ProgressMessage,
    outcome: TurnOutcome,
) -> None:
    text, options = _answer_text(outcome)
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
    keyboard = choice_keyboard(bridge, request.project, options) if options else None
    await send_text(bot, request.chat_id, text, reply_markup=keyboard)
