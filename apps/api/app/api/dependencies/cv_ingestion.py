from fastapi import Request

from app.api.dependencies.container import get_application_container
from app.contexts.cv_ingestion import CVIngestionUseCases


def get_cv_ingestion_use_cases(request: Request) -> CVIngestionUseCases:
    return get_application_container(request).cv_ingestion
