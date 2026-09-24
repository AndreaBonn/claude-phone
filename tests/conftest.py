from collections.abc import Iterator
from typing import Any

import pytest

from src.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep variables exported in the developer's shell out of every test."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    yield


def make_isolated_settings(values: dict[str, Any]) -> Settings:
    """Settings from `values` only: the real .env (bot token included) is never read."""
    return Settings(_env_file=None, **values)
