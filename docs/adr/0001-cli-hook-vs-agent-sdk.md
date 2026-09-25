# ADR 0001: drive the Claude Code CLI directly and gate tools with a PreToolUse hook

- Status: accepted
- Date: 2026-09-25

## Context

The bridge must let a phone drive Claude Code on the user's PC while every tool call Claude makes passes through an approval the user answers on Telegram. Three requirements shape the integration:

1. Use the Claude Code installed on the PC and its subscription login, not pay-per-use API billing.
2. Load the user's full Claude configuration (rules, skills, hooks, plugins, MCP servers, cloak profiles), so a session from the phone behaves like one in the terminal.
3. Gate **every** tool call, including the ones the user's own settings already allow. A rule in `permissions.allow` written for terminal use must not become an unattended action from a phone.

Two ways to integrate were considered.

**A. The Claude Agent SDK for Python.** It runs the `claude` CLI as a subprocess over stream-json and adds a typed control protocol. It offers two permission mechanisms:

- `can_use_tool`, an async callback. It is invoked only when the CLI's permission rules evaluate to "ask", not for calls already permitted by `allowed_tools`, the permission mode or `permissions.allow` rules ([permissions reference](https://github.com/anthropics/claude-agent-sdk-python/blob/main/_autodocs/api-reference/permissions.md)). With the user's settings loaded, requirement 3 fails.
- In-process `PreToolUse` hook callbacks (`HookMatcher`), which run before the permission rules ([README](https://github.com/anthropics/claude-agent-sdk-python/blob/main/README.md)). These would meet requirement 3.

**B. The CLI directly** (`claude -p --input-format stream-json --output-format stream-json`), with a `PreToolUse` hook (matcher `*`) injected through `--settings`. The hook is a stdlib-only script that asks the bot's approval broker over a 0600 Unix socket and denies on any error.

## Decision

Option B.

Option A with in-process hooks would satisfy the requirements too; B was chosen because at this size it costs less and exposes more:

- **No extra dependency on the security path.** The gate is one stdlib script plus the broker; nothing between the CLI and the decision is third-party code the project does not control.
- **The user's binary, not a bundled one.** The SDK ships its own CLI build by default; the bridge runs whatever `claude` the user keeps logged in and updated, which is also what `start.sh` checks with `claude auth status`.
- **Fail-closed by construction.** The hook is a separate process the CLI must hear back from: if the bot dies, the socket is gone and every tool call is denied. The failure mode does not depend on how a library handles a dead callback.
- **Shared sessions with the terminal.** Session ids and transcripts live in the same profile directory the terminal uses, so `--resume` continues the same conversation.

## Consequences

- The bridge depends on CLI behaviour that is not a published contract: the stream-json event shapes, the hook input and output, `--resume` semantics. The SDK has the same dependency but keeps it maintained upstream; here it is the project's job. The facts the code relies on are measured and listed with the CLI version in `CLAUDE.md` (Invariants).
- These facts are exercised only against `tests/fake_claude.py`. A smoke test against the real binary, to run after each CLI upgrade, is still to be written (audit item F4b).
- A newer CLI that changes the protocol breaks the bridge until the parser follows. Migrating to option A with `PreToolUse` callbacks remains possible: the policy (`permission_policy.classify_tool_call`) and the broker do not depend on the transport.
