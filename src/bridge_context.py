from dataclasses import dataclass, field
from typing import Any

from src.config import Settings
from src.permission_gate import ApprovalBroker
from src.profiles import ProfileCatalog
from src.project_manager import ProjectManager
from src.session_manager import SessionManager
from src.session_store import SessionStore
from src.telegram_presenter import TelegramApprovalPresenter

BRIDGE_KEY = "bridge"


@dataclass(frozen=True)
class ChoiceSet:
    """Choice buttons under one answer, bound to the project that produced them."""

    project: str
    labels: list[str]


@dataclass
class BridgeContext:
    """Everything the Telegram handlers need, stored in `application.bot_data`."""

    settings: Settings
    store: SessionStore
    projects: ProjectManager
    sessions: SessionManager
    broker: ApprovalBroker
    presenter: TelegramApprovalPresenter
    profiles: ProfileCatalog
    # Open choice buttons: token -> choices. A new turn invalidates its project's ones.
    choices: dict[str, ChoiceSet] = field(default_factory=dict)

    def forget_choices(self, project: str) -> None:
        for token in [t for t, choice in self.choices.items() if choice.project == project]:
            del self.choices[token]


def get_bridge(context: Any) -> BridgeContext:
    bridge: BridgeContext = context.application.bot_data[BRIDGE_KEY]
    return bridge
