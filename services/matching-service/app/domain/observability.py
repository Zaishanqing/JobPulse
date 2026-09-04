"""Immutable health contracts shared by API and infrastructure."""

from __future__ import annotations

from typing import Literal

from app.domain.profiles import ImmutableDTO


class ComponentHealth(ImmutableDTO):
    component: Literal[
        "postgresql",
        "redis",
        "embedding",
        "vector",
        "sparse",
        "reranker",
        "oidc",
        "cv_profile",
        "position_profile",
        "knowledge_graph",
        "cv_authorization",
        "application_grant",
        "responsibility_ce",
    ]
    provider: str
    status: Literal["ready", "disabled", "unavailable"]
    required: bool
    error_code: str | None = None
    artifact_digest: str | None = None


class ReadinessReport(ImmutableDTO):
    status: Literal["ready", "not_ready"]
    components: tuple[ComponentHealth, ...]
