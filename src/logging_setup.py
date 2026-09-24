import logging
from collections.abc import Iterable
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.config import PROJECT_ROOT

LOG_FILE = PROJECT_ROOT / "logs" / "bridge.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 3
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
REDACTED = "***"
# httpx logs every request URL at INFO, and Telegram URLs embed the bot token.
NOISY_LOGGERS = ("httpx", "httpcore")


class SecretRedactingFilter(logging.Filter):
    """Replace secret values in every log record before it is written."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = [secret for secret in secrets if secret]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self._secrets:
            message = message.replace(secret, REDACTED)
        record.msg = message
        record.args = None
        return True


def setup_logging(level: str, secrets: Iterable[str], log_file: Path = LOG_FILE) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    redactor = SecretRedactingFilter(secrets)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        RotatingFileHandler(log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS),
    ]
    for handler in handlers:
        handler.addFilter(redactor)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.basicConfig(level=level.upper(), handlers=handlers, force=True)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
