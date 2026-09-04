from fastapi import Request

from app.contexts.platform import ManageFiles
from app.api.dependencies.container import get_application_container


def get_file_use_cases(request: Request) -> ManageFiles:
    return get_application_container(request).files
