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
- Permission gate: `claude` runs `permission_hook.py` (PreToolUse, matcher `*`, injected via `--settings`), which asks `permission_gate.ApprovalBroker` over a 0600 Unix socket. `permission_policy.classify_tool_call` decides allow / ask / block; there is no passthrough, unlisted tools (MCP, WebFetch, NotebookEdit) ask. The user's full config (rules, skills, hooks, plugins, MCP servers) is loaded on purpose: MCP calls ask, and `Sandbox.read_only` exposes the skill/rule/plugin directories of `~/.claude` and of each profile for reading only (`profiles.READABLE_CONFIG_SUBDIRS`).
- Choice buttons: `AskUserQuestion` does not exist in `-p` mode (measured on Claude Code 2.1.281); `prompts/telegram-bridge-system-v1.md` teaches Claude an `[[option: label]]` line syntax that `message_formatter.extract_choices` turns into buttons.

## Invariants

- Never add `--dangerously-skip-permissions` or `bypassPermissions` anywhere, not even commented out.
- The gate is fail-closed: every error path in the hook or broker must deny.
- Every tool not listed in the auto-approve sets must ask: never fall through to Claude's own permission rules.
- Sandbox violations are blocked before auto-approval and before asking the user. The sandbox is several roots (`APPROVED_DIRECTORY`, comma-separated) minus `PROJECT_ROOT`: the bridge is always carved out. Project ids are `<root name>/<dir>`; project buttons carry a sha256 prefix because ids overflow the 64-byte callback_data, and the list is paginated (Telegram caps inline keyboards at about 100 buttons).
- `TELEGRAM_BOT_TOKEN` never reaches the claude child env (`claude_session.SECRET_ENV_VARS`) and is redacted by `logging_setup.SecretRedactingFormatter`, tracebacks included. Config errors are printed through `config.format_config_error`, never `str(ValidationError)`.
- `permission_hook.py` is stdlib-only and must not import `src`: it runs inside the project directory, not the bridge's.
- Measured protocol facts: each turn emits one `system/init` and ends with one `result`; `--resume` keeps the session id; a hook reply with `continue: false` ends the turn but keeps the process alive; resuming an unknown session yields a `result` with `num_turns: 0` plus `No conversation found` on stderr, then exit 1.

## Claude profiles

A profile is a directory under `CLAUDE_PROFILES_DIR` (cloak layout); `build_env` always sets or removes `CLAUDE_CONFIG_DIR`, so the bot shell's own profile never leaks in. `SessionManager.set_profile` refuses while busy and stops all processes; session ids are stored under `<profile>::<project>` because transcripts live per profile. `claude auth status` reports `loggedIn: true` even with an expired token (measured), so auth failures surface as a 401 result and `turn_runner` appends a re-login hint.

## Tests

`tests/fake_claude.py` stands in for the `claude` binary (scenarios via `FAKE_SCENARIO`: echo, crash, hang, notfound). `tests/fakes.FakeBot` records Telegram calls. The gate tests run the real hook script against a real broker socket.
