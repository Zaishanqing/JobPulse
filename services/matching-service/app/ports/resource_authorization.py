"""Trusted resource-authorization ports."""

from __future__ import annotations

from typing import Protocol

from app.domain.auth import AuthContext


class CVAuthorizationPort(Protocol):
    def is_owner(self, context: AuthContext, cv_id: str) -> bool: ...


class ApplicationGrantPort(Protocol):
    def has_active_grant(
        self, context: AuthContext, cv_id: str, position_id: str
    ) -> bool: ...


class EnterpriseJobGrantPort(Protocol):
    def has_active_grant(
        self, context: AuthContext, cv_id: str, position_id: str
    ) -> bool: ...
