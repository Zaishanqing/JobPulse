from typing import Literal

from pydantic import BaseModel, Field


UserRole = Literal[
    "personal_user",
    "enterprise_user",
    "admin",
    "reviewer",
    "developer",
]
PublicUserRole = Literal["personal_user", "enterprise_user", "admin"]


class PublicRegisterRequest(BaseModel):
    role: PublicUserRole
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    email: str | None = None
    phone: str | None = None


# Backwards-compatible Python import name; the public HTTP contract is restricted.
RegisterRequest = PublicRegisterRequest


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class AccountRoleChangeRequest(BaseModel):
    """Stable request contract for account role administration."""

    role: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CurrentUserResponse(BaseModel):
    user_id: str
    role: UserRole
    username: str
    email: str | None = None
    phone: str | None = None
    is_active: bool
    permissions: list[str] = Field(default_factory=list)
