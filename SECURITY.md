**English** | [Italiano](./SECURITY.it.md)

# Security Policy

## Supported Versions

This project is in active development. Security updates are applied to the latest commit on `main`; tagged releases (from `v0.1.0`) do not receive backported fixes.

## Reporting a Vulnerability

To report a security vulnerability, use [GitHub Security Advisories](https://github.com/AndreaBonn/claude-phone/security/advisories/new). Do not open a public issue.

Please include:

- Description of the vulnerability
- Steps to reproduce
- Expected vs actual behavior
- Impact assessment (what an attacker could achieve)

Response timeline:

- Acknowledgment: within 72 hours
- Fix for critical issues: within 30 days
- Coordinated public disclosure after the fix is released

## Threat Model

The bridge lets a phone drive a Claude Code process that can run commands on your PC. The assets it protects are the files and the shell of that PC. The two trust boundaries are the Telegram chat (only whitelisted users may talk to the bot) and the tool calls Claude makes (each one passes through the permission gate).

## Security Measures Implemented

- **User whitelist**: every Telegram update is checked against `ALLOWED_USERS` in a handler group that runs before all others; unknown users are dropped and logged (`src/auth.py:29`, `src/bot.py:176`).
- **Permission gate on every tool call**: Claude Code runs a `PreToolUse` hook with matcher `*` that asks the bot's approval broker; tools not in the auto-approve sets always require approval, including MCP tools (`src/permission_policy.py:87`).
- **Fail-closed hook**: any error while reaching the broker denies the tool call (`src/permission_hook.py:64`). An unanswered approval is denied after `APPROVAL_TIMEOUT_SECONDS` (`src/permission_gate.py:201`).
- **Path sandbox**: tool paths are resolved, symlinks included, and checked against the `APPROVED_DIRECTORY` roots; the bridge folder is always excluded, so Claude cannot read or modify the gate that controls it. A sandbox violation is blocked before auto-approval and before asking the user (`src/project_manager.py:30`, `src/permission_policy.py:110`).
- **Private runtime files**: the process sets umask 077, the gate socket is chmod 0600 and the SQLite database 0600 (`src/bot.py:222`, `src/permission_gate.py:118`, `src/session_store.py:81`).
- **Secrets kept from Claude**: `TELEGRAM_BOT_TOKEN` is removed from the environment of the `claude` child process, which could otherwise read it with `env` (`src/claude_command.py:88`).
- **Secret redaction in logs**: a log formatter masks the bot token and the API key, tracebacks included (`src/logging_setup.py:17`). Both are stored as pydantic `SecretStr` (`src/config.py:31`).
- **Audit log**: every message sent to Claude and every gate decision is recorded in the `audit_log` table (`src/session_store.py:158`, `src/turn_runner.py:145`, `src/bot.py:117`).
- **No listening port**: the bot uses Telegram long polling, outbound connections only, and drops updates received while it was off (`src/bot.py:237`).
- **No permission bypass**: `--dangerously-skip-permissions` and `bypassPermissions` appear nowhere in the code.
- **Dependency pinning**: `uv.lock` is committed and `start.sh` installs with `uv sync --frozen`.
- **Continuous integration**: every push and pull request runs ruff, mypy, the test suite with coverage and `pip-audit` on the locked runtime dependencies (`.github/workflows/ci.yml`).

## Known Limitations

- **Bash path check is best-effort**: the bridge scans shell command tokens that look like paths (`/...`, `~`, `..`) and blocks those outside the sandbox (`src/permission_policy.py:64`). A command can still reach outside files in ways a token scan does not see. For Bash, the real protection is your approval: read the command before pressing approve.
- **"Approve always" grants**: once you grant a whole non-Bash tool for the session, further calls of that tool are not shown to you until `/new`, a profile switch or a restart. The sandbox still applies, and so does one exception: `Write`, `Edit`, `MultiEdit` and `NotebookEdit` on files Claude Code or git execute on their own (anything under `.claude/` or `.git/`, `.mcp.json`, `.envrc`) always ask and can never be granted (`src/permission_policy.py:127`). The exception does not cover Bash: a command that writes such a file through a redirect is caught only by your reading of it.
- **The sandbox is per root, not per project**: read-only tools (`Read`, `Grep`, `Glob`) are auto-approved on every project under the `APPROVED_DIRECTORY` roots, not only the active one, `.env` files included (`src/permission_policy.py:100`). A root holding downloaded or third-party content puts untrusted text, a prompt injection vector, in the same perimeter as your work projects: keep such folders out of the roots.
- **Attachments are sent without approval**: a `[[file: path]]` line in Claude's answer, or a deliverable written by an approved `Write`, is uploaded to the chat without asking. Any file inside the sandbox roots can be sent, `.env` files of other projects included, and it is then stored on Telegram's servers like every message of the chat (`src/file_delivery.py:70`). The bridge folder and paths outside the roots are never sent.
- **Your Claude Code configuration is loaded**: rules, hooks, skills, plugins and MCP servers of the chosen profile run inside bridge sessions. A hook in your user settings executes as it would in a terminal session.

## Security Best Practices for Users

- Keep `ALLOWED_USERS` to your own ID and disable group joins in BotFather (`/setjoingroups`).
- Keep `CLAUDE_AUTO_APPROVE_TOOLS` read-only; add MCP tools there only if they are read-only.
- Keep `APPROVED_DIRECTORY` as narrow as your work allows. Do not point it at your home folder.
- Never commit `.env`; it is listed in `.gitignore`.
- Stop the bot with `./stop.sh` when you do not need it.

## Out of Scope

- Vulnerabilities in Claude Code, Telegram or third-party dependencies (report them to their maintainers)
- Attacks that require control of a whitelisted Telegram account
- Actions you approved from the approval buttons
- Social engineering attacks

## Acknowledgments

Security researchers who responsibly disclose vulnerabilities will be listed here.

---

[Back to README](./README.md)
