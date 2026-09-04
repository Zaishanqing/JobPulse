from fastapi import Request

from app.api.dependencies.container import get_application_container
from app.contexts.extraction_tasks import ExtractionTaskUseCases, RunPendingExtractionTasks


def get_extraction_task_use_cases(request: Request) -> ExtractionTaskUseCases:
    return get_application_container(request).extraction_tasks


def get_extraction_worker_control(request: Request) -> RunPendingExtractionTasks:
    return get_application_container(request).extraction_worker
