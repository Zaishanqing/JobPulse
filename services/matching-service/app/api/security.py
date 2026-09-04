"""FastAPI authentication dependency and privacy-safe security auditing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Header, Request

from app.domain.auth import AuthContext, derive_access_scope
from app.ports.authentication import AuthenticationError


def security_audit(
    request: Request,
    *,
    decision: str,
    reason_code: str,
    context: AuthContext | None = None,
) -> None:
    request.app.state.structured_logger.event(
        "security_audit",
        request_id=getattr(request.state, "request_id", None),
        actor_id=None,
        auth_decision=decision,
        error_code=reason_code,
    )


def authenticated_context(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> AuthContext:
    if authorization is None or not authorization.startswith("Bearer "):
        security_audit(
            request, decision="denied", reason_code="AUTHENTICATION_REQUIRED"
        )
        raise AuthenticationError(
            "AUTHENTICATION_REQUIRED", "Bearer authentication is required"
        )
    credential = authorization.removeprefix("Bearer ").strip()
    if not credential:
        raise AuthenticationError(
            "AUTHENTICATION_REQUIRED", "Bearer authentication is required"
        )
    try:
        context = request.app.state.authentication_provider.authenticate(credential)
    except AuthenticationError as exc:
        security_audit(request, decision="denied", reason_code=exc.code)
        raise
    if context.expires_at <= datetime.now(timezone.utc):
        security_audit(request, decision="denied", reason_code="TOKEN_EXPIRED")
        raise AuthenticationError("TOKEN_EXPIRED", "credential has expired")
    try:
        expected_scope = derive_access_scope(
            context.subject_id, context.tenant_id, context.roles
        )
    except ValueError as exc:
        raise AuthenticationError(
            "TOKEN_CLAIMS_INVALID", "credential claims are invalid"
        ) from exc
    if context.access_scope != expected_scope:
        security_audit(request, decision="denied", reason_code="TOKEN_CLAIMS_INVALID")
        raise AuthenticationError(
            "TOKEN_CLAIMS_INVALID", "credential claims are inconsistent"
        )
    request.state.auth_context = context
    security_audit(request, decision="allowed", reason_code="AUTHENTICATED", context=context)
    return context
