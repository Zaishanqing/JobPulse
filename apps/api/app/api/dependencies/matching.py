from fastapi import Request

from app.contexts.matching_learning import ManageLearningPaths, ManageMatching
from app.api.dependencies.container import get_application_container


def get_matching_use_cases(request: Request) -> ManageMatching:
    return get_application_container(request).matching


def get_learning_path_use_cases(request: Request) -> ManageLearningPaths:
    return get_application_container(request).learning_paths
