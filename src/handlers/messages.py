from telegram import Update
from telegram.ext import ContextTypes

from src.bridge_context import get_bridge
from src.handlers.projects import project_keyboard
from src.telegram_io import send_text
from src.turn_runner import TurnRequest, run_user_turn

NO_PROJECT = "📁 Nessun progetto attivo: scegline uno, poi rimanda il messaggio."


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
