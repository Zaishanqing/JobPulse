from fastapi import Request

from app.application.handlers import ApplicationHandlers


def get_application_handlers(request: Request) -> ApplicationHandlers:
    return request.state.application_handlers
