import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    project TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_states (
    user_id INTEGER PRIMARY KEY,
    active_project TEXT,
    verbose_level INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    user_id INTEGER,
    project TEXT,
    detail TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log (created_at);
CREATE TABLE IF NOT EXISTS pending_approvals (
    approval_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


PRIVATE_FILE_MODE = 0o600


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class PendingApproval:
    approval_id: str
    project: str
    chat_id: int
    message_id: int


@dataclass(frozen=True)
class AuditEntry:
    event: str
    user_id: int | None
    project: str | None
    detail: str
    outcome: str
    reason: str
    created_at: str


class SessionStore:
    """SQLite persistence: session ids, per-user state, audit log, open approvals.

    Uses the synchronous sqlite3 driver: every query is a single-row local
    operation, negligible next to the Telegram and Claude round-trips.
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        # The audit log holds prompt texts: readable by the owner only.
        db_path.chmod(PRIVATE_FILE_MODE)
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def get_session(self, project: str) -> str | None:
        row = self._conn.execute(
            "SELECT session_id FROM sessions WHERE project = ?", (project,)
        ).fetchone()
        return row[0] if row else None

    def save_session(self, project: str, session_id: str) -> None:
        now = _now()
        self._conn.execute(
            "INSERT INTO sessions (project, session_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(project) DO UPDATE SET "
            "session_id = excluded.session_id, updated_at = excluded.updated_at",
            (project, session_id, now, now),
        )

    def clear_session(self, project: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE project = ?", (project,))

    def _user_row(self, user_id: int) -> tuple[str | None, int | None]:
        row = self._conn.execute(
            "SELECT active_project, verbose_level FROM user_states WHERE user_id = ?", (user_id,)
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def get_active_project(self, user_id: int) -> str | None:
        return self._user_row(user_id)[0]

    def set_active_project(self, user_id: int, project: str) -> None:
        now = _now()
        self._conn.execute(
            "INSERT INTO user_states (user_id, active_project, created_at, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
            "active_project = excluded.active_project, updated_at = excluded.updated_at",
            (user_id, project, now, now),
        )

    def get_verbose(self, user_id: int, default: int) -> int:
        level = self._user_row(user_id)[1]
        return default if level is None else level

    def set_verbose(self, user_id: int, level: int) -> None:
        now = _now()
        self._conn.execute(
            "INSERT INTO user_states (user_id, verbose_level, created_at, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
            "verbose_level = excluded.verbose_level, updated_at = excluded.updated_at",
            (user_id, level, now, now),
        )

    def record_audit(
        self,
        event: str,
        project: str | None,
        detail: str,
        outcome: str = "",
        reason: str = "",
        user_id: int | None = None,
    ) -> None:
        now = _now()
        self._conn.execute(
            "INSERT INTO audit_log (event, user_id, project, detail, outcome, reason, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event, user_id, project, detail, outcome, reason, now, now),
        )

    def recent_audit(self, limit: int) -> list[AuditEntry]:
        rows = self._conn.execute(
            "SELECT event, user_id, project, detail, outcome, reason, created_at "
            "FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [AuditEntry(*row) for row in rows]

    def add_pending_approval(
        self, approval_id: str, project: str, chat_id: int, message_id: int
    ) -> None:
        now = _now()
        self._conn.execute(
            "INSERT OR REPLACE INTO pending_approvals "
            "(approval_id, project, chat_id, message_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (approval_id, project, chat_id, message_id, now, now),
        )

    def remove_pending_approval(self, approval_id: str) -> None:
        self._conn.execute("DELETE FROM pending_approvals WHERE approval_id = ?", (approval_id,))

    def pop_pending_approvals(self) -> list[PendingApproval]:
        """Return and delete approvals left open by a previous run of the bot."""
        rows = self._conn.execute(
            "SELECT approval_id, project, chat_id, message_id FROM pending_approvals"
        ).fetchall()
        self._conn.execute("DELETE FROM pending_approvals")
        return [PendingApproval(*row) for row in rows]
