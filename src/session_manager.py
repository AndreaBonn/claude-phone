import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.claude_command import SessionConfig
from src.claude_session import ClaudeSession, EventCallback, SessionNotFoundError
from src.profiles import DEFAULT_PROFILE
from src.project_manager import ProjectManager
from src.session_store import SessionStore
from src.stream_parser import ResultEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnOutcome:
    result: ResultEvent
    # True when the saved session could not be resumed and a new one was started.
    fresh_session: bool = False


class SessionManager:
    """Owns one ClaudeSession per project and keeps session ids persisted."""

    def __init__(
        self,
        config: SessionConfig,
        store: SessionStore,
        projects: ProjectManager,
        is_waiting_for_user: Callable[[str], bool],
    ) -> None:
        self._config = config
        self._store = store
        self._projects = projects
        self._is_waiting_for_user = is_waiting_for_user
        self._sessions: dict[str, ClaudeSession] = {}
        self.profile = DEFAULT_PROFILE

    def _key(self, project: str) -> str:
        # Each profile has its own transcripts, so session ids are per profile.
        return f"{self.profile}::{project}"

    async def set_profile(self, profile: str, config_dir: Path | None) -> bool:
        """Switch Claude profile; False (nothing changed) while a turn is running."""
        if any(session.busy for session in self._sessions.values()):
            return False
        await self.stop_all()
        self.profile = profile
        self._config = dataclasses.replace(self._config, config_dir=config_dir)
        return True

    def get(self, project: str) -> ClaudeSession:
        session = self._sessions.get(project)
        if session is None:
            session = ClaudeSession(
                project=project,
                cwd=self._projects.resolve_project(project),
                config=self._config,
                session_id=self._store.get_session(self._key(project)),
                is_waiting_for_user=lambda: self._is_waiting_for_user(project),
            )
            self._sessions[project] = session
        return session

    def is_busy(self, project: str) -> bool:
        session = self._sessions.get(project)
        return session is not None and session.busy

    def is_running(self, project: str) -> bool:
        session = self._sessions.get(project)
        return session is not None and session.running

    def session_id(self, project: str) -> str | None:
        session = self._sessions.get(project)
        if session is not None:
            return session.session_id
        return self._store.get_session(self._key(project))

    def request_stop(self, project: str) -> None:
        """Stop the project's process as soon as the current turn ends."""
        session = self._sessions.get(project)
        if session is not None:
            session.stop_requested = True

    async def interrupt(self, project: str) -> bool:
        """Kill the project's running turn; False if nothing was running."""
        session = self._sessions.get(project)
        return session is not None and await session.interrupt()

    async def run_turn(self, project: str, text: str, on_event: EventCallback) -> TurnOutcome:
        try:
            result = await self._run_and_persist(project, text, on_event)
            fresh = False
        except SessionNotFoundError:
            logger.warning("Saved session for %s not found, starting a new one", project)
            await self.reset(project)
            result = await self._run_and_persist(project, text, on_event)
            fresh = True
        session = self._sessions.get(project)
        if session is not None and session.stop_requested:
            session.stop_requested = False
            await session.stop()
        return TurnOutcome(result=result, fresh_session=fresh)

    async def _run_and_persist(
        self, project: str, text: str, on_event: EventCallback
    ) -> ResultEvent:
        session = self.get(project)
        try:
            return await session.run_turn(text, on_event)
        finally:
            if session.session_id:
                self._store.save_session(self._key(project), session.session_id)

    async def discard(self, project: str) -> None:
        """Stop the project's process; its saved session id stays resumable."""
        session = self._sessions.pop(project, None)
        if session is not None:
            await session.stop()

    async def reset(self, project: str) -> None:
        """Forget the project's conversation: the next message starts a new session."""
        await self.discard(project)
        self._store.clear_session(self._key(project))

    async def stop_all(self) -> None:
        for project in list(self._sessions):
            await self.discard(project)
