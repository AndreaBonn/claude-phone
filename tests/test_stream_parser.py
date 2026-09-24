import json
from typing import Any

from src.stream_parser import (
    InitEvent,
    ResultEvent,
    TextEvent,
    ToolResultEvent,
    ToolUseEvent,
    parse_line,
)


def line(payload: dict[str, Any]) -> str:
    return json.dumps(payload)


def test_parse_line_init_event_carries_session_id() -> None:
    events = parse_line(line({"type": "system", "subtype": "init", "session_id": "s1"}))
    assert events == [InitEvent(session_id="s1")]


def test_parse_line_assistant_text_and_tool_use_in_order() -> None:
    payload = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "thinking", "thinking": "hidden"},
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
            ]
        },
    }
    assert parse_line(line(payload)) == [
        TextEvent(text="Let me check."),
        ToolUseEvent(tool_use_id="t1", name="Bash", input={"command": "ls"}),
    ]


def test_parse_line_tool_result_flattens_list_content() -> None:
    payload = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "is_error": True,
                    "content": [{"type": "text", "text": "denied"}],
                }
            ]
        },
    }
    assert parse_line(line(payload)) == [
        ToolResultEvent(tool_use_id="t1", is_error=True, content="denied")
    ]


def test_parse_line_result_event() -> None:
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Done.",
        "session_id": "s1",
        "total_cost_usd": 0.12,
        "num_turns": 3,
    }
    assert parse_line(line(payload)) == [
        ResultEvent(
            session_id="s1",
            text="Done.",
            is_error=False,
            subtype="success",
            cost_usd=0.12,
            num_turns=3,
        )
    ]


def test_parse_line_ignores_noise_and_garbage() -> None:
    assert parse_line(line({"type": "system", "subtype": "hook_started"})) == []
    assert parse_line(line({"type": "rate_limit_event"})) == []
    assert parse_line("not json at all") == []
    assert parse_line("") == []
    assert parse_line(line({"type": "result"})) != []
