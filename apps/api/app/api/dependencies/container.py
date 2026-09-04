from fastapi import Request

from app.application_container import ApplicationContainer


def get_application_container(request: Request) -> ApplicationContainer:
    container = getattr(request.app.state, "container", None)
    if not isinstance(container, ApplicationContainer):
        raise RuntimeError("Application container is unavailable")
    return container
