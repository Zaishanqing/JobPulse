"""Authenticated identities and deterministic access-domain derivation."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.domain.profiles import ImmutableDTO

PERSONAL_ROLES = frozenset({"candidate", "user"})
ENTERPRISE_ROLES = frozenset({"enterprise", "recruiter"})
SERVICE_ROLES = frozenset({"matching.service", "matching.worker"})


def derive_tenant_ref(tenant_id: str) -> str:
    return tenant_id


def derive_subject_ref(subject_id: str) -> str:
    return subject_id


class AuthContext(ImmutableDTO):
    subject_id: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=200)
    roles: frozenset[str] = Field(min_length=1)
    access_scope: str = Field(min_length=1, max_length=700)
    token_id: str = Field(min_length=1, max_length=200)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        return value


def derive_access_scope(subject_id: str, tenant_id: str, roles: frozenset[str]) -> str:
    """Derive the sole data partition from authenticated, validated claims."""
    tenant_ref = derive_tenant_ref(tenant_id)
    subject_ref = derive_subject_ref(subject_id)
    if roles & SERVICE_ROLES:
        return f"service:{tenant_ref}:{subject_ref}"
    if roles & ENTERPRISE_ROLES:
        return f"tenant:{tenant_ref}"
    if roles & PERSONAL_ROLES:
        return f"user:{tenant_ref}:{subject_ref}"
    raise ValueError("identity has no supported access role")
