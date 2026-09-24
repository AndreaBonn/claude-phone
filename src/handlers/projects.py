import hashlib

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.bridge_context import BridgeContext

PROJECT_PREFIX = "pj"
# Project ids like "ProgettiPersonali/<repo>" overflow Telegram's 64-byte
# callback_data, so buttons carry a short digest resolved against the list.
PROJECT_TOKEN_LENGTH = 16
PAGE_PREFIX = "pg"
PAGE_SIZE = 20


def project_token(project_id: str) -> str:
    return hashlib.sha256(project_id.encode()).hexdigest()[:PROJECT_TOKEN_LENGTH]


def find_project(bridge: BridgeContext, token: str) -> str | None:
    return next((p for p in bridge.projects.list_projects() if project_token(p) == token), None)


def _page_count(projects: list[str]) -> int:
    return max(1, -(-len(projects) // PAGE_SIZE))


def _nav_row(page: int, pages: int) -> list[InlineKeyboardButton]:
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️", callback_data=f"{PAGE_PREFIX}:{page - 1}"))
    if page < pages - 1:
        row.append(InlineKeyboardButton("➡️", callback_data=f"{PAGE_PREFIX}:{page + 1}"))
    return row


def project_keyboard(
    projects: list[str], active: str | None, page: int = 0
) -> InlineKeyboardMarkup | None:
    """One page of project buttons plus navigation arrows.

    Telegram caps inline keyboards at about a hundred buttons, fewer than the
    projects of a few real roots, hence the pagination.
    """
    if not projects:
        return None
    pages = _page_count(projects)
    page = min(max(page, 0), pages - 1)
    rows = [
        [
            InlineKeyboardButton(
                f"▶️ {name}" if name == active else name,
                callback_data=f"{PROJECT_PREFIX}:{project_token(name)}",
            )
        ]
        for name in projects[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    ]
    nav = _nav_row(page, pages)
    return InlineKeyboardMarkup([*rows, nav] if nav else rows)


def projects_text(bridge: BridgeContext, active: str | None, page: int = 0) -> str:
    projects = bridge.projects.list_projects()
    roots = bridge.projects.sandbox.roots
    if not projects:
        names = ", ".join(str(root) for root in roots)
        return f"Nessun progetto in {names}. Crea una sotto-directory e riprova."
    pages = _page_count(projects)
    page = min(max(page, 0), pages - 1)
    return (
        f"📁 {len(projects)} progetti in {len(roots)} radici, pagina {page + 1}/{pages}.\n"
        f"Attivo: {active or 'nessuno'}. Tocca un progetto o usa /switch <radice>/<nome>."
    )


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
