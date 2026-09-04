"""Readiness checks for only the dependencies selected by current configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.observability import ComponentHealth, ReadinessReport
from app.domain.vector_contracts import VectorContractViolation
from app.ports.observability import MetricsCollector
from app.ports.task_queue import TaskQueueError


@dataclass(frozen=True)
class DependencySpec:
    component: str
    provider: str
    dependency: Any = None
    configuration_error: str | None = None


class HealthService:
    def __init__(
        self, dependencies: tuple[DependencySpec, ...], metrics: MetricsCollector
    ) -> None:
        self._dependencies = dependencies
        self._metrics = metrics

    def readiness(self) -> ReadinessReport:
        components = tuple(self._check(item) for item in self._dependencies)
        status = (
            "ready"
            if all(item.status != "unavailable" for item in components)
            else "not_ready"
        )
        return ReadinessReport(status=status, components=components)

    def _check(self, spec: DependencySpec) -> ComponentHealth:
        external = spec.provider in {"postgres", "redis", "http", "oidc", "model"}
        if spec.configuration_error is not None:
            self._metrics.increment(
                "matching_dependency_errors_total", component=spec.component
            )
            return ComponentHealth(
                component=spec.component,
                provider=spec.provider,
                status="unavailable",
                required=True,
                error_code=spec.configuration_error,
            )
        if not external:
            return ComponentHealth(
                component=spec.component,
                provider=spec.provider,
                status="disabled",
                required=False,
            )
        try:
            spec.dependency.check_health()
        except (VectorContractViolation, TaskQueueError) as exc:
            return self._unavailable(spec, exc.code)
        except Exception:
            fallback_codes = {
                "postgresql": "POSTGRES_UNAVAILABLE",
                "redis": "TASK_QUEUE_UNAVAILABLE",
                "embedding": "EMBEDDING_UNAVAILABLE",
                "vector": "VECTOR_UNAVAILABLE",
                "sparse": "SPARSE_RETRIEVAL_UNAVAILABLE",
                "reranker": "RERANKER_UNAVAILABLE",
                "oidc": "AUTHENTICATION_UNAVAILABLE",
                "cv_profile": "CV_PROFILE_UPSTREAM_UNAVAILABLE",
                "position_profile": "POSITION_PROFILE_UPSTREAM_UNAVAILABLE",
                "knowledge_graph": "GRAPH_UPSTREAM_UNAVAILABLE",
                "cv_authorization": "CV_AUTHORIZATION_UNAVAILABLE",
                "application_grant": "APPLICATION_GRANT_UNAVAILABLE",
                "responsibility_ce": "RESPONSIBILITY_CE_UNAVAILABLE",
            }
            return self._unavailable(
                spec,
                fallback_codes.get(
                    spec.component, f"{spec.component.upper()}_UNAVAILABLE"
                ),
            )
        return ComponentHealth(
            component=spec.component,
            provider=spec.provider,
            status="ready",
            required=True,
            artifact_digest=getattr(spec.dependency, "artifact_digest", None),
        )

    def _unavailable(self, spec: DependencySpec, code: str) -> ComponentHealth:
        self._metrics.increment(
            "matching_dependency_errors_total", component=spec.component
        )
        return ComponentHealth(
            component=spec.component,
            provider=spec.provider,
            status="unavailable",
            required=True,
            error_code=code,
            artifact_digest=getattr(spec.dependency, "artifact_digest", None),
        )
