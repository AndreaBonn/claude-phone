import asyncio
import collections
import contextlib
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from src.config import PROJECT_ROOT
from src.permission_hook import PROJECT_ENV, SOCKET_ENV, TIMEOUT_ENV
from src.stream_parser import InitEvent, ResultEvent, StreamEvent, parse_line

logger = logging.getLogger(__name__)

HOOK_SCRIPT = PROJECT_ROOT / "src" / "permission_hook.py"
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "prompts" / "telegram-bridge-system-v1.md"
STREAM_LIMIT = 16 * 1024 * 1024
STDERR_TAIL_LINES = 20
STOP_GRACE_SECONDS = 5.0
# The hook must outlive the approval wait, and Claude must outlive the hook.
HOOK_TIMEOUT_MARGIN = 30
SECRET_ENV_VARS = ("TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY")
SESSION_NOT_FOUND_MARKER = "No conversation found"

EventCallback = Callable[[StreamEvent], Awaitable[None]]


class ClaudeSessionError(Exception):
    """A turn could not complete."""


class ClaudeCrashedError(ClaudeSessionError):
    """The claude process exited before the end of the turn."""


class ClaudeTimeoutError(ClaudeSessionError):
    """No output from claude for longer than the idle timeout."""


class SessionNotFoundError(ClaudeSessionError):
    """`--resume` pointed at a session claude no longer has."""


@dataclass(frozen=True)
class SessionConfig:
    claude_command: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    gate_socket: Path
    approval_timeout: int
    idle_timeout: float
    system_prompt: str
    api_key: str | None = None


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


def build_env(config: SessionConfig, project: str, base: Mapping[str, str]) -> dict[str, str]:
    """Child environment: bridge variables in, bot secrets out.

    Claude can run `env` through Bash, so the bot token must never reach it.
    """
    env = {key: value for key, value in base.items() if key not in SECRET_ENV_VARS}
    env[SOCKET_ENV] = str(config.gate_socket)
    env[PROJECT_ENV] = project
    env[TIMEOUT_ENV] = str(config.approval_timeout + HOOK_TIMEOUT_MARGIN)
    if config.api_key:
        env["ANTHROPIC_API_KEY"] = config.api_key
    return env


def user_message(text: str) -> bytes:
    payload = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    return json.dumps(payload).encode() + b"\n"


class ClaudeSession:
    """One long-running `claude -p` process bound to a project directory.

    The process survives between turns so the conversation context stays
    loaded; turns are serialized by a lock, so messages sent while Claude is
    working are queued instead of racing on the same stdin.
    """

    def __init__(
        self,
        project: str,
        cwd: Path,
        config: SessionConfig,
        session_id: str | None,
        is_waiting_for_user: Callable[[], bool],
    ) -> None:
        self.project = project
        self.cwd = cwd
        self.session_id = session_id
        self.stop_requested = False
        self._config = config
        self._is_waiting_for_user = is_waiting_for_user
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_tail: collections.deque[str] = collections.deque(maxlen=STDERR_TAIL_LINES)
        self._stderr_task: asyncio.Task[None] | None = None

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def run_turn(self, text: str, on_event: EventCallback) -> ResultEvent:
        """Send one user message and stream events until Claude's final result."""
        async with self._lock:
            resuming = not self.running and self.session_id is not None
            process = await self._ensure_started()
            assert process.stdin is not None
            try:
                process.stdin.write(user_message(text))
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise ClaudeCrashedError(self._crash_details()) from exc
            result = await self._read_until_result(process, on_event)
            if resuming and result.is_error and result.num_turns == 0:
                await self._reap()
                if any(SESSION_NOT_FOUND_MARKER in line for line in self._stderr_tail):
                    raise SessionNotFoundError(self.session_id or "")
            return result

    async def _ensure_started(self) -> asyncio.subprocess.Process:
        if self._process is not None and self._process.returncode is None:
            return self._process
        self._stderr_tail.clear()
        command = build_command(self._config, self.session_id)
        logger.info("Starting claude for %s (resume=%s)", self.project, self.session_id)
        self._process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.cwd,
            env=build_env(self._config, self.project, os.environ),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LIMIT,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr(self._process))
        return self._process

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> None:
        # An unread stderr pipe fills up and freezes the child: always drain it.
        assert process.stderr is not None
        async for raw in process.stderr:
            line = raw.decode(errors="replace").rstrip()
            if line:
                self._stderr_tail.append(line)
                logger.debug("claude[%s] stderr: %s", self.project, line)

    async def _next_line(self, process: asyncio.subprocess.Process) -> bytes:
        assert process.stdout is not None
        while True:
            try:
                return await asyncio.wait_for(
                    process.stdout.readline(), timeout=self._config.idle_timeout
                )
            except TimeoutError:
                # Time spent waiting for the user's approval is not inactivity.
                if self._is_waiting_for_user():
                    continue
                await self._terminate()
                raise ClaudeTimeoutError(
                    f"Nessun output da Claude per {self._config.idle_timeout:.0f}s"
                ) from None

    async def _read_until_result(
        self, process: asyncio.subprocess.Process, on_event: EventCallback
    ) -> ResultEvent:
        while True:
            line = await self._next_line(process)
            if not line:
                await self._reap()
                raise ClaudeCrashedError(self._crash_details())
            for event in parse_line(line):
                if isinstance(event, InitEvent) and event.session_id:
                    self.session_id = event.session_id
                if isinstance(event, ResultEvent):
                    self.session_id = event.session_id or self.session_id
                    return event
                await on_event(event)

    def _crash_details(self) -> str:
        code = self._process.returncode if self._process is not None else None
        tail = "\n".join(self._stderr_tail) or "(stderr vuoto)"
        return f"Claude Code terminato inaspettatamente (exit {code}).\n{tail}"

    async def _reap(self) -> None:
        if self._process is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._process.wait(), STOP_GRACE_SECONDS)
        if self._stderr_task is not None:
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._stderr_task, STOP_GRACE_SECONDS)

    async def _terminate(self) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            return
        if process.stdin is not None:
            process.stdin.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), STOP_GRACE_SECONDS)
            return
        process.terminate()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), STOP_GRACE_SECONDS)
            return
        process.kill()
        await process.wait()

    async def stop(self) -> None:
        """Close the process cleanly: EOF on stdin, then SIGTERM, then SIGKILL."""
        await self._terminate()
        await self._reap()
