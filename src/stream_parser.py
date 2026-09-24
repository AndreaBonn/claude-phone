import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InitEvent:
    session_id: str


@dataclass(frozen=True)
class TextEvent:
    text: str


@dataclass(frozen=True)
class ContextEvent:
    """Text Claude Code injects on the user's side: skill bodies, hook feedback."""

    text: str


@dataclass(frozen=True)
class ToolUseEvent:
    tool_use_id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResultEvent:
    tool_use_id: str
    is_error: bool
    content: str


@dataclass(frozen=True)
class ResultEvent:
    session_id: str | None
    text: str
    is_error: bool
    subtype: str
    cost_usd: float | None = None
    num_turns: int | None = None


StreamEvent = InitEvent | TextEvent | ContextEvent | ToolUseEvent | ToolResultEvent | ResultEvent


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) for item in content if isinstance(item, dict)
        ).strip()
    return ""


def _parse_blocks(blocks: Any, from_user: bool) -> list[StreamEvent]:
    if from_user and isinstance(blocks, str):
        return [ContextEvent(text=blocks)] if blocks.strip() else []
    events: list[StreamEvent] = []
    for block in blocks if isinstance(blocks, list) else []:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "text" and block.get("text"):
            # User-role text is never typed by the user in -p mode: it is
            # content Claude Code injects, not something Claude said.
            text = block["text"]
            events.append(ContextEvent(text=text) if from_user else TextEvent(text=text))
        elif kind == "tool_use":
            events.append(
                ToolUseEvent(
                    tool_use_id=str(block.get("id", "")),
                    name=str(block.get("name", "?")),
                    input=block.get("input") or {},
                )
            )
        elif kind == "tool_result":
            events.append(
                ToolResultEvent(
                    tool_use_id=str(block.get("tool_use_id", "")),
                    is_error=bool(block.get("is_error")),
                    content=_flatten_content(block.get("content")),
                )
            )
    return events


def _parse_result(payload: dict[str, Any]) -> ResultEvent:
    return ResultEvent(
        session_id=payload.get("session_id"),
        text=str(payload.get("result") or ""),
        is_error=bool(payload.get("is_error")),
        subtype=str(payload.get("subtype", "")),
        cost_usd=payload.get("total_cost_usd"),
        num_turns=payload.get("num_turns"),
    )


def parse_line(line: str | bytes) -> list[StreamEvent]:
    """Translate one stream-json line from `claude -p` into bridge events.

    Events the bridge does not render (hook progress, rate limits, thinking)
    are dropped. Malformed lines are logged and dropped instead of crashing
    the reader loop.
    """
    if not line or not line.strip():
        return []
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        logger.warning("Skipping non-JSON line from claude: %.200r", line)
        return []
    kind = payload.get("type")
    if kind == "system" and payload.get("subtype") == "init":
        return [InitEvent(session_id=str(payload.get("session_id", "")))]
    if kind in ("assistant", "user"):
        content = (payload.get("message") or {}).get("content")
        return _parse_blocks(content, from_user=kind == "user")
    if kind == "result":
        return [_parse_result(payload)]
    return []
