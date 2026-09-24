from pathlib import Path

import pytest

from src.session_store import PendingApproval, SessionStore


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "nested" / "bridge.db")


def test_session_id_roundtrip_and_clear(store: SessionStore) -> None:
    assert store.get_session("alpha") is None
    store.save_session("alpha", "s1")
    store.save_session("alpha", "s2")
    assert store.get_session("alpha") == "s2"
    store.clear_session("alpha")
    assert store.get_session("alpha") is None


def test_sessions_survive_reopening_the_database(tmp_path: Path) -> None:
    SessionStore(tmp_path / "b.db").save_session("alpha", "s1")
    assert SessionStore(tmp_path / "b.db").get_session("alpha") == "s1"


def test_user_state_defaults_and_updates(store: SessionStore) -> None:
    assert store.get_active_project(42) is None
    assert store.get_verbose(42, default=1) == 1
    store.set_active_project(42, "alpha")
    store.set_verbose(42, 2)
    store.set_active_project(42, "beta")
    assert store.get_active_project(42) == "beta"
    assert store.get_verbose(42, default=1) == 2


def test_pending_approvals_are_popped_once(store: SessionStore) -> None:
    store.add_pending_approval("a1", project="alpha", chat_id=42, message_id=7)
    store.add_pending_approval("a2", project="alpha", chat_id=42, message_id=8)
    store.remove_pending_approval("a2")
    assert store.pop_pending_approvals() == [
        PendingApproval(approval_id="a1", project="alpha", chat_id=42, message_id=7)
    ]
    assert store.pop_pending_approvals() == []


def test_audit_log_records_events(store: SessionStore) -> None:
    store.record_audit(event="tool", project="alpha", detail="Bash: ls", outcome="approve")
    store.record_audit(event="prompt", project="alpha", detail="hi", user_id=42)
    rows = store.recent_audit(limit=10)
    assert [row.event for row in rows] == ["prompt", "tool"]
    assert rows[1].outcome == "approve"
    assert rows[0].user_id == 42


def test_database_file_is_private_even_if_it_existed(tmp_path: Path) -> None:
    db_path = tmp_path / "b.db"
    db_path.touch(mode=0o644)
    db_path.chmod(0o644)
    SessionStore(db_path)
    assert db_path.stat().st_mode & 0o777 == 0o600


def test_profile_roundtrip(store: SessionStore) -> None:
    assert store.get_profile(42) is None
    store.set_active_project(42, "alpha")
    store.set_profile(42, "sales")
    assert store.get_profile(42) == "sales"
    assert store.get_active_project(42) == "alpha"


def test_existing_database_gains_the_profile_column(tmp_path: Path) -> None:
    import sqlite3

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE user_states (user_id INTEGER PRIMARY KEY, active_project TEXT, "
        "verbose_level INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO user_states VALUES (42, 'alpha', 2, 'x', 'x')")
    conn.commit()
    conn.close()
    store = SessionStore(db_path)
    store.set_profile(42, "sales")
    assert (store.get_active_project(42), store.get_profile(42)) == ("alpha", "sales")
