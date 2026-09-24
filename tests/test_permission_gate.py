import asyncio
import json
import os
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from src.permission_gate import (
    EXPIRED_NOTE,
    ApprovalBroker,
    ApprovalDecision,
    ApprovalRequest,
)
from src.permission_policy import GatePolicy

HOOK_SCRIPT = Path(__file__).resolve().parent.parent / "src" / "permission_hook.py"
WAIT_TIMEOUT = 5.0


class FakePresenter:
    """Records prompts; optionally answers them as soon as they are shown."""

    def __init__(self, answer: ApprovalDecision | None = None) -> None:
        self.answer = answer
        self.broker: ApprovalBroker | None = None
        self.shown: list[ApprovalRequest] = []
        self.closed: list[tuple[str, ApprovalDecision, str]] = []

    async def show(self, request: ApprovalRequest) -> None:
        self.shown.append(request)
        if self.answer is not None and self.broker is not None:
            self.broker.resolve(request.approval_id, self.answer)

    async def close(self, request: ApprovalRequest, decision: ApprovalDecision, note: str) -> None:
        self.closed.append((request.approval_id, decision, note))


class FailingPresenter(FakePresenter):
    async def show(self, request: ApprovalRequest) -> None:
        raise ConnectionError("telegram down")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "alpha").mkdir()
    return tmp_path.resolve()


def make_broker(
    root: Path, presenter: FakePresenter, timeout: float = WAIT_TIMEOUT
) -> tuple[ApprovalBroker, list[tuple[Any, ...]], list[str]]:
    audit: list[tuple[Any, ...]] = []
    stopped: list[str] = []
    policy = GatePolicy(
        root=root,
        allowed_tools=frozenset({"Read", "Bash", "Edit", "Write"}),
        auto_approve_tools=frozenset({"Read"}),
    )
    broker = ApprovalBroker(
        policy=policy,
        presenter=presenter,
        timeout_seconds=timeout,
        audit=lambda *row: audit.append(row),
        on_stop=stopped.append,
    )
    presenter.broker = broker
    return broker, audit, stopped


def request(root: Path, tool: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": "alpha",
        "tool_name": tool,
        "tool_input": tool_input,
        "cwd": str(root / "alpha"),
    }


async def test_auto_approved_tool_passes_without_buttons(root: Path) -> None:
    presenter = FakePresenter()
    broker, audit, _ = make_broker(root, presenter)
    response = await broker.handle_request(request(root, "Read", {"file_path": "a.py"}))
    assert response["decision"] == "allow"
    assert presenter.shown == []
    assert audit[0][3] == "auto-approved"


async def test_risky_tool_blocks_until_user_approves(root: Path) -> None:
    presenter = FakePresenter()
    broker, _, _ = make_broker(root, presenter)
    task = asyncio.create_task(broker.handle_request(request(root, "Bash", {"command": "ls"})))
    await asyncio.sleep(0.05)
    assert not task.done()
    assert len(broker.pending("alpha")) == 1
    broker.resolve(presenter.shown[0].approval_id, ApprovalDecision.APPROVE)
    response = await asyncio.wait_for(task, WAIT_TIMEOUT)
    assert response["decision"] == "allow"
    assert broker.pending() == []


async def test_denied_tool_returns_deny(root: Path) -> None:
    broker, audit, _ = make_broker(root, FakePresenter(answer=ApprovalDecision.DENY))
    response = await broker.handle_request(request(root, "Edit", {"file_path": "a.py"}))
    assert response == {"decision": "deny", "reason": response["reason"], "stop": False}
    assert audit[0][3] == "deny"


async def test_deny_and_stop_sets_stop_flag_and_notifies(root: Path) -> None:
    broker, _, stopped = make_broker(root, FakePresenter(answer=ApprovalDecision.DENY_AND_STOP))
    response = await broker.handle_request(request(root, "Bash", {"command": "ls"}))
    assert response["decision"] == "deny" and response["stop"] is True
    assert stopped == ["alpha"]


async def test_approval_timeout_denies_and_closes_prompt(root: Path) -> None:
    presenter = FakePresenter()
    broker, _, _ = make_broker(root, presenter, timeout=0.05)
    response = await broker.handle_request(request(root, "Bash", {"command": "ls"}))
    assert response["decision"] == "deny"
    assert presenter.closed == [
        (presenter.shown[0].approval_id, ApprovalDecision.DENY, EXPIRED_NOTE)
    ]
    assert broker.resolve(presenter.shown[0].approval_id, ApprovalDecision.APPROVE) is None


async def test_presenter_failure_denies(root: Path) -> None:
    broker, _, _ = make_broker(root, FailingPresenter())
    response = await broker.handle_request(request(root, "Bash", {"command": "ls"}))
    assert response["decision"] == "deny"


async def test_sandbox_violation_is_blocked_even_for_auto_tools(root: Path) -> None:
    presenter = FakePresenter(answer=ApprovalDecision.APPROVE)
    broker, audit, _ = make_broker(root, presenter)
    response = await broker.handle_request(request(root, "Read", {"file_path": "/etc/passwd"}))
    assert response["decision"] == "deny"
    assert presenter.shown == []
    assert audit[0][3] == "blocked"


async def test_cancel_denies_pending_requests(root: Path) -> None:
    presenter = FakePresenter()
    broker, _, _ = make_broker(root, presenter)
    task = asyncio.create_task(broker.handle_request(request(root, "Bash", {"command": "ls"})))
    await asyncio.sleep(0.05)
    await broker.cancel(project="alpha")
    assert (await asyncio.wait_for(task, WAIT_TIMEOUT))["decision"] == "deny"
    assert len(presenter.closed) == 1


# --- end to end: the real hook script talking to the broker over a Unix socket ---


@pytest.fixture
async def socket_path() -> AsyncIterator[Path]:
    # Short path: Unix socket paths are limited to 107 bytes.
    with tempfile.TemporaryDirectory(prefix="gate") as directory:
        yield Path(directory) / "g.sock"


async def run_hook(socket_path: Path, root: Path, tool: str, tool_input: dict[str, Any]) -> str:
    env = {**os.environ, "BRIDGE_GATE_SOCKET": str(socket_path), "BRIDGE_PROJECT": "alpha"}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(HOOK_SCRIPT),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        env=env,
    )
    event = {
        "session_id": "s1",
        "tool_name": tool,
        "tool_input": tool_input,
        "cwd": str(root / "alpha"),
    }
    stdout, _ = await asyncio.wait_for(
        process.communicate(json.dumps(event).encode()), WAIT_TIMEOUT
    )
    assert process.returncode == 0
    return stdout.decode()


def decision_of(stdout: str) -> str:
    return str(json.loads(stdout)["hookSpecificOutput"]["permissionDecision"])


@pytest.mark.parametrize(
    ("answer", "expected"),
    [(ApprovalDecision.APPROVE, "allow"), (ApprovalDecision.DENY, "deny")],
)
async def test_hook_end_to_end_follows_user_answer(
    root: Path, socket_path: Path, answer: ApprovalDecision, expected: str
) -> None:
    broker, _, _ = make_broker(root, FakePresenter(answer=answer))
    await broker.start(socket_path)
    try:
        stdout = await run_hook(socket_path, root, "Bash", {"command": "ls"})
    finally:
        await broker.stop()
    assert decision_of(stdout) == expected
    assert not socket_path.exists()


async def test_hook_end_to_end_stop_sets_continue_false(root: Path, socket_path: Path) -> None:
    broker, _, _ = make_broker(root, FakePresenter(answer=ApprovalDecision.DENY_AND_STOP))
    await broker.start(socket_path)
    try:
        output = json.loads(await run_hook(socket_path, root, "Bash", {"command": "ls"}))
    finally:
        await broker.stop()
    assert output["continue"] is False


async def test_hook_end_to_end_passthrough_prints_nothing(root: Path, socket_path: Path) -> None:
    broker, _, _ = make_broker(root, FakePresenter())
    await broker.start(socket_path)
    try:
        stdout = await run_hook(socket_path, root, "TodoWrite", {"todos": []})
    finally:
        await broker.stop()
    assert stdout == ""


async def test_hook_denies_when_bridge_is_down(root: Path, socket_path: Path) -> None:
    stdout = await run_hook(socket_path, root, "Read", {"file_path": "a.py"})
    assert decision_of(stdout) == "deny"
