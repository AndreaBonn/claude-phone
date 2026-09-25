# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- GitHub Actions pipeline: ruff, mypy, pytest with branch coverage and `pip-audit` on the locked runtime dependencies.
- Project metadata in `pyproject.toml`, a changelog of the system prompt versions, this changelog.

### Changed

- SECURITY documents that attachments reach the chat without approval.

## [0.1.0] - 2026-09-25

First version.

### Added

- Telegram bot, long polling only, restricted to a whitelist of user IDs checked before every other handler.
- One long-lived `claude -p` process per project over stream-json, turns serialized, idle timeout paused while an approval is pending, session resume per project and per Claude profile.
- Permission gate: a `PreToolUse` hook (matcher `*`) asks an approval broker over a 0600 Unix socket; the policy allows read-only tools, asks for every other tool (MCP included) and blocks paths outside the sandbox roots. Fail-closed on every error path.
- Sandbox made of several roots with the bridge folder carved out; symlinks resolved before the check.
- Approval buttons: approve, deny, deny and stop, and "approve always" for one exact Bash command or one non-Bash tool, in memory only.
- `/stop` and a stop button that terminate the running turn; `/new`, `/switch`, `/profile` for sessions, projects and Claude profiles (cloak layout).
- Choice buttons through `[[option: ...]]` lines and file attachments through `[[file: ...]]` lines, plus automatic attachment of deliverables written in the turn.
- SQLite store for sessions, user state, audit log and open approvals; prompts left open by a previous run are closed at startup.
- Rotating log file with redaction of the bot token and API key, tracebacks included.
- Manual `start.sh` and `stop.sh`, and a systemd user unit that cannot be enabled.
- README in English and Italian, SECURITY policy with threat model, Apache-2.0 license.

### Security

- The bot token never reaches the Claude child environment; the API key is passed only when configured; configuration errors are printed without pydantic input values.
- Database, logs and socket are created readable by the owner only.

[Unreleased]: https://github.com/AndreaBonn/claude-phone/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AndreaBonn/claude-phone/releases/tag/v0.1.0
