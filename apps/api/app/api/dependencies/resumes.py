from fastapi import Request

from app.contexts.talent_acquisition import ManageResumes
from app.api.dependencies.container import get_application_container


def get_resume_use_cases(request: Request) -> ManageResumes:
    return get_application_container(request).resumes
