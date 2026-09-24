from src.telegram_text import TELEGRAM_MAX_LENGTH, markdown_to_telegram_html, split_message


def test_split_message_keeps_short_text_whole() -> None:
    assert split_message("hello\nworld", limit=100) == ["hello\nworld"]


def test_split_message_respects_limit_on_plain_text() -> None:
    text = "\n".join(f"line {i:04d}" for i in range(1000))
    chunks = split_message(text, limit=500)
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert "\n".join(chunks) == text


def test_split_message_hard_wraps_a_single_huge_line() -> None:
    chunks = split_message("x" * 1200, limit=500)
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert "".join(chunks) == "x" * 1200


def test_split_message_never_cuts_a_code_block_open() -> None:
    code = "\n".join(f"print({i})" for i in range(200))
    text = f"Intro\n```python\n{code}\n```\nOutro"
    chunks = split_message(text, limit=400)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 400
        assert chunk.count("```") % 2 == 0
    assert chunks[1].startswith("```python")


def test_split_message_default_limit_fits_telegram() -> None:
    chunks = split_message("a" * 20000)
    assert all(len(chunk) <= TELEGRAM_MAX_LENGTH for chunk in chunks)


def test_split_message_drops_empty_input() -> None:
    assert split_message("   ") == []


def test_markdown_to_html_escapes_and_formats() -> None:
    html = markdown_to_telegram_html("# Title\nUse **bold** and `a<b>` & more")
    assert html == "<b>Title</b>\nUse <b>bold</b> and <code>a&lt;b&gt;</code> &amp; more"


def test_markdown_to_html_renders_code_block_with_language() -> None:
    html = markdown_to_telegram_html("See:\n```py\nx = 1 < 2\n```\nok")
    assert html == 'See:\n<pre><code class="language-py">x = 1 &lt; 2</code></pre>\nok'


def test_markdown_to_html_does_not_format_inside_code() -> None:
    assert markdown_to_telegram_html("```\n**not bold**\n```") == "<pre>**not bold**</pre>"
