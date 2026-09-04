from fastapi import Request

from app.contexts.governance_feedback import ManageFeedback
from app.api.dependencies.container import get_application_container


def get_feedback_use_cases(request: Request) -> ManageFeedback:
    return get_application_container(request).feedback
