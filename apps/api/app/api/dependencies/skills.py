from fastapi import Request

from app.contexts.catalog import ManageSkills
from app.api.dependencies.container import get_application_container


def get_skill_use_cases(request: Request) -> ManageSkills:
    return get_application_container(request).skills
