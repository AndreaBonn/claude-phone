from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALLOWED_TOOLS = ("Read", "Grep", "Glob", "Bash", "Edit", "Write")
DEFAULT_AUTO_APPROVE_TOOLS = ("Read", "Grep", "Glob", "LS")
# Linux sun_path is 108 bytes including the trailing NUL.
MAX_UNIX_SOCKET_PATH = 107


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    return value


def _anchor(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


class Settings(BaseSettings):
    """Bridge configuration, loaded from `.env` and the process environment."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    telegram_bot_token: SecretStr
    allowed_users: Annotated[frozenset[int], NoDecode]
    approved_directory: Path
    claude_allowed_tools: Annotated[tuple[str, ...], NoDecode] = DEFAULT_ALLOWED_TOOLS
    claude_auto_approve_tools: Annotated[tuple[str, ...], NoDecode] = DEFAULT_AUTO_APPROVE_TOOLS
    claude_timeout_seconds: int = Field(default=300, gt=0)
    approval_timeout_seconds: int = Field(default=300, gt=0)
    verbose_level: int = Field(default=1, ge=0, le=2)
    db_path: Path = Path("data/bridge.db")
    gate_socket_path: Path = Path("data/gate.sock")
    log_level: str = "INFO"
    claude_bin: str = "claude"
    anthropic_api_key: SecretStr | None = None

    @field_validator("allowed_users", mode="before")
    @classmethod
    def _parse_users(cls, value: object) -> object:
        parsed = _split_csv(value)
        if not parsed:
            raise ValueError("ALLOWED_USERS must contain at least one Telegram user ID")
        return parsed

    @field_validator("claude_allowed_tools", "claude_auto_approve_tools", mode="before")
    @classmethod
    def _parse_tools(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def _empty_key_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("approved_directory")
    @classmethod
    def _resolve_sandbox(cls, value: Path) -> Path:
        resolved = value.expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"APPROVED_DIRECTORY is not an existing directory: {resolved}")
        return resolved

    @field_validator("db_path", "gate_socket_path")
    @classmethod
    def _anchor_paths(cls, value: Path) -> Path:
        return _anchor(value.expanduser())

    @model_validator(mode="after")
    def _check_consistency(self) -> "Settings":
        # Claude must never be able to edit the gate that supervises it.
        if PROJECT_ROOT.is_relative_to(self.approved_directory):
            raise ValueError(
                "APPROVED_DIRECTORY must not contain the bridge itself "
                f"({PROJECT_ROOT}): Claude could rewrite its own permission gate"
            )
        if len(str(self.gate_socket_path).encode()) > MAX_UNIX_SOCKET_PATH:
            raise ValueError(f"GATE_SOCKET_PATH too long: {self.gate_socket_path}")
        return self
