import html
import re

TELEGRAM_MAX_LENGTH = 4096
# Leaves headroom for the growth caused by HTML escaping of the chunk.
DEFAULT_CHUNK_LENGTH = 3500
FENCE = "```"
_FENCE_LINE = re.compile(r"^\s*```(\S*)")
_CODE_BLOCK = re.compile(r"```([\w+-]*)\n?(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"(`[^`\n]+`)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)


def _hard_wrap(line: str, width: int) -> list[str]:
    if len(line) <= width:
        return [line]
    return [line[i : i + width] for i in range(0, len(line), width)]


class _ChunkBuilder:
    """Accumulates lines into chunks, re-opening a code fence across a cut."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.chunks: list[str] = []
        self.lines: list[str] = []
        self.fence_lang: str | None = None

    def _size(self) -> int:
        return sum(len(line) + 1 for line in self.lines)

    def add(self, piece: str) -> None:
        reserve = len(FENCE) + 1 if self.fence_lang is not None else 0
        if self.lines and self._size() + len(piece) + reserve > self.limit:
            self.cut()
        self.lines.append(piece)

    def cut(self) -> None:
        if self.fence_lang is not None:
            self.lines.append(FENCE)
        self.chunks.append("\n".join(self.lines))
        self.lines = [FENCE + self.fence_lang] if self.fence_lang is not None else []

    def toggle_fence(self, line: str) -> None:
        match = _FENCE_LINE.match(line)
        if match:
            self.fence_lang = None if self.fence_lang is not None else match.group(1)

    def finish(self) -> list[str]:
        self.chunks.append("\n".join(self.lines))
        return [chunk for chunk in self.chunks if chunk.strip()]


def split_message(text: str, limit: int = DEFAULT_CHUNK_LENGTH) -> list[str]:
    """Split text into chunks of at most `limit` characters.

    Cuts happen on line boundaries; a cut inside a fenced code block closes the
    fence and re-opens it, with the same language, in the next chunk.
    """
    builder = _ChunkBuilder(limit)
    wrap_width = limit - 2 * (len(FENCE) + 1) - len("python")
    for line in text.split("\n"):
        for piece in _hard_wrap(line, wrap_width):
            builder.add(piece)
        builder.toggle_fence(line)
    return builder.finish()


def _inline_to_html(text: str) -> str:
    parts = _INLINE_CODE.split(text)
    rendered = []
    for index, part in enumerate(parts):
        if index % 2:
            rendered.append(f"<code>{html.escape(part[1:-1])}</code>")
        else:
            escaped = _HEADING.sub(r"<b>\1</b>", html.escape(part))
            rendered.append(_BOLD.sub(r"<b>\1</b>", escaped))
    return "".join(rendered)


def markdown_to_telegram_html(text: str) -> str:
    """Convert the Markdown subset Claude uses most into Telegram HTML."""
    rendered = []
    position = 0
    for match in _CODE_BLOCK.finditer(text):
        rendered.append(_inline_to_html(text[position : match.start()]))
        lang = match.group(1)
        code = html.escape(match.group(2).rstrip("\n"))
        if lang:
            rendered.append(f'<pre><code class="language-{lang}">{code}</code></pre>')
        else:
            rendered.append(f"<pre>{code}</pre>")
        position = match.end()
    rendered.append(_inline_to_html(text[position:]))
    return "".join(rendered)
