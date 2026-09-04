from fastapi import Request

from app.contexts.catalog import ManagePositions
from app.api.dependencies.container import get_application_container


def get_position_use_cases(request: Request) -> ManagePositions:
    return get_application_container(request).positions
