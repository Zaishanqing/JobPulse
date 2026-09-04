from fastapi import Request

from app.contexts.talent_acquisition import ManageCandidates
from app.api.dependencies.container import get_application_container


def get_candidate_use_cases(request: Request) -> ManageCandidates:
    return get_application_container(request).candidates
