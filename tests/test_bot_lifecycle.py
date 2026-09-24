import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from telegram.error import InvalidToken

from src import bot as bot_module
from src.bot import OFFLINE_MESSAGE, ONLINE_MESSAGE, acquire_instance_lock
from src.bridge_context import BRIDGE_KEY, BridgeContext
from tests.conftest import USER, make_isolated_settings
from tests.fakes import FakeBot

SECRET_TOKEN = "123456:VERYSECRETTOKEN"


def fake_app(bridge: BridgeContext, bot: FakeBot) -> Any:
    return cast(Any, SimpleNamespace(bot_data={BRIDGE_KEY: bridge}, bot=bot))


def test_second_instance_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot_module, "LOCK_PATH", tmp_path / "data" / "bot.lock")
    first = acquire_instance_lock()
    with pytest.raises(SystemExit, match="già in esecuzione"):
        acquire_instance_lock()
    first.close()
    acquire_instance_lock().close()


async def test_startup_opens_the_gate_and_announces_itself(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    await bot_module.on_startup(fake_app(bridge, bot))
    try:
        assert bridge.settings.gate_socket_path.is_socket()
        assert bot.commands == bot_module.BOT_COMMANDS
        assert bot.messages[-1].text == ONLINE_MESSAGE
    finally:
        await bridge.broker.stop()


async def test_startup_cancels_approvals_left_by_the_previous_run(
    bridge: BridgeContext, bot: FakeBot
) -> None:
    prompt = await bot.send_message(chat_id=USER, text="🔐 old prompt")
    bridge.store.add_pending_approval("old", "sandbox/alpha", USER, prompt.message_id)
    await bot_module.on_startup(fake_app(bridge, bot))
    await bridge.broker.stop()
    assert prompt.text.startswith("⚠️ Richiesta annullata")


async def test_stop_closes_gate_and_says_goodbye(bridge: BridgeContext, bot: FakeBot) -> None:
    app = fake_app(bridge, bot)
    await bot_module.on_startup(app)
    await bot_module.on_stop(app)
    assert not bridge.settings.gate_socket_path.exists()
    assert bot.messages[-1].text == OFFLINE_MESSAGE


async def test_broadcast_survives_a_user_who_never_started_the_bot(
    bridge: BridgeContext, bot: FakeBot, caplog: pytest.LogCaptureFixture
) -> None:
    bot.blocked_chats.add(USER)
    await bot_module.on_startup(fake_app(bridge, bot))
    await bridge.broker.stop()
    assert bot.messages == []
    assert "Could not notify user 42" in caplog.text


async def test_shutdown_closes_the_database(bridge: BridgeContext, bot: FakeBot) -> None:
    await bot_module.on_shutdown(fake_app(bridge, bot))
    with pytest.raises(sqlite3.ProgrammingError):
        bridge.store.get_session("x")


async def test_on_error_logs_the_exception(caplog: pytest.LogCaptureFixture) -> None:
    context = cast(Any, SimpleNamespace(error=RuntimeError("boom")))
    with caplog.at_level(logging.ERROR):
        await bot_module.on_error("update-1", context)
    assert "Unhandled error while processing update-1" in caplog.text
    assert caplog.records[-1].exc_info is not None


@pytest.fixture
def isolated_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run main() against temporary files: no real .env, logs, lock or umask change."""
    real_setup_logging = bot_module.setup_logging
    log_file = tmp_path / "bridge.log"
    umasks: list[int] = []

    def record_umask(mask: int) -> int:
        umasks.append(mask)
        return 0

    monkeypatch.setattr(bot_module, "LOCK_PATH", tmp_path / "bot.lock")
    monkeypatch.setattr(bot_module.os, "umask", record_umask)
    monkeypatch.setattr(
        bot_module,
        "setup_logging",
        lambda level, secrets: real_setup_logging(level, secrets, log_file=log_file),
    )
    (tmp_path / "sandbox").mkdir()
    values = {
        "telegram_bot_token": SECRET_TOKEN,
        "allowed_users": "42",
        "approved_directory": str(tmp_path / "sandbox"),
        "db_path": str(tmp_path / "b.db"),
        "gate_socket_path": str(tmp_path / "g.sock"),
    }
    return {"umask": umasks, "log": log_file, "values": values, "lock": tmp_path / "bot.lock"}


def test_main_reports_invalid_config_without_the_token(
    isolated_main: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad = {**isolated_main["values"], "approved_directory": "/nonexistent-root"}

    def load_settings() -> Any:
        return make_isolated_settings(bad)

    monkeypatch.setattr(bot_module, "Settings", load_settings)
    assert bot_module.main() == 2
    err = capsys.readouterr().err
    assert "APPROVED_DIRECTORY" in err
    assert SECRET_TOKEN not in err


def test_main_logs_fatal_errors_redacted_and_releases_the_lock(
    isolated_main: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    class RejectedApp:
        def run_polling(self, **_kwargs: Any) -> None:
            raise InvalidToken(f"The token `{SECRET_TOKEN}` was rejected by the server.")

    values = isolated_main["values"]
    monkeypatch.setattr(bot_module, "Settings", lambda: make_isolated_settings(values))
    monkeypatch.setattr(bot_module, "build_application", lambda _settings: RejectedApp())
    assert bot_module.main() == 1
    log = isolated_main["log"].read_text()
    assert "Bot stopped because of an unrecoverable error" in log
    assert SECRET_TOKEN not in log
    assert isolated_main["umask"] == [bot_module.PRIVATE_UMASK]
    acquire_instance_lock().close()
