import asyncio
import contextlib
import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from src.permission_policy import GateAction, GatePolicy, classify_tool_call

logger = logging.getLogger(__name__)

REQUEST_LINE_LIMIT = 16 * 1024 * 1024
SOCKET_MODE = 0o600


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    # Approve now and grant the project the same exact Bash command, or the whole
    # non-Bash tool, until the grants are revoked (see grant_key).
    APPROVE_ALWAYS = "always"
    DENY = "deny"
    DENY_AND_STOP = "stop"


@dataclass
class ApprovalRequest:
    approval_id: str
    project: str
    tool_name: str
    tool_input: dict[str, Any]
    warning: str = ""
    # Created on the running loop: requests only exist inside the broker's event loop.
    future: asyncio.Future[ApprovalDecision] = field(
        repr=False, default_factory=lambda: asyncio.get_running_loop().create_future()
    )


class ApprovalPresenter(Protocol):
    """Shows approval prompts to the user; implemented by the Telegram layer."""

    async def show(self, request: ApprovalRequest) -> None: ...

    async def close(
        self, request: ApprovalRequest, decision: ApprovalDecision, note: str
    ) -> None: ...


AuditCallback = Callable[[str, str, dict[str, Any], str, str], None]
StopCallback = Callable[[str], None]
EXPIRED_NOTE = "⏱️ Tempo scaduto: azione negata"
CANCELLED_NOTE = "🚫 Richiesta annullata"


def grant_key(tool_name: str, tool_input: dict[str, Any]) -> str:
    """What an "approve always" covers: one exact Bash command, or a whole other tool.

    Bash is never granted as a tool: `npm test` must not pre-approve `rm -rf`.
    """
    if tool_name == "Bash":
        return f"Bash: {tool_input.get('command', '')}"
    return tool_name


def _response(decision: str, reason: str = "", stop: bool = False) -> dict[str, Any]:
    return {"decision": decision, "reason": reason, "stop": stop}


class ApprovalBroker:
    """Server side of the permission gate.

    Listens on a Unix socket for requests sent by `permission_hook.py`, applies
    the gate policy and, for risky tools, parks the hook until the user answers
    on Telegram or the approval timeout expires (then the call is denied).
    """

    def __init__(
        self,
        policy: GatePolicy,
        presenter: ApprovalPresenter,
        timeout_seconds: float,
        audit: AuditCallback | None = None,
        on_stop: StopCallback | None = None,
    ) -> None:
        self._policy = policy
        self._presenter = presenter
        self._timeout = timeout_seconds
        self._audit = audit
        self._on_stop = on_stop
        self._pending: dict[str, ApprovalRequest] = {}
        # project -> grant keys; in memory only, so a restart revokes everything.
        self._grants: dict[str, set[str]] = {}
        self._server: asyncio.AbstractServer | None = None
        self._socket_path: Path | None = None

    def pending(self, project: str | None = None) -> list[ApprovalRequest]:
        return [r for r in self._pending.values() if project is None or r.project == project]

    def grants(self, project: str) -> list[str]:
        return sorted(self._grants.get(project, set()))

    def revoke_grants(self, project: str | None) -> None:
        """Forget the "approve always" answers of one project, or of all."""
        if project is None:
            self._grants.clear()
        else:
            self._grants.pop(project, None)

    async def start(self, socket_path: Path) -> None:
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(
            self._on_connection, path=str(socket_path), limit=REQUEST_LINE_LIMIT
        )
        socket_path.chmod(SOCKET_MODE)
        self._socket_path = socket_path

    async def stop(self) -> None:
        await self.cancel(project=None)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self._socket_path is not None:
            self._socket_path.unlink(missing_ok=True)

    def resolve(self, approval_id: str, decision: ApprovalDecision) -> ApprovalRequest | None:
        """Deliver the user's answer; None if the request expired or is unknown."""
        request = self._pending.get(approval_id)
        if request is None or request.future.done():
            return None
        request.future.set_result(decision)
        return request

    async def cancel(self, project: str | None) -> None:
        """Deny every pending request (of one project, or all) and close its prompt."""
        for request in self.pending(project):
            if self.resolve(request.approval_id, ApprovalDecision.DENY) is not None:
                await self._close_prompt(request, ApprovalDecision.DENY, CANCELLED_NOTE)

    async def _on_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            payload = json.loads(await reader.readline())
            response = await self.handle_request(payload)
        except Exception as exc:  # socket boundary: never leave the hook without an answer
            logger.exception("Gate request failed")
            response = _response("deny", f"Errore interno del gate: {exc!r}")
        with contextlib.suppress(ConnectionError):
            writer.write(json.dumps(response).encode() + b"\n")
            await writer.drain()
        writer.close()

    async def handle_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        project = str(payload.get("project") or "?")
        tool = str(payload.get("tool_name") or "?")
        tool_input = payload.get("tool_input") or {}
        # A request without cwd resolves to "/", outside the sandbox: blocked.
        cwd = Path(str(payload.get("cwd") or "/"))
        verdict = classify_tool_call(
            tool_name=tool, tool_input=tool_input, cwd=cwd, policy=self._policy
        )
        if verdict.action is GateAction.ALLOW:
            self._record(project, tool, tool_input, "auto-approved", "")
            return _response("allow", "Auto-approvato")
        if verdict.action is GateAction.BLOCK:
            self._record(project, tool, tool_input, "blocked", verdict.reason)
            return _response("deny", verdict.reason)
        # After the sandbox check: a grant never lets a path escape it.
        key = grant_key(tool, tool_input)
        if key in self._grants.get(project, set()):
            self._record(project, tool, tool_input, "session-approved", "")
            return _response("allow", "Approvato per la sessione")
        decision, note = await self._ask_user(project, tool, tool_input, verdict.reason)
        self._record(project, tool, tool_input, decision.value, note)
        if decision is ApprovalDecision.APPROVE_ALWAYS:
            self._grants.setdefault(project, set()).add(key)
        return self._decision_response(project, decision, note)

    def _decision_response(
        self, project: str, decision: ApprovalDecision, note: str
    ) -> dict[str, Any]:
        if decision in (ApprovalDecision.APPROVE, ApprovalDecision.APPROVE_ALWAYS):
            return _response("allow", "Approvato dall'utente su Telegram")
        if decision is ApprovalDecision.DENY_AND_STOP:
            if self._on_stop is not None:
                self._on_stop(project)
            return _response("deny", "L'utente ha negato e fermato la sessione", stop=True)
        return _response("deny", note or "L'utente ha negato l'azione su Telegram")

    async def _ask_user(
        self, project: str, tool: str, tool_input: dict[str, Any], warning: str
    ) -> tuple[ApprovalDecision, str]:
        request = ApprovalRequest(secrets.token_hex(4), project, tool, tool_input, warning)
        self._pending[request.approval_id] = request
        try:
            await self._presenter.show(request)
            decision = await asyncio.wait_for(request.future, timeout=self._timeout)
            return decision, ""
        except TimeoutError:
            await self._close_prompt(request, ApprovalDecision.DENY, EXPIRED_NOTE)
            return ApprovalDecision.DENY, f"Nessuna risposta entro {self._timeout:.0f}s: negato"
        except Exception:
            logger.exception("Could not show approval request %s", request.approval_id)
            return ApprovalDecision.DENY, "Impossibile mostrare la richiesta su Telegram"
        finally:
            self._pending.pop(request.approval_id, None)

    async def _close_prompt(
        self, request: ApprovalRequest, decision: ApprovalDecision, note: str
    ) -> None:
        try:
            await self._presenter.close(request, decision, note)
        except Exception:
            logger.exception("Could not update approval prompt %s", request.approval_id)

    def _record(
        self, project: str, tool: str, tool_input: dict[str, Any], outcome: str, reason: str
    ) -> None:
        if self._audit is not None:
            self._audit(project, tool, tool_input, outcome, reason)
