from fastapi import Request

from app.contexts.tasks import ManageTasks
from app.api.dependencies.container import get_application_container


def get_task_use_cases(request: Request) -> ManageTasks:
    return get_application_container(request).tasks
