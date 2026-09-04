from fastapi import Request

from app.contexts.governance_feedback import GovernanceHandlers
from app.api.dependencies.container import get_application_container


def get_governance_handlers(request: Request) -> GovernanceHandlers:
    return get_application_container(request).governance
