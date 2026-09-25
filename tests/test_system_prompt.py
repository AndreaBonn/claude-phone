import re

from src.claude_command import SYSTEM_PROMPT_PATH
from src.message_formatter import extract_choices, extract_files

_EXAMPLE = re.compile(r"<example>\n(.*?)\n</example>", re.DOTALL)


def _examples() -> list[str]:
    return _EXAMPLE.findall(SYSTEM_PROMPT_PATH.read_text(encoding="utf-8"))


def test_system_prompt_examples_parse_into_buttons_and_attachments() -> None:
    choice_example, file_example = _examples()

    _, labels = extract_choices(choice_example)
    _, files = extract_files(file_example)

    assert labels == ["SQLite", "PostgreSQL"]
    assert files == ["reports/sales-summary.pdf"]


def test_control_lines_before_closing_lines_are_still_extracted() -> None:
    # The prompt lets Claude place control lines anywhere, because a user config
    # may prescribe its own closing lines at the end of every message.
    answer = "Report ready.\n\n[[file: out/report.pdf]]\n\nNext: review page 2"

    text, files = extract_files(answer)

    assert files == ["out/report.pdf"]
    assert "[[file:" not in text
    assert text.endswith("Next: review page 2")
