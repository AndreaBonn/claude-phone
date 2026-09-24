# Telegram ⇄ Claude Code Bridge

Single-user Telegram bot that drives a local `claude -p` process (stream-json in/out) and gates risky tool calls with inline approval buttons.

## Commands

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format .
uv run mypy src tests
./start.sh [--background]   # manual start only
./stop.sh
```

## Architecture

- `bot.py` builds a `BridgeContext` (store, projects, sessions, broker, presenter) into `application.bot_data`; updates run concurrently so approval callbacks are served while a text handler awaits a turn.
- `claude_session.py`: one long-lived `claude -p --input-format stream-json --output-format stream-json` process per project, turns serialized by an `asyncio.Lock`, stderr always drained, idle timeout paused while an approval is pending.
- Permission gate: `claude` runs `permission_hook.py` (PreToolUse, matcher `*`, injected via `--settings`), which asks `permission_gate.ApprovalBroker` over a 0600 Unix socket. `permission_policy.classify_tool_call` decides allow / ask / block; there is no passthrough, unlisted tools (MCP, WebFetch, NotebookEdit) ask. Sessions run with `--strict-mcp-config`, so the user's MCP servers are never loaded.
- Choice buttons: `AskUserQuestion` does not exist in `-p` mode (measured on Claude Code 2.1.281); `prompts/telegram-bridge-system-v1.md` teaches Claude an `[[option: label]]` line syntax that `message_formatter.extract_choices` turns into buttons.

## Invariants

- Never add `--dangerously-skip-permissions` or `bypassPermissions` anywhere, not even commented out.
- The gate is fail-closed: every error path in the hook or broker must deny.
- Every tool not listed in the auto-approve sets must ask: never fall through to Claude's own permission rules.
- Sandbox violations are blocked before auto-approval and before asking the user.
- `TELEGRAM_BOT_TOKEN` never reaches the claude child env (`claude_session.SECRET_ENV_VARS`) and is redacted by `logging_setup.SecretRedactingFormatter`, tracebacks included. Config errors are printed through `config.format_config_error`, never `str(ValidationError)`.
- `permission_hook.py` is stdlib-only and must not import `src`: it runs inside the project directory, not the bridge's.
- Measured protocol facts: each turn emits one `system/init` and ends with one `result`; `--resume` keeps the session id; a hook reply with `continue: false` ends the turn but keeps the process alive; resuming an unknown session yields a `result` with `num_turns: 0` plus `No conversation found` on stderr, then exit 1.

## Tests

`tests/fake_claude.py` stands in for the `claude` binary (scenarios via `FAKE_SCENARIO`: echo, crash, hang, notfound). `tests/fakes.FakeBot` records Telegram calls. The gate tests run the real hook script against a real broker socket.
