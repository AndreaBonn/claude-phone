from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.bridge_context import BridgeContext

PROJECT_PREFIX = "pj"
# Telegram limits callback_data to 64 bytes.
CALLBACK_DATA_MAX_BYTES = 64


def project_keyboard(projects: list[str], active: str | None) -> InlineKeyboardMarkup | None:
    """One button per project; names too long for callback_data need /switch."""
    rows = []
    for name in projects:
        data = f"{PROJECT_PREFIX}:{name}"
        if len(data.encode()) <= CALLBACK_DATA_MAX_BYTES:
            label = f"▶️ {name}" if name == active else name
            rows.append([InlineKeyboardButton(label, callback_data=data)])
    return InlineKeyboardMarkup(rows) if rows else None


def projects_text(bridge: BridgeContext, active: str | None) -> str:
    projects = bridge.projects.list_projects()
    if not projects:
        return f"Nessun progetto in {bridge.projects.root}. Crea una sotto-directory e riprova."
    lines = [f"{'▶️' if name == active else '•'} {name}" for name in projects]
    return "📁 Progetti disponibili:\n" + "\n".join(lines)


async def switch_project(bridge: BridgeContext, user_id: int, name: str) -> str:
    """Make `name` the user's active project; returns the reply for the user.

    Raises
    ------
    SandboxError
        If `name` is not a valid project inside the sandbox.
    """
    bridge.projects.resolve_project(name)
    current = bridge.store.get_active_project(user_id)
    if current is not None and bridge.sessions.is_busy(current):
        return f"⏳ Claude sta lavorando su {current}: attendi la fine del turno."
    if current is not None and current != name:
        # Only one live claude process at a time; the old session stays resumable.
        await bridge.sessions.discard(current)
    bridge.store.set_active_project(user_id, name)
    session_id = bridge.sessions.session_id(name)
    if session_id:
        return f"📁 Progetto attivo: {name}\n🔄 Riprendo la sessione {session_id[:8]}…"
    return f"📁 Progetto attivo: {name}\n🆕 Nuova sessione al primo messaggio."
