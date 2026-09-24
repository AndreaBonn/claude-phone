import html
import json
import re
from pathlib import PurePath
from typing import Any

TOOL_EMOJI = {
    "Read": "📖",
    "Grep": "🔍",
    "Glob": "🔍",
    "LS": "📂",
    "Bash": "💻",
    "Edit": "✏️",
    "Write": "✏️",
    "NotebookEdit": "✏️",
    "WebFetch": "🌐",
    "WebSearch": "🌐",
    "Task": "🤖",
    "TodoWrite": "📝",
}
DEFAULT_TOOL_EMOJI = "🔧"
PATH_TOOLS = {"Read", "Edit", "Write", "NotebookEdit"}
SUMMARY_KEYS = ("command", "pattern", "description", "url", "query", "path")
SUMMARY_MAX = 200
FULL_INPUT_MAX = 1000
APPROVAL_SNIPPET_MAX = 1500
MAX_CHOICES = 8
CHOICE_LABEL_MAX = 60
_CHOICE = re.compile(r"^[ \t]*\[\[option:[ \t]*(.+?)[ \t]*\]\][ \t]*$", re.MULTILINE)


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_tool_input(name: str, tool_input: dict[str, Any]) -> str:
    """One-line human summary of what a tool call targets."""
    if name in PATH_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if path:
            return PurePath(str(path)).name
    for key in SUMMARY_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return truncate(value.splitlines()[0] if value.strip() else value, SUMMARY_MAX)
    return ""


def format_tool_line(name: str, tool_input: dict[str, Any], verbose: int) -> str | None:
    """Progress line for a tool call, or None when verbosity hides tools."""
    if verbose <= 0:
        return None
    emoji = TOOL_EMOJI.get(name, DEFAULT_TOOL_EMOJI)
    summary = summarize_tool_input(name, tool_input)
    line = f"{emoji} {name}: {summary}" if summary else f"{emoji} {name}"
    if verbose >= 2 and tool_input:
        details = json.dumps(tool_input, ensure_ascii=False, indent=1)
        line += "\n" + truncate(details, FULL_INPUT_MAX)
    return line


def extract_choices(text: str) -> tuple[str, list[str]]:
    """Pull `[[option: label]]` lines out of Claude's answer.

    The convention is taught to Claude by the bridge system prompt, because
    AskUserQuestion is not available in headless mode.
    """
    labels = [truncate(label, CHOICE_LABEL_MAX) for label in _CHOICE.findall(text)]
    if not labels:
        return text, []
    return _CHOICE.sub("", text).strip(), labels[:MAX_CHOICES]


def _approval_body(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "Bash":
        return str(tool_input.get("command", ""))
    if tool_name == "Edit":
        old = truncate(str(tool_input.get("old_string", "")), APPROVAL_SNIPPET_MAX // 2)
        new = truncate(str(tool_input.get("new_string", "")), APPROVAL_SNIPPET_MAX // 2)
        minus = "\n".join(f"- {line}" for line in old.splitlines())
        plus = "\n".join(f"+ {line}" for line in new.splitlines())
        return f"{minus}\n{plus}"
    if tool_name == "Write":
        return str(tool_input.get("content", ""))
    return json.dumps(tool_input, ensure_ascii=False, indent=1)


def format_approval_request(project: str, tool_name: str, tool_input: dict[str, Any]) -> str:
    """HTML text of the approval prompt shown with the inline buttons."""
    lines = [f"🔐 <b>Approvazione richiesta</b> · {html.escape(project)}", f"<b>{tool_name}</b>"]
    path = tool_input.get("file_path")
    if path:
        lines.append(f"<code>{html.escape(str(path))}</code>")
    body = truncate(_approval_body(tool_name, tool_input), APPROVAL_SNIPPET_MAX)
    if body.strip():
        lines.append(f"<pre>{html.escape(body)}</pre>")
    return "\n".join(lines)


def render_progress(header: str, lines: list[str], limit: int) -> str:
    """Header plus as many of the most recent lines as fit in `limit`."""
    kept: list[str] = []
    size = len(header)
    for line in reversed(lines):
        if size + len(line) + 1 > limit:
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join([header, *reversed(kept)])
