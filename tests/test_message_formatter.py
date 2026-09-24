from src.message_formatter import (
    MAX_CHOICES,
    describe_event,
    extract_choices,
    format_approval_request,
    format_tool_line,
    render_progress,
)
from src.stream_parser import TextEvent, ToolResultEvent, ToolUseEvent


def test_format_tool_line_verbose_zero_hides_tools() -> None:
    assert format_tool_line(name="Read", tool_input={"file_path": "/a/b.py"}, verbose=0) is None


def test_format_tool_line_read_shows_basename() -> None:
    line = format_tool_line(name="Read", tool_input={"file_path": "/a/src/app.py"}, verbose=1)
    assert line == "📖 Read: app.py"


def test_format_tool_line_bash_shows_command() -> None:
    line = format_tool_line(name="Bash", tool_input={"command": "uv run pytest"}, verbose=1)
    assert line == "💻 Bash: uv run pytest"


def test_format_tool_line_edit_and_unknown_tool() -> None:
    assert format_tool_line(name="Edit", tool_input={"file_path": "x/y.md"}, verbose=1) == (
        "✏️ Edit: y.md"
    )
    assert format_tool_line(name="Mystery", tool_input={}, verbose=1) == "🔧 Mystery"


def test_format_tool_line_verbose_two_includes_full_input() -> None:
    line = format_tool_line(name="Grep", tool_input={"pattern": "foo", "path": "src"}, verbose=2)
    assert line is not None
    assert line.startswith("🔍 Grep: foo")
    assert '"path": "src"' in line


def test_format_tool_line_truncates_long_commands() -> None:
    line = format_tool_line(name="Bash", tool_input={"command": "x" * 1000}, verbose=1)
    assert line is not None and len(line) < 300 and line.endswith("…")


def test_extract_choices_strips_markers_and_returns_labels() -> None:
    text = "Which one?\n\n[[option: Use SQLite]]\n[[option:  Use Postgres ]]"
    assert extract_choices(text) == ("Which one?", ["Use SQLite", "Use Postgres"])


def test_extract_choices_without_markers_returns_text_unchanged() -> None:
    assert extract_choices("Plain answer [[not an option]]") == (
        "Plain answer [[not an option]]",
        [],
    )


def test_extract_choices_caps_number_of_options() -> None:
    text = "\n".join(f"[[option: o{i}]]" for i in range(20))
    _, options = extract_choices(text)
    assert len(options) == MAX_CHOICES


def test_format_approval_request_escapes_html_and_shows_command() -> None:
    text = format_approval_request(
        project="alpha", tool_name="Bash", tool_input={"command": "echo <x> && rm a"}
    )
    assert "alpha" in text
    assert "<b>Bash</b>" in text
    assert "echo &lt;x&gt; &amp;&amp; rm a" in text


def test_format_approval_request_edit_shows_diff_snippets() -> None:
    text = format_approval_request(
        project="alpha",
        tool_name="Edit",
        tool_input={"file_path": "/s/alpha/a.py", "old_string": "x = 1", "new_string": "x = 2"},
    )
    assert "/s/alpha/a.py" in text and "- x = 1" in text and "+ x = 2" in text


def test_render_progress_keeps_most_recent_lines_within_limit() -> None:
    lines = [f"💻 Bash: step {i}" for i in range(500)]
    rendered = render_progress(header="⏳ Working", lines=lines, limit=300)
    assert len(rendered) <= 300
    assert rendered.startswith("⏳ Working")
    assert rendered.endswith("step 499")


def test_describe_event_hides_everything_at_verbose_zero() -> None:
    assert describe_event(TextEvent(text="hi"), verbose=0) is None
    assert describe_event(ToolUseEvent(tool_use_id="t", name="Bash", input={}), verbose=0) is None


def test_describe_event_renders_text_tools_and_errors() -> None:
    assert describe_event(TextEvent(text=" Checking "), verbose=1) == "💬 Checking"
    tool = ToolUseEvent(tool_use_id="t", name="Bash", input={"command": "ls"})
    assert describe_event(tool, verbose=1) == "💻 Bash: ls"
    denied = ToolResultEvent(tool_use_id="t", is_error=True, content="denied by user")
    assert describe_event(denied, verbose=1) == "⚠️ denied by user"


def test_describe_event_skips_successful_tool_results() -> None:
    ok = ToolResultEvent(tool_use_id="t", is_error=False, content="file content")
    assert describe_event(ok, verbose=2) is None


def test_describe_event_truncates_text_more_at_verbose_one() -> None:
    long_text = TextEvent(text="x" * 5000)
    short = describe_event(long_text, verbose=1)
    full = describe_event(long_text, verbose=2)
    assert short is not None and full is not None and len(short) < len(full)
