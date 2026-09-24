import os

# Opt in to python-telegram-bot's future timedelta type for RetryAfter.retry_after:
# telegram_io already handles it, and this silences the v22 deprecation warning.
os.environ.setdefault("PTB_TIMEDELTA", "1")
