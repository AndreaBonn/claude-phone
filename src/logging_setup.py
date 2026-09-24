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


class SecretRedactingFormatter(logging.Formatter):
    """Redact secrets from the final text of a record, traceback included.

    Redacting at the formatter level matters: exception messages (for example
    python-telegram-bot's InvalidToken) carry the token inside `exc_info`,
    which a filter on the message alone never sees.
    """

    def __init__(self, fmt: str, secrets: Iterable[str]) -> None:
        super().__init__(fmt)
        self._secrets = [secret for secret in secrets if secret]

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text


def setup_logging(level: str, secrets: Iterable[str], log_file: Path = LOG_FILE) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    formatter = SecretRedactingFormatter(LOG_FORMAT, secrets)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        RotatingFileHandler(log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=level.upper(), handlers=handlers, force=True)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
