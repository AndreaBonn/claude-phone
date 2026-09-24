import logging
from collections.abc import Callable, Coroutine
from typing import Any

from telegram import Update
from telegram.ext import ApplicationHandlerStop

logger = logging.getLogger(__name__)

AuthGuard = Callable[[Update, Any], Coroutine[Any, Any, None]]


def is_authorized(user_id: int | None, allowed: frozenset[int]) -> bool:
    return user_id is not None and user_id in allowed


def build_auth_guard(allowed: frozenset[int]) -> AuthGuard:
    """Build a handler that drops every update not coming from a whitelisted user.

    Registered in the lowest handler group, so it runs before any other handler
    on every single update, not only on the first command.
    """

    async def guard(update: Update, _context: Any) -> None:
        user = update.effective_user
        user_id = user.id if user is not None else None
        if not is_authorized(user_id=user_id, allowed=allowed):
            logger.warning("Rejected update %s from user %s", update.update_id, user_id)
            raise ApplicationHandlerStop

    return guard
