import asyncio
import collections
import contextlib
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from src.claude_command import SessionConfig, build_command, build_env, user_message
from src.stream_parser import InitEvent, ResultEvent, StreamEvent, parse_line

logger = logging.getLogger(__name__)

STREAM_LIMIT = 16 * 1024 * 1024
STDERR_TAIL_LINES = 20
STDERR_CHUNK = 65536
STDERR_LINE_MAX = 2000
STOP_GRACE_SECONDS = 5.0
SESSION_NOT_FOUND_MARKER = "No conversation found"

EventCallback = Callable[[StreamEvent], Awaitable[None]]


class ClaudeSessionError(Exception):
    """A turn could not complete."""


class ClaudeCrashedError(ClaudeSessionError):
    """The claude process exited before the end of the turn."""


class ClaudeTimeoutError(ClaudeSessionError):
    """No output from claude for longer than the idle timeout."""


class TurnInterruptedError(ClaudeSessionError):
    """The user stopped the turn; the process was killed on purpose."""


class SessionNotFoundError(ClaudeSessionError):
    """`--resume` pointed at a session claude no longer has."""


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
        self._interrupted = False
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
            self._interrupted = False
            resuming = not self.running and self.session_id is not None
            process = await self._ensure_started()
            if self._interrupted:
                raise TurnInterruptedError("Turno interrotto dall'utente")
            assert process.stdin is not None
            try:
                process.stdin.write(user_message(text))
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise self._ended_error() from exc
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
        # Fixed-size reads, not readline: a line longer than the stream limit
        # would raise and silently stop the drain.
        assert process.stderr is not None
        pending = ""
        while chunk := await process.stderr.read(STDERR_CHUNK):
            *lines, pending = (pending + chunk.decode(errors="replace")).split("\n")
            pending = pending[-STDERR_LINE_MAX:]
            for line in lines:
                self._record_stderr(line)
        self._record_stderr(pending)

    def _record_stderr(self, line: str) -> None:
        line = line.rstrip()[:STDERR_LINE_MAX]
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
                raise self._ended_error()
            for event in parse_line(line):
                if isinstance(event, InitEvent) and event.session_id:
                    self.session_id = event.session_id
                if isinstance(event, ResultEvent):
                    self.session_id = event.session_id or self.session_id
                    return event
                await on_event(event)

    def _ended_error(self) -> ClaudeSessionError:
        """Why the process went away mid-turn: the user's stop, or a crash."""
        if self._interrupted:
            return TurnInterruptedError("Turno interrotto dall'utente")
        return ClaudeCrashedError(self._crash_details())

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
        assert process.stdin is not None  # always a pipe, see _ensure_started
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

    async def interrupt(self) -> bool:
        """Kill the process in the middle of a turn; False if no turn is running.

        There is no graceful cancel over stdin, so the process is signalled
        (SIGTERM, then SIGKILL): the transcript is already on disk and the next
        turn resumes the same session.
        """
        if not self.busy:
            return False
        self._interrupted = True
        process = self._process
        if process is None or process.returncode is not None:
            # Still spawning: run_turn checks the flag before sending the message.
            return True
        process.terminate()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), STOP_GRACE_SECONDS)
            return True
        process.kill()
        await process.wait()
        return True

    async def stop(self) -> None:
        """Close the process cleanly: EOF on stdin, then SIGTERM, then SIGKILL."""
        await self._terminate()
        await self._reap()
