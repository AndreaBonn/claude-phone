# System prompt changelog

The bridge appends one of these files to Claude's system prompt (`src/claude_command.py`, `SYSTEM_PROMPT_PATH`). Only the latest version is in use; older ones are kept to compare behaviour across versions.

A resumed session keeps the prompt it was created with (`--resume` ignores a new `--append-system-prompt`, measured on Claude Code 2.1.281), so a new version reaches only sessions opened after it, for example with `/new`.

## v3 (2026-09-25, `9a5fdd4`), in use

- Control lines (`[[option: ...]]`, `[[file: ...]]`) may appear anywhere in the message, each on its own line, and are written in addition to any closing lines prescribed by the user's own configuration. v2 asked for them at the end, and a user config with its own closing lines displaced them.
- Attachments are also requested when Claude mentions a file the user will want to open, since a path in prose is useless on a phone.
- Explains a sandbox block: it cannot be approved by anyone, so Claude must stay inside the project or say what it needed.
- Describes the Markdown subset the bridge renders (bold, inline code, fenced code, headings) and replaces tables with lists.
- Examples wrapped in `<example>` tags.

## v2 (2026-09-25, `7a11e7a`)

- Adds the `[[file: path]]` attachment syntax, with limits (50 MB each, 10 per message, inside the project roots) and preferred phone-friendly formats.
- States that the user cannot open files on the computer, only Telegram attachments.

## v1 (2026-09-24, `e5f4746`)

- First version: role, phone-screen context, denied tool calls as deliberate decisions, `[[option: label]]` choice buttons replacing the unavailable AskUserQuestion tool.
