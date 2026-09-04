from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from app.application.handlers import DiscoveryHandlers


def get_handlers(request: Request) -> DiscoveryHandlers:
    return request.state.discovery_handlers


def require_internal_service(
    authorization: Annotated[str | None, Header()] = None,
    handlers: DiscoveryHandlers = Depends(get_handlers),
) -> None:
    if not handlers.authenticator.verify(authorization):
        raise HTTPException(
            status_code=401,
            detail="valid internal service credential required",
        )
