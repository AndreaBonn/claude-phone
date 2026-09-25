from telegram import Update
from telegram.ext import ContextTypes

from src.bridge_context import get_bridge
from src.handlers.projects import project_keyboard, projects_text, switch_project
from src.project_manager import SandboxError
from src.telegram_io import send_text
from src.turn_runner import stop_turn

VERBOSE_HELP = "Uso: /verbose 0|1|2 (0 solo risposta, 1 tool in tempo reale, 2 tool e input)"
VERBOSE_LEVELS = ("0", "1", "2")
WELCOME = (
    "🤖 Bridge Claude Code attivo.\n"
    "Scegli un progetto, poi scrivimi normalmente: inoltro tutto a Claude Code.\n"
    "Comandi: /projects /switch <nome> /stop /new /clear /status /verbose <0|1|2> /profile"
)
UNKNOWN_COMMAND = "Comando sconosciuto, per una nuova sessione usa /new o /clear."


def _ids(update: Update) -> tuple[int, int]:
    assert update.effective_user is not None and update.effective_chat is not None
    return update.effective_user.id, update.effective_chat.id


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bridge = get_bridge(context)
    user_id, chat_id = _ids(update)
    active = bridge.store.get_active_project(user_id)
    keyboard = project_keyboard(bridge.projects.list_projects(), active)
    text = f"{WELCOME}\n\n{projects_text(bridge, active)}"
    await send_text(context.bot, chat_id, text, reply_markup=keyboard)


async def projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bridge = get_bridge(context)
    user_id, chat_id = _ids(update)
    active = bridge.store.get_active_project(user_id)
    keyboard = project_keyboard(bridge.projects.list_projects(), active)
    await send_text(context.bot, chat_id, projects_text(bridge, active), reply_markup=keyboard)


async def switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bridge = get_bridge(context)
    user_id, chat_id = _ids(update)
    if not context.args:
        await send_text(context.bot, chat_id, "Uso: /switch <radice>/<progetto>, vedi /projects")
        return
    try:
        reply = await switch_project(bridge, user_id, " ".join(context.args))
    except SandboxError as exc:
        reply = f"🚫 {exc}"
    await send_text(context.bot, chat_id, reply)


async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bridge = get_bridge(context)
    user_id, chat_id = _ids(update)
    project = bridge.store.get_active_project(user_id)
    if project is None:
        reply = "Nessun progetto attivo: usa /projects."
    elif bridge.sessions.is_busy(project):
        reply = f"⏳ Claude sta lavorando su {project}: attendi la fine del turno."
    else:
        await bridge.broker.cancel(project)
        bridge.broker.revoke_grants(project)
        await bridge.sessions.reset(project)
        reply = f"🆕 Sessione azzerata per {project}: il prossimo messaggio ne apre una nuova."
    await send_text(context.bot, chat_id, reply)


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stop — interrupt Claude's running turn in the active project."""
    bridge = get_bridge(context)
    user_id, chat_id = _ids(update)
    project = bridge.store.get_active_project(user_id)
    if project is None:
        reply = "Nessun progetto attivo: usa /projects."
    else:
        reply = await stop_turn(bridge, project)
    await send_text(context.bot, chat_id, reply)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answer commands the bridge does not know instead of dropping them silently."""
    _, chat_id = _ids(update)
    await send_text(context.bot, chat_id, UNKNOWN_COMMAND)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bridge = get_bridge(context)
    user_id, chat_id = _ids(update)
    project = bridge.store.get_active_project(user_id)
    verbose = bridge.store.get_verbose(user_id, default=bridge.settings.verbose_level)
    lines = [f"📁 Progetto: {project or 'nessuno'}"]
    if project is not None:
        state = "al lavoro" if bridge.sessions.is_busy(project) else "in attesa"
        running = "attivo" if bridge.sessions.is_running(project) else "spento"
        grants = ", ".join(bridge.broker.grants(project)) or "nessuno"
        lines += [
            f"🧵 Sessione: {bridge.sessions.session_id(project) or 'nuova'}",
            f"⚙️ Processo Claude: {running}, {state}",
            f"🔁 Approvati per la sessione: {grants}",
        ]
    lines += [
        f"👤 Profilo Claude: {bridge.sessions.profile}",
        f"🔊 Verbosità: {verbose}",
        f"🔐 Approvazioni pendenti: {len(bridge.broker.pending())}",
        "📦 Sandbox: " + ", ".join(str(root) for root in bridge.settings.approved_directory),
    ]
    await send_text(context.bot, chat_id, "\n".join(lines))


async def verbose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bridge = get_bridge(context)
    user_id, chat_id = _ids(update)
    if not context.args or context.args[0] not in VERBOSE_LEVELS:
        current = bridge.store.get_verbose(user_id, default=bridge.settings.verbose_level)
        await send_text(context.bot, chat_id, f"Verbosità attuale: {current}\n{VERBOSE_HELP}")
        return
    bridge.store.set_verbose(user_id, int(context.args[0]))
    await send_text(context.bot, chat_id, f"🔊 Verbosità impostata a {context.args[0]}")
