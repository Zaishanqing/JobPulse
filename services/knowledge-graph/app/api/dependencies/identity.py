from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.application.identity import (
    AuthenticationFailed,
    AuthorizationDenied,
    IdentityService,
)
from app.domain.identity import IdentityActor, Permission


bearer = HTTPBearer(auto_error=False)


def get_identity_service(request: Request) -> IdentityService:
    return request.state.identity_service


def current_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    identities: IdentityService = Depends(get_identity_service),
) -> IdentityActor:
    if credentials is None:
        raise HTTPException(401, "missing bearer token")
    try:
        return identities.current_actor(credentials.credentials)
    except AuthenticationFailed as exc:
        raise HTTPException(401, str(exc)) from exc


def _permission_dependency(
    permission: Permission,
) -> Callable[..., IdentityActor]:
    def check(
        actor: IdentityActor = Depends(current_actor),
        identities: IdentityService = Depends(get_identity_service),
    ) -> IdentityActor:
        try:
            return identities.authorize(actor, permission)
        except AuthorizationDenied as exc:
            raise HTTPException(403, str(exc)) from exc

    return check


require_graph_editor = _permission_dependency(Permission.GRAPH_EDIT)
require_reviewer = _permission_dependency(Permission.REVIEW)
require_publisher = _permission_dependency(Permission.PUBLISH)
require_internal_reader = _permission_dependency(Permission.INTERNAL_READ)
