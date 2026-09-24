import logging
from pathlib import Path

from src.logging_setup import REDACTED, setup_logging


def test_setup_logging_redacts_secrets_in_messages_and_args(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "bridge.log"
    setup_logging(level="INFO", secrets=["123:SECRET", ""], log_file=log_file)
    logger = logging.getLogger("test.redaction")
    logger.info("POST https://api.telegram.org/bot123:SECRET/getUpdates")
    logger.info("token=%s", "123:SECRET")
    logger.info("harmless line")
    for handler in logging.getLogger().handlers:
        handler.flush()
    content = log_file.read_text()
    assert "123:SECRET" not in content
    assert content.count(REDACTED) == 2
    assert "harmless line" in content


def test_setup_logging_silences_httpx_info(tmp_path: Path) -> None:
    setup_logging(level="DEBUG", secrets=[], log_file=tmp_path / "b.log")
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
