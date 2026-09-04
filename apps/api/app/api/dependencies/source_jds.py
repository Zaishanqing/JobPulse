from fastapi import Request

from app.api.dependencies.container import get_application_container
from app.contexts.source_jds import SourceJDUseCases


def get_source_jd_use_cases(request: Request) -> SourceJDUseCases:
    return get_application_container(request).source_jds
