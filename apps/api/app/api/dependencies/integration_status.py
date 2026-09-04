from fastapi import Request

from app.api.dependencies.container import get_application_container
from app.contexts.integration_status import QueryIntegrationStatus


def get_integration_status_query(request: Request) -> QueryIntegrationStatus:
    return get_application_container(request).integration_status
