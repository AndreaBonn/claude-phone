from types import SimpleNamespace
from typing import cast

import pytest
from telegram import Update
from telegram.ext import ApplicationHandlerStop

from src.auth import build_auth_guard, is_authorized

ALLOWED = frozenset({42})


def fake_update(user_id: int | None) -> Update:
    user = None if user_id is None else SimpleNamespace(id=user_id)
    return cast(Update, SimpleNamespace(effective_user=user, update_id=1))


def test_is_authorized_accepts_whitelisted_user() -> None:
    assert is_authorized(user_id=42, allowed=ALLOWED) is True


def test_is_authorized_rejects_other_user() -> None:
    assert is_authorized(user_id=7, allowed=ALLOWED) is False


def test_is_authorized_rejects_missing_user() -> None:
    assert is_authorized(user_id=None, allowed=ALLOWED) is False


async def test_auth_guard_stops_unknown_user() -> None:
    guard = build_auth_guard(ALLOWED)
    with pytest.raises(ApplicationHandlerStop):
        await guard(fake_update(7), None)


async def test_auth_guard_stops_update_without_user() -> None:
    guard = build_auth_guard(ALLOWED)
    with pytest.raises(ApplicationHandlerStop):
        await guard(fake_update(None), None)


async def test_auth_guard_lets_whitelisted_user_through() -> None:
    guard = build_auth_guard(ALLOWED)
    await guard(fake_update(42), None)
