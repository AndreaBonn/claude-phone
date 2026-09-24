from pathlib import Path
from typing import Any

import pytest

from src.permission_policy import (
    UNPARSEABLE_BASH_WARNING,
    GateAction,
    GatePolicy,
    classify_tool_call,
)
from src.project_manager import Sandbox


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "sandbox" / "alpha").mkdir(parents=True)
    return (tmp_path / "sandbox").resolve()


@pytest.fixture
def policy(root: Path) -> GatePolicy:
    return GatePolicy(
        sandbox=Sandbox(
            roots=(root,), excluded=(root / "bridge",), read_only=(root.parent / "skills",)
        ),
        allowed_tools=frozenset({"Read", "Grep", "Glob", "Bash", "Edit", "Write"}),
        auto_approve_tools=frozenset({"Read", "Grep", "Glob", "LS"}),
    )


def classify(policy: GatePolicy, tool: str, tool_input: dict[str, Any]) -> GateAction:
    cwd = policy.sandbox.roots[0] / "alpha"
    return classify_tool_call(tool_name=tool, tool_input=tool_input, cwd=cwd, policy=policy).action


def test_read_only_tool_inside_sandbox_is_auto_approved(policy: GatePolicy) -> None:
    assert classify(policy, "Read", {"file_path": "src/app.py"}) is GateAction.ALLOW
    assert classify(policy, "Grep", {"pattern": "foo"}) is GateAction.ALLOW


@pytest.mark.parametrize("tool", ["Bash", "Edit", "Write"])
def test_risky_tool_requires_approval(policy: GatePolicy, tool: str) -> None:
    tool_input = {"command": "uv run pytest"} if tool == "Bash" else {"file_path": "a.py"}
    assert classify(policy, tool, tool_input) is GateAction.ASK


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "../../etc/passwd"}),
        ("Read", {"file_path": "/etc/passwd"}),
        ("Write", {"file_path": "~/.bashrc", "content": "x"}),
        ("Grep", {"pattern": "x", "path": "/home"}),
        ("Glob", {"pattern": "/etc/**/*.conf"}),
        ("Glob", {"pattern": "../../**"}),
        ("NotebookEdit", {"notebook_path": "/tmp/n.ipynb"}),
    ],
)
def test_paths_outside_sandbox_are_blocked(
    policy: GatePolicy, tool: str, tool_input: dict[str, Any]
) -> None:
    assert classify(policy, tool, tool_input) is GateAction.BLOCK


@pytest.mark.parametrize(
    "command",
    [
        "cat /etc/passwd",
        "cd ../.. && ls",
        "ls ~",
        "cp a.py --target-directory=/tmp",
        "echo x >/tmp/y",
    ],
)
def test_bash_touching_outside_paths_is_blocked(policy: GatePolicy, command: str) -> None:
    assert classify(policy, "Bash", {"command": command}) is GateAction.BLOCK


@pytest.mark.parametrize(
    "command", ["ls .. 2>/dev/null", "cat ../alpha/x > /dev/null", "git status"]
)
def test_bash_inside_sandbox_goes_to_approval(policy: GatePolicy, command: str) -> None:
    assert classify(policy, "Bash", {"command": command}) is GateAction.ASK


def test_unparseable_bash_asks_with_warning(policy: GatePolicy) -> None:
    verdict = classify_tool_call(
        tool_name="Bash",
        tool_input={"command": "cat <<EOF\ndon't\nEOF"},
        cwd=policy.sandbox.roots[0] / "alpha",
        policy=policy,
    )
    assert verdict.action is GateAction.ASK
    assert verdict.reason == UNPARSEABLE_BASH_WARNING


def test_internal_bookkeeping_tool_is_allowed(policy: GatePolicy) -> None:
    assert classify(policy, "TodoWrite", {"todos": []}) is GateAction.ALLOW


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("mcp__firebase__deploy", {"project": "prod"}),
        ("WebFetch", {"url": "https://example.com"}),
        ("NotebookEdit", {"notebook_path": "n.ipynb", "new_source": "x"}),
    ],
)
def test_unlisted_tools_require_approval(
    policy: GatePolicy, tool: str, tool_input: dict[str, Any]
) -> None:
    assert classify(policy, tool, tool_input) is GateAction.ASK


def test_cwd_outside_sandbox_blocks_everything(policy: GatePolicy, tmp_path: Path) -> None:
    verdict = classify_tool_call(
        tool_name="Read", tool_input={"file_path": "x"}, cwd=tmp_path, policy=policy
    )
    assert verdict.action is GateAction.BLOCK


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "../bridge/src/permission_hook.py"}),
        ("Edit", {"file_path": "../bridge/src/permission_policy.py"}),
        ("Bash", {"command": "cat ../bridge/.env"}),
    ],
)
def test_bridge_directory_is_blocked_even_for_auto_tools(
    policy: GatePolicy, tool: str, tool_input: dict[str, Any]
) -> None:
    assert classify(policy, tool, tool_input) is GateAction.BLOCK


@pytest.mark.parametrize(
    ("tool", "tool_input", "expected"),
    [
        ("Read", {"file_path": "../../skills/x/SKILL.md"}, GateAction.ALLOW),
        ("Grep", {"pattern": "a", "path": "../../skills"}, GateAction.ALLOW),
        ("Bash", {"command": "uv run --script ../../skills/x/run.py"}, GateAction.ASK),
        ("Edit", {"file_path": "../../skills/x/SKILL.md"}, GateAction.BLOCK),
        ("Write", {"file_path": "../../skills/new.md", "content": "x"}, GateAction.BLOCK),
    ],
)
def test_claude_config_dirs_are_read_only(
    policy: GatePolicy, tool: str, tool_input: dict[str, Any], expected: GateAction
) -> None:
    assert classify(policy, tool, tool_input) is expected


@pytest.mark.parametrize("tool", ["Skill", "ToolSearch"])
def test_skill_loading_is_allowed(policy: GatePolicy, tool: str) -> None:
    assert classify(policy, tool, {"skill": "scrivi-italiano"}) is GateAction.ALLOW
