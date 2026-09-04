from fastapi import Request

from app.contexts.platform import ManageSystemConfigs, QuerySystemStatus
from app.api.dependencies.container import get_application_container


def get_system_queries(request: Request) -> QuerySystemStatus:
    return get_application_container(request).system


def get_system_config_use_cases(request: Request) -> ManageSystemConfigs:
    return get_application_container(request).system_configs
