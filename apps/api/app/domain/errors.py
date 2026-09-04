"""Framework-independent errors shared by domain and application workflows."""

from typing import Any


class DomainError(Exception):
    """Base error that may be translated by an interface adapter."""


class PermissionDenied(DomainError):
    pass


class NoReleasedJDFacts(DomainError):
    pass


class ProjectionConflict(DomainError):
    pass


class ExternalGatewayError(DomainError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 503,
        error_code: str = "external_service_unavailable",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.details = details or {}
