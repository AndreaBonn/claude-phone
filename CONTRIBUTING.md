# Contributing

Issues and pull requests are welcome. Security problems go through the private channel described in [SECURITY.md](./SECURITY.md), not through public issues.

## Setup

```bash
uv sync
cp .env.example .env   # only needed to run the bot, not the tests
```

The tests never read your `.env`: they use a fake `claude` binary (`tests/fake_claude.py`) and a fake Telegram bot (`tests/fakes.py`), so no token and no Claude login are needed.

## Checks

Every pull request must pass the same checks the CI runs:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
uv run pytest --cov=src --cov-branch --cov-report=term-missing
```

New behaviour comes with a test that fails without it. Branch coverage is currently 99%; keep uncovered lines deliberate.

## Invariants of the permission gate

A change that weakens any of these will not be merged. The full list is in [CLAUDE.md](./CLAUDE.md#invariants).

- No `--dangerously-skip-permissions` or `bypassPermissions`, not even commented out.
- The gate is fail-closed: every error path in the hook or the broker denies.
- A tool not listed in the auto-approve sets asks the user; nothing falls through to Claude's own permission rules.
- Sandbox violations are blocked before auto-approval, grants and the user's approval.
- `src/permission_hook.py` stays stdlib-only and never imports `src`.

## Commits and pull requests

- [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): description`, imperative, first line within 72 characters. Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`, `perf`.
- One logical change per commit; the body explains why, the diff shows what.
- Update [CHANGELOG.md](./CHANGELOG.md) under `Unreleased` for user-visible changes.
- A change to the system prompt is a new file (`prompts/telegram-bridge-system-vN.md`) plus an entry in [prompts/CHANGELOG.md](./prompts/CHANGELOG.md), never an edit of a published version.
