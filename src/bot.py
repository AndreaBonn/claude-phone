import fcntl
import logging
import os
import shlex
import sys
from typing import Any, TextIO

from pydantic import ValidationError
from telegram import BotCommand, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from src.auth import build_auth_guard
from src.bridge_context import BRIDGE_KEY, BridgeContext
from src.claude_session import SYSTEM_PROMPT_PATH, SessionConfig
from src.config import PROJECT_ROOT, Settings, format_config_error
from src.handlers import callbacks, commands, messages, profile
from src.logging_setup import setup_logging
from src.message_formatter import summarize_tool_input, truncate
from src.permission_gate import ApprovalBroker
from src.permission_policy import GatePolicy
from src.profiles import ProfileCatalog
from src.project_manager import ProjectManager, Sandbox
from src.session_manager import SessionManager
from src.session_store import SessionStore
from src.telegram_io import send_text
from src.telegram_presenter import APPROVAL_PREFIX, TelegramApprovalPresenter
from src.turn_runner import AUDIT_DETAIL_MAX, CHOICE_PREFIX

logger = logging.getLogger(__name__)

LOCK_PATH = PROJECT_ROOT / "data" / "bot.lock"
PRIVATE_UMASK = 0o077
ONLINE_MESSAGE = "🟢 Bot online: Claude Code è raggiungibile. /status per lo stato."
OFFLINE_MESSAGE = "🔴 Bot disattivato."
READY_BANNER = "Bot attivo, in ascolto"
BOT_COMMANDS = [
    BotCommand("start", "Benvenuto e lista progetti"),
    BotCommand("projects", "Progetti disponibili"),
    BotCommand("switch", "Cambia progetto: /switch <nome>"),
    BotCommand("new", "Nuova sessione per il progetto attivo"),
    BotCommand("status", "Stato di progetto, sessione e approvazioni"),
    BotCommand("verbose", "Dettaglio del progresso: /verbose 0|1|2"),
    BotCommand("profile", "Profilo Claude (cloak) da usare"),
]


def acquire_instance_lock() -> TextIO:
    """Refuse to start twice: two pollers on one token would steal each other's updates."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Il bot è già in esecuzione (lock su data/bot.lock).") from None
    return handle


def _session_config(settings: Settings) -> SessionConfig:
    api_key = settings.anthropic_api_key
    return SessionConfig(
        claude_command=tuple(shlex.split(settings.claude_bin)),
        allowed_tools=settings.claude_allowed_tools,
        gate_socket=settings.gate_socket_path,
        approval_timeout=settings.approval_timeout_seconds,
        idle_timeout=settings.claude_timeout_seconds,
        system_prompt=SYSTEM_PROMPT_PATH.read_text(encoding="utf-8"),
        api_key=api_key.get_secret_value() if api_key else None,
    )


def _sandbox(settings: Settings) -> Sandbox:
    # The bridge may live inside a root: it is carved out, so Claude can never
    # read or rewrite its own permission gate.
    # Skills and rules read their own files at runtime: that part of Claude's
    # config is readable, never writable.
    read_only = ProfileCatalog(settings.claude_profiles_dir).readonly_config_dirs()
    return Sandbox(roots=settings.approved_directory, excluded=(PROJECT_ROOT,), read_only=read_only)


def _gate_policy(settings: Settings) -> GatePolicy:
    return GatePolicy(
        sandbox=_sandbox(settings),
        allowed_tools=frozenset(settings.claude_allowed_tools),
        auto_approve_tools=frozenset(settings.claude_auto_approve_tools),
    )


def build_bridge(settings: Settings, bot: Any) -> BridgeContext:
    store = SessionStore(settings.db_path)
    projects = ProjectManager(_sandbox(settings))
    presenter = TelegramApprovalPresenter(bot, store, default_chat_id=min(settings.allowed_users))
    # The session manager and the broker reference each other: late-bound via a list.
    broker_ref: list[ApprovalBroker] = []
    sessions = SessionManager(
        config=_session_config(settings),
        store=store,
        projects=projects,
        is_waiting_for_user=lambda project: bool(broker_ref and broker_ref[0].pending(project)),
    )

    def audit(
        project: str, tool: str, tool_input: dict[str, Any], outcome: str, reason: str
    ) -> None:
        detail = truncate(f"{tool}: {summarize_tool_input(tool, tool_input)}", AUDIT_DETAIL_MAX)
        store.record_audit("tool", project, detail, outcome, reason)

    broker = ApprovalBroker(
        _gate_policy(settings),
        presenter,
        settings.approval_timeout_seconds,
        audit,
        sessions.request_stop,
    )
    broker_ref.append(broker)
    profiles = ProfileCatalog(settings.claude_profiles_dir)
    return BridgeContext(settings, store, projects, sessions, broker, presenter, profiles)


AnyApplication = Application[Any, Any, Any, Any, Any, Any]


def bridge_of(app: AnyApplication) -> BridgeContext:
    bridge: BridgeContext = app.bot_data[BRIDGE_KEY]
    return bridge


async def _broadcast(app: AnyApplication, text: str) -> None:
    for user_id in sorted(bridge_of(app).settings.allowed_users):
        try:
            await send_text(app.bot, user_id, text)
        except TelegramError:
            logger.warning("Could not notify user %s (has it started the bot?)", user_id)


async def on_startup(app: AnyApplication) -> None:
    bridge = bridge_of(app)
    await profile.apply_initial_profile(bridge)
    await bridge.broker.start(bridge.settings.gate_socket_path)
    await app.bot.set_my_commands(BOT_COMMANDS)
    cancelled = await bridge.presenter.cancel_leftovers()
    if cancelled:
        logger.warning("Cancelled %d approvals left open by the previous run", cancelled)
    await _broadcast(app, ONLINE_MESSAGE)
    logger.info(READY_BANNER)


async def on_stop(app: AnyApplication) -> None:
    bridge = bridge_of(app)
    await bridge.broker.stop()
    await bridge.sessions.stop_all()
    await _broadcast(app, OFFLINE_MESSAGE)


async def on_shutdown(app: AnyApplication) -> None:
    bridge_of(app).store.close()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled error while processing %s", update, exc_info=context.error)


def register_handlers(app: AnyApplication, allowed_users: frozenset[int]) -> None:
    # Group -1 runs first on every update: unknown users never reach a handler.
    app.add_handler(TypeHandler(Update, build_auth_guard(allowed_users)), group=-1)
    app.add_handler(CommandHandler("start", commands.start))
    app.add_handler(CommandHandler("projects", commands.projects))
    app.add_handler(CommandHandler("switch", commands.switch))
    app.add_handler(CommandHandler("new", commands.new_session))
    app.add_handler(CommandHandler("status", commands.status))
    app.add_handler(CommandHandler("verbose", commands.verbose))
    app.add_handler(CommandHandler("profile", profile.profile))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, messages.handle_text
        )
    )
    app.add_handler(CallbackQueryHandler(callbacks.handle_approval, pattern=f"^{APPROVAL_PREFIX}:"))
    app.add_handler(CallbackQueryHandler(callbacks.handle_choice, pattern=f"^{CHOICE_PREFIX}:"))
    app.add_handler(CallbackQueryHandler(callbacks.handle_project_pick, pattern=r"^pj:"))
    app.add_handler(CallbackQueryHandler(callbacks.handle_project_page, pattern=r"^pg:"))
    app.add_handler(CallbackQueryHandler(callbacks.handle_profile_pick, pattern=r"^pf:"))
    app.add_error_handler(on_error)


def build_application(settings: Settings) -> AnyApplication:
    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token.get_secret_value())
        # Approval buttons must be handled while a message handler awaits Claude.
        .concurrent_updates(True)
        .post_init(on_startup)
        .post_stop(on_stop)
        .post_shutdown(on_shutdown)
        .build()
    )
    app.bot_data[BRIDGE_KEY] = build_bridge(settings, app.bot)
    register_handlers(app, settings.allowed_users)
    return app


def main() -> int:
    # Everything the bot creates (database, logs, socket, lock) is owner-only;
    # it also closes the window between binding the gate socket and its chmod.
    os.umask(PRIVATE_UMASK)
    try:
        settings = Settings()
    except ValidationError as exc:
        print(f"Configurazione non valida (.env):\n{format_config_error(exc)}", file=sys.stderr)
        return 2
    secrets = [settings.telegram_bot_token.get_secret_value()]
    if settings.anthropic_api_key:
        secrets.append(settings.anthropic_api_key.get_secret_value())
    setup_logging(settings.log_level, secrets)
    lock = acquire_instance_lock()
    try:
        app = build_application(settings)
        # Messages sent while the bot was offline are dropped, never executed late.
        app.run_polling(
            drop_pending_updates=True, allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY]
        )
    except Exception:
        # Logged through the redacting formatter: a raw traceback on stderr
        # would print the bot token contained in InvalidToken.
        logger.exception("Bot stopped because of an unrecoverable error")
        return 1
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
