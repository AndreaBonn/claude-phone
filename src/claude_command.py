import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from src.config import PROJECT_ROOT
from src.permission_hook import PROJECT_ENV, SOCKET_ENV, TIMEOUT_ENV

HOOK_SCRIPT = PROJECT_ROOT / "src" / "permission_hook.py"
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "prompts" / "telegram-bridge-system-v3.md"
# The hook must outlive the approval wait, and Claude must outlive the hook.
HOOK_TIMEOUT_MARGIN = 30
SECRET_ENV_VARS = ("TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY")
CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"
VIRTUAL_ENV_VAR = "VIRTUAL_ENV"
BRIDGE_VENV = PROJECT_ROOT / ".venv"


@dataclass(frozen=True)
class SessionConfig:
    claude_command: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    gate_socket: Path
    approval_timeout: int
    idle_timeout: float
    system_prompt: str
    api_key: str | None = None
    # Claude profile directory (CLAUDE_CONFIG_DIR); None means the default ~/.claude.
    config_dir: Path | None = None


def build_hook_settings(config: SessionConfig) -> str:
    command = f'"{sys.executable}" "{HOOK_SCRIPT}"'
    hook = {
        "type": "command",
        "command": command,
        "timeout": config.approval_timeout + 2 * HOOK_TIMEOUT_MARGIN,
    }
    return json.dumps({"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [hook]}]}})


def build_command(config: SessionConfig, session_id: str | None) -> list[str]:
    command = [
        *config.claude_command,
        "-p",
        "--output-format",
        "stream-json",
        "--input-format",
        "stream-json",
        "--verbose",
        "--allowedTools",
        ",".join(config.allowed_tools),
        "--permission-mode",
        "default",
        "--settings",
        build_hook_settings(config),
        "--append-system-prompt",
        config.system_prompt,
    ]
    if session_id:
        command += ["--resume", session_id]
    return command


def _without_bridge_venv(env: dict[str, str]) -> dict[str, str]:
    """Undo what `uv run` did to the bot's environment.

    `uv run` puts the bridge's .venv first in PATH and sets VIRTUAL_ENV; inherited
    by claude, every `python3` in hooks and Bash became the bridge interpreter
    (the user's verify.sh failed on a missing pyyaml). A foreign venv is kept.
    """
    if env.get(VIRTUAL_ENV_VAR) != str(BRIDGE_VENV):
        return env
    venv_bin = str(BRIDGE_VENV / "bin")
    cleaned = {key: value for key, value in env.items() if key != VIRTUAL_ENV_VAR}
    entries = env.get("PATH", "").split(os.pathsep)
    cleaned["PATH"] = os.pathsep.join(entry for entry in entries if entry != venv_bin)
    return cleaned


def build_env(config: SessionConfig, project: str, base: Mapping[str, str]) -> dict[str, str]:
    """Child environment: bridge variables in, bot secrets and bot venv out.

    Claude can run `env` through Bash, so the bot token must never reach it.
    """
    env = {key: value for key, value in base.items() if key not in SECRET_ENV_VARS}
    env = _without_bridge_venv(env)
    env[SOCKET_ENV] = str(config.gate_socket)
    env[PROJECT_ENV] = project
    env[TIMEOUT_ENV] = str(config.approval_timeout + HOOK_TIMEOUT_MARGIN)
    if config.api_key:
        env["ANTHROPIC_API_KEY"] = config.api_key
    # Explicit either way: the bot's own shell may carry another profile.
    env.pop(CONFIG_DIR_ENV, None)
    if config.config_dir is not None:
        env[CONFIG_DIR_ENV] = str(config.config_dir)
    return env


def user_message(text: str) -> bytes:
    payload = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    return json.dumps(payload).encode() + b"\n"
