from dataclasses import dataclass, field
from typing import Any

from src.config import Settings
from src.permission_gate import ApprovalBroker
from src.project_manager import ProjectManager
from src.session_manager import SessionManager
from src.session_store import SessionStore
from src.telegram_presenter import TelegramApprovalPresenter

BRIDGE_KEY = "bridge"


@dataclass
class BridgeContext:
    """Everything the Telegram handlers need, stored in `application.bot_data`."""

    settings: Settings
    store: SessionStore
    projects: ProjectManager
    sessions: SessionManager
    broker: ApprovalBroker
    presenter: TelegramApprovalPresenter
    # Choice buttons of the latest answer: token -> labels. Cleared at every turn.
    choices: dict[str, list[str]] = field(default_factory=dict)


def get_bridge(context: Any) -> BridgeContext:
    bridge: BridgeContext = context.application.bot_data[BRIDGE_KEY]
    return bridge
