import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from src.bridge_context import BridgeContext, get_bridge
from src.profiles import DEFAULT_PROFILE, ProfileError
from src.telegram_io import send_text

logger = logging.getLogger(__name__)

PROFILE_PREFIX = "pf"
BUSY_REPLY = "⏳ Claude sta lavorando: attendi la fine del turno prima di cambiare profilo."


def profile_keyboard(profiles: list[str], active: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"▶️ {name}" if name == active else name,
                    callback_data=f"{PROFILE_PREFIX}:{name}",
                )
            ]
            for name in profiles
        ]
    )


def profiles_text(bridge: BridgeContext) -> str:
    return (
        f"👤 Profilo Claude attivo: {bridge.sessions.profile}\n"
        f"Profili in {bridge.profiles.profiles_dir} (default = ~/.claude)."
    )


async def select_profile(bridge: BridgeContext, user_id: int, name: str) -> str:
    """Switch the Claude profile and remember it; returns the reply for the user."""
    try:
        config_dir = bridge.profiles.config_dir(name)
    except ProfileError as exc:
        return f"🚫 {exc}"
    if not await bridge.sessions.set_profile(name, config_dir):
        return BUSY_REPLY
    # Grants belong to the sessions just closed, which are per profile.
    bridge.broker.revoke_grants(None)
    bridge.store.set_profile(user_id, name)
    return (
        f"👤 Profilo Claude attivo: {name}\nOgni progetto riprende la sessione di questo profilo."
    )


async def apply_initial_profile(bridge: BridgeContext) -> None:
    """At startup: last profile chosen on Telegram, else CLAUDE_PROFILE, else default."""
    owner = min(bridge.settings.allowed_users)
    name = bridge.store.get_profile(owner) or bridge.settings.claude_profile or DEFAULT_PROFILE
    try:
        config_dir = bridge.profiles.config_dir(name)
    except ProfileError:
        logger.warning("Claude profile %r not found, using the default one", name)
        name, config_dir = DEFAULT_PROFILE, None
    await bridge.sessions.set_profile(name, config_dir)
    logger.info("Claude profile: %s", name)


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/profile [name] — show the profiles as buttons, or switch directly."""
    bridge = get_bridge(context)
    assert update.effective_user is not None and update.effective_chat is not None
    chat_id = update.effective_chat.id
    if context.args:
        reply = await select_profile(bridge, update.effective_user.id, context.args[0])
        await send_text(context.bot, chat_id, reply)
        return
    keyboard = profile_keyboard(bridge.profiles.list_profiles(), bridge.sessions.profile)
    await send_text(context.bot, chat_id, profiles_text(bridge), reply_markup=keyboard)
