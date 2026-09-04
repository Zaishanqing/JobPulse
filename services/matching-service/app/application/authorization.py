"""Role and access-domain policy shared by HTTP and worker entry points."""

from __future__ import annotations

from app.domain.auth import AuthContext

API_ROLES = frozenset({"candidate", "user", "enterprise", "recruiter", "matching.service"})


class AuthorizationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def require_any_role(context: AuthContext, allowed: frozenset[str]) -> None:
    if not context.roles & allowed:
        raise AuthorizationError("INSUFFICIENT_ROLE", "identity is not permitted")


def request_access_scope(context: AuthContext, asserted_scope: str | None) -> str:
    require_any_role(context, API_ROLES)
    if asserted_scope is not None and asserted_scope != context.access_scope:
        raise AuthorizationError("ACCESS_SCOPE_MISMATCH", "access scope does not match identity")
    return context.access_scope


def require_worker(context: AuthContext) -> None:
    require_any_role(context, frozenset({"matching.worker"}))


def require_service(context: AuthContext) -> None:
    require_any_role(context, frozenset({"matching.service"}))
