from fastapi import Request

from app.contexts.evaluation import ManageEvaluation
from app.api.dependencies.container import get_application_container


def get_evaluation_use_cases(request: Request) -> ManageEvaluation:
    return get_application_container(request).evaluation
