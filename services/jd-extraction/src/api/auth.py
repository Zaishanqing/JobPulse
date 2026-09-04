from __future__ import annotations

import hmac

from fastapi import Header, Request

from .errors import APIError


def require_internal_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    expected = request.app.state.settings.internal_token
    if expected is None:
        raise APIError(
            status_code=503,
            error_code="service_not_ready",
            message="Extraction service is not ready.",
        )
    prefix = "Bearer "
    if authorization is None or not authorization.startswith(prefix):
        raise APIError(
            status_code=401,
            error_code="unauthorized",
            message="Bearer authentication is required.",
        )
    supplied = authorization[len(prefix) :]
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise APIError(
            status_code=401,
            error_code="unauthorized",
            message="Bearer authentication failed.",
        )
