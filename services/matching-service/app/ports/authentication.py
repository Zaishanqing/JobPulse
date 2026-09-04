"""Authentication boundary owned by the application."""

from __future__ import annotations

from typing import Protocol

from app.domain.auth import AuthContext


class AuthenticationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AuthenticationProvider(Protocol):
    def authenticate(self, credential: str) -> AuthContext: ...
