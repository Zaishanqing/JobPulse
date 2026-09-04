from fastapi import Request

from app.contexts.talent_acquisition import RecruitmentHandlers
from app.api.dependencies.container import get_application_container


def get_recruitment_handlers(request: Request) -> RecruitmentHandlers:
    return get_application_container(request).recruitment
