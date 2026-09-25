from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from src.bridge_context import BridgeContext, get_bridge
from src.file_intake import (
    Attachment,
    AttachmentError,
    describe_attachment,
    receive_attachment,
)
from src.handlers.projects import project_keyboard
from src.project_manager import SandboxError
from src.telegram_io import send_text
from src.turn_runner import TurnRequest, run_user_turn

NO_PROJECT = "📁 Nessun progetto attivo: scegline uno, poi rimanda il messaggio."
SAVED = "📎 Salvato in `{path}`"
# Opens the turn a captioned upload starts; the caption follows on its own lines.
ANNOUNCE = "📎 File ricevuto: {path}"


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Free text goes straight to Claude Code in the active project."""
    bridge = get_bridge(context)
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or not message.text:
        return
    project = bridge.store.get_active_project(user.id)
    if project is None:
        keyboard = project_keyboard(bridge.projects.list_projects(), active=None)
        await send_text(context.bot, message.chat_id, NO_PROJECT, reply_markup=keyboard)
        return
    request = TurnRequest(
        chat_id=message.chat_id, user_id=user.id, project=project, text=message.text
    )
    await run_user_turn(bridge, context.bot, request)


async def handle_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A document or photo is saved in the active project's uploads directory.

    A caption is an instruction about the file, so it starts a turn that names
    the saved path; without one the file is only saved.
    """
    bridge = get_bridge(context)
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    attachment = describe_attachment(message)
    if attachment is None:
        return
    project = bridge.store.get_active_project(user.id)
    if project is None:
        keyboard = project_keyboard(bridge.projects.list_projects(), active=None)
        await send_text(context.bot, message.chat_id, NO_PROJECT, reply_markup=keyboard)
        return
    try:
        relative = await _store(
            bridge=bridge, bot=context.bot, project=project, attachment=attachment
        )
    except AttachmentError as exc:
        await send_text(context.bot, message.chat_id, exc.notice)
        return
    await send_text(context.bot, message.chat_id, SAVED.format(path=relative))
    caption = (message.caption or "").strip()
    if caption:
        text = f"{ANNOUNCE.format(path=relative)}\n\n{caption}"
        request = TurnRequest(chat_id=message.chat_id, user_id=user.id, project=project, text=text)
        await run_user_turn(bridge, context.bot, request)


async def _store(bridge: BridgeContext, bot: Any, project: str, attachment: Attachment) -> str:
    """Save the attachment; its path relative to the project, or AttachmentError."""
    try:
        # Resolved again now: the project may have been removed or relinked since it was chosen.
        directory = bridge.projects.resolve_project(project)
    except SandboxError as exc:
        raise AttachmentError(str(exc)) from exc
    saved = await receive_attachment(
        bot=bot, attachment=attachment, directory=directory, sandbox=bridge.projects.sandbox
    )
    return str(saved.relative_to(directory.resolve()))

