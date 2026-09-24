import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from src.bot import build_bridge
from src.bridge_context import BridgeContext
from src.config import Settings
from tests.fakes import FakeBot


@pytest.fixture(autouse=True)
def isolated_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep variables exported in the developer's shell out of every test."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    yield


def make_isolated_settings(values: dict[str, Any]) -> Settings:
    """Settings from `values` only: the real .env (bot token included) is never read."""
    return Settings(_env_file=None, **values)


FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"
USER = 42
ALPHA = "sandbox/alpha"
BETA = "sandbox/beta"


@pytest.fixture
def bot() -> FakeBot:
    return FakeBot()


@pytest.fixture
def bridge(
    tmp_path: Path, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
) -> Iterator[BridgeContext]:
    monkeypatch.setenv("FAKE_SCENARIO", "echo")
    (tmp_path / "sandbox" / "alpha").mkdir(parents=True)
    (tmp_path / "sandbox" / "beta").mkdir()
    (tmp_path / "profiles" / "sales").mkdir(parents=True)
    settings = make_isolated_settings(
        {
            "telegram_bot_token": "1:x",
            "allowed_users": str(USER),
            "approved_directory": str(tmp_path / "sandbox"),
            "claude_bin": f"{sys.executable} {FAKE_CLAUDE}",
            "db_path": str(tmp_path / "bridge.db"),
            "gate_socket_path": str(tmp_path / "g.sock"),
            "approval_timeout_seconds": 5,
            "claude_profiles_dir": str(tmp_path / "profiles"),
        }
    )
    bridge = build_bridge(settings, bot)
    bridge.store.set_active_project(USER, ALPHA)
    yield bridge
    bridge.store.close()
