import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePath
from typing import Any

from src.project_manager import Sandbox, SandboxError

PATH_FIELDS = ("file_path", "path", "notebook_path")
GLOB_CHARS = re.compile(r"[*?\[{]")
# Redirection targets that are harmless even though they live outside the sandbox.
BASH_SAFE_PATHS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/stdin"})
_REDIRECT_PREFIX = re.compile(r"^\d*[<>]+&?")
# Bookkeeping tools with no effect outside Claude's own state; subagent tool
# calls are gated by the same hook, so spawning one is harmless on its own.
NO_EFFECT_TOOLS = frozenset({"TodoWrite", "Task", "Agent", "ExitPlanMode", "Skill", "ToolSearch"})
# Tools that may reach the read-only directories: readers, and Bash, which
# always goes through the user's approval (skills run their own scripts).
READ_ONLY_OK_TOOLS = frozenset({"Read", "Grep", "Glob", "LS", "Bash"})
UNPARSEABLE_BASH_WARNING = "⚠️ Comando non analizzabile automaticamente: controllalo a mano"


class GateAction(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    BLOCK = "block"


@dataclass(frozen=True)
class GateVerdict:
    action: GateAction
    reason: str = ""


@dataclass(frozen=True)
class GatePolicy:
    sandbox: Sandbox
    allowed_tools: frozenset[str]
    auto_approve_tools: frozenset[str]


def _looks_like_path(token: str) -> bool:
    return token.startswith(("/", "~")) or ".." in PurePath(token).parts


def _glob_static_prefix(pattern: str) -> str:
    match = GLOB_CHARS.search(pattern)
    prefix = pattern[: match.start()] if match else pattern
    return prefix or "."


def _tool_paths(tool_name: str, tool_input: dict[str, Any]) -> Iterator[str]:
    for key in PATH_FIELDS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            yield value
    pattern = tool_input.get("pattern")
    if tool_name == "Glob" and isinstance(pattern, str) and _looks_like_path(pattern):
        yield _glob_static_prefix(pattern)


def _bash_paths(command: str) -> list[str] | None:
    """Path-like tokens of a shell command, or None if it cannot be tokenized."""
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        return None
    paths = []
    for token in tokens:
        for part in _REDIRECT_PREFIX.sub("", token).split("="):
            if part and part not in BASH_SAFE_PATHS and _looks_like_path(part):
                paths.append(part)
    return paths


def _outside(paths: list[str], cwd: Path, sandbox: Sandbox, read_only_ok: bool) -> str | None:
    for raw in paths:
        try:
            sandbox.resolve(raw=raw, cwd=cwd, read_only_ok=read_only_ok)
        except SandboxError:
            return raw
    return None


def classify_tool_call(
    tool_name: str, tool_input: dict[str, Any], cwd: Path, policy: GatePolicy
) -> GateVerdict:
    """Decide what the gate does with one tool call.

    Sandbox violations win over everything, including auto-approval and the
    user's approval: a path outside the sandbox roots, or inside the bridge,
    is always blocked.
    Any tool not explicitly listed (MCP tools, WebFetch, NotebookEdit...) needs
    the user's approval: nothing falls through to Claude's own permission rules.
    The Bash check is a best-effort token scan, not a sandbox: the real guard
    for shell commands is the human approval that always follows it.
    """
    if not policy.sandbox.contains(cwd):
        return GateVerdict(GateAction.BLOCK, f"Directory di lavoro fuori sandbox: {cwd}")
    paths = list(_tool_paths(tool_name, tool_input))
    warning = ""
    if tool_name == "Bash":
        bash_paths = _bash_paths(str(tool_input.get("command", "")))
        warning = UNPARSEABLE_BASH_WARNING if bash_paths is None else ""
        paths.extend(bash_paths or [])
    read_only_ok = tool_name in READ_ONLY_OK_TOOLS
    escaped = _outside(paths, cwd=cwd, sandbox=policy.sandbox, read_only_ok=read_only_ok)
    if escaped is not None:
        return GateVerdict(GateAction.BLOCK, f"Percorso fuori dalla sandbox: {escaped}")
    if tool_name in policy.auto_approve_tools or tool_name in NO_EFFECT_TOOLS:
        return GateVerdict(GateAction.ALLOW)
    return GateVerdict(GateAction.ASK, warning)
