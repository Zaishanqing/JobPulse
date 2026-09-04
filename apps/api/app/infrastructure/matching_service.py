from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from jose import jwt
from sqlalchemy.orm import Session, sessionmaker

from app.contexts.matching_learning.matching_service import (
    MatchingIdentity,
    MatchingServiceError,
    RemoteEvaluation,
    RemoteTask,
)
from app.domain.accounts import AccountActor
from app.domain.errors import PermissionDenied
from app.models.candidate_submission import CandidateSubmission
from app.models.enterprise import Enterprise
from app.models.enterprise_job import EnterpriseJob
from app.models.resume import Resume
from jobgraph_contracts.matching import MATCHING_REQUEST_SCHEMA_VERSION


def derive_matching_access_scope(
    subject_id: str, tenant_id: str, roles: tuple[str, ...]
) -> str:
    role_set = set(roles)
    if role_set & {"matching.service", "matching.worker"}:
        return f"service:{tenant_id}:{subject_id}"
    if role_set & {"enterprise", "recruiter"}:
        return f"tenant:{tenant_id}"
    if role_set & {"candidate", "user"}:
        return f"user:{tenant_id}:{subject_id}"
    raise ValueError("unsupported matching identity role")


class DisabledMatchingServiceAdapter:
    def _error(self) -> None:
        raise MatchingServiceError(
            "MATCHING_SERVICE_NOT_CONFIGURED",
            "matching service is not configured",
            status_code=503,
        )

    def create_task(self, *args, **kwargs):
        self._error()

    def get_task(self, *args, **kwargs):
        self._error()

    def abandon_task(self, *args, **kwargs):
        self._error()

    def get_evaluation(self, *args, **kwargs):
        self._error()

    def generate_learning_path(self, *args, **kwargs):
        self._error()

    def evaluate_what_if(self, *args, **kwargs):
        self._error()

    def evaluate_explanation_deletion(self, *args, **kwargs):
        self._error()


class SqlAlchemyMatchingIdentityAdapter:
    """Resolve identity and grants from the main framework's authoritative data."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def resolve(self, actor: AccountActor) -> MatchingIdentity:
        if actor.role == "personal_user":
            tenant_id = f"personal:{actor.account_id}"
            roles = ("candidate",)
        elif actor.role == "enterprise_user":
            with self._session_factory() as session:
                enterprise = (
                    session.query(Enterprise)
                    .filter(
                        Enterprise.owner_user_id == actor.account_id,
                        Enterprise.status == "active",
                    )
                    .order_by(Enterprise.created_at.desc())
                    .first()
                )
            if enterprise is None:
                raise PermissionDenied("Active enterprise identity is required")
            tenant_id = str(enterprise.id)
            roles = ("recruiter",)
        elif actor.role in {"admin", "developer", "reviewer"}:
            tenant_id = "jobgraph-main"
            roles = ("matching.service",)
        else:
            raise PermissionDenied("Matching identity is not supported")
        return MatchingIdentity(
            subject_id=actor.account_id,
            tenant_id=tenant_id,
            roles=roles,
            access_scope=derive_matching_access_scope(actor.account_id, tenant_id, roles),
        )

    def authorize_request(
        self,
        actor: AccountActor,
        *,
        cv_id: str,
        position_id: str,
        target_type: str = "standard_position",
    ) -> None:
        with self._session_factory() as session:
            resume = session.get(Resume, cv_id)
            if resume is None:
                raise PermissionDenied("Matching resource was not found")
            if actor.role == "personal_user":
                if resume.user_id != actor.account_id:
                    raise PermissionDenied("Matching resource was not found")
                if target_type == "enterprise_job":
                    job = session.get(EnterpriseJob, position_id)
                    if job is None or job.status != "published":
                        raise PermissionDenied("Matching resource was not found")
                elif target_type != "standard_position":
                    raise PermissionDenied("Matching resource was not found")
                return
            if actor.role == "enterprise_user":
                grant = (
                    session.query(CandidateSubmission.id)
                    .join(
                        EnterpriseJob,
                        EnterpriseJob.id == CandidateSubmission.enterprise_job_id,
                    )
                    .join(Enterprise, Enterprise.id == CandidateSubmission.enterprise_id)
                    .filter(
                        Enterprise.owner_user_id == actor.account_id,
                        Enterprise.status == "active",
                        EnterpriseJob.enterprise_id == Enterprise.id,
                        CandidateSubmission.enterprise_id == EnterpriseJob.enterprise_id,
                        CandidateSubmission.resume_id == cv_id,
                        CandidateSubmission.status == "submitted",
                        (
                            EnterpriseJob.id == position_id
                            if target_type == "enterprise_job"
                            else EnterpriseJob.standard_position_id == position_id
                        ),
                        EnterpriseJob.status.in_(("published", "paused")),
                    )
                    .first()
                )
                if grant is None:
                    raise PermissionDenied("Matching resource was not found")
                return
            if actor.role not in {"admin", "developer", "reviewer"}:
                raise PermissionDenied("Matching resource was not found")


class HttpMatchingServiceAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        issuer: str,
        audience: str,
        signing_key: str,
        timeout_seconds: float,
        max_retries: int,
        retry_backoff_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._issuer = issuer
        self._audience = audience
        self._key = signing_key
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._backoff = retry_backoff_seconds

    def create_task(
        self,
        identity: MatchingIdentity,
        *,
        cv_id: str,
        position_id: str,
        idempotency_key: str,
        correlation_id: str,
        cv_profile: Mapping[str, object] | None = None,
        position_profile: Mapping[str, object] | None = None,
        target_type: str = "standard_position",
        use_enterprise_weights: bool = False,
        generate_learning_path: bool = False,
    ) -> RemoteTask:
        payload: dict[str, object] = {
            "schema_version": MATCHING_REQUEST_SCHEMA_VERSION,
            "target_type": target_type,
            "tenant_id": identity.tenant_id,
            "cv_id": cv_id,
            "position_id": position_id,
            "use_enterprise_weights": use_enterprise_weights,
            "generate_learning_path": generate_learning_path,
        }
        if cv_profile is not None and position_profile is not None:
            payload["cv_profile"] = dict(cv_profile)
            payload["position_profile"] = dict(position_profile)
        body = self._request(
            "POST",
            "/api/v1/evaluation-tasks",
            identity,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        data = self._data(body)
        task = data.get("task")
        if not isinstance(task, dict):
            code = str(data.get("error_code") or "MATCHING_TASK_REJECTED")
            message = str(data.get("error_message") or "matching task was rejected")
            raise MatchingServiceError(code, message, status_code=502)
        return self._task(task, created=bool(data.get("created", False)))

    def deliver_profile_index_event(
        self, payload: Mapping[str, object], *, correlation_id: str
    ) -> dict[str, object]:
        identity = MatchingIdentity(
            subject_id="jobgraph-profile-outbox",
            tenant_id="jobgraph-main",
            roles=("matching.service",),
            access_scope=derive_matching_access_scope(
                "jobgraph-profile-outbox", "jobgraph-main", ("matching.service",)
            ),
        )
        body = self._request(
            "POST",
            "/internal/vector-index/profile-events",
            identity,
            correlation_id=correlation_id,
            payload=dict(payload),
        )
        return self._data(body)

    def get_task(
        self, identity: MatchingIdentity, task_id: str, *, correlation_id: str
    ) -> RemoteTask:
        body = self._request(
            "GET",
            f"/api/v1/evaluation-tasks/{task_id}",
            identity,
            correlation_id=correlation_id,
        )
        data = self._data(body)
        task = data.get("task")
        if not isinstance(task, dict):
            raise MatchingServiceError("MATCHING_TASK_NOT_FOUND", "matching task was not found", status_code=404)
        return self._task(task, created=False)

    def abandon_task(
        self, identity: MatchingIdentity, task_id: str, *, correlation_id: str
    ) -> RemoteTask:
        body = self._request(
            "POST",
            f"/api/v1/evaluation-tasks/{task_id}/abandon",
            identity,
            correlation_id=correlation_id,
        )
        data = self._data(body)
        task = data.get("task")
        if not isinstance(task, dict):
            code = str(data.get("error_code") or "MATCHING_TASK_ABANDON_REJECTED")
            raise MatchingServiceError(code, "matching task could not be abandoned", status_code=409)
        return self._task(task, created=False)

    def get_evaluation(
        self, identity: MatchingIdentity, evaluation_id: str, *, correlation_id: str
    ) -> RemoteEvaluation:
        body = self._request(
            "GET",
            f"/api/v1/evaluations/{evaluation_id}",
            identity,
            correlation_id=correlation_id,
        )
        data = self._data(body)
        result = data.get("result")
        if not isinstance(result, dict):
            raise MatchingServiceError(
                "MATCHING_EVALUATION_NOT_FOUND", "matching evaluation was not found", status_code=404
            )
        evaluation = result.get("evaluation")
        gap = result.get("gap_analysis")
        metadata = result.get("report_metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        radar = result.get("radar_dimensions")
        radar = radar if isinstance(radar, list) else []
        if not isinstance(evaluation, dict) or not isinstance(gap, dict):
            raise MatchingServiceError("MATCHING_RESPONSE_INVALID", "matching response is invalid")
        return RemoteEvaluation(
            evaluation_id=str(result["evaluation_id"]),
            task_id=str(result["task_id"]),
            stale=bool(result.get("stale", False)),
            evaluation=evaluation,
            gap_analysis=gap,
            versions=dict(result.get("versions") or {}),
            created_at=self._optional_text(result.get("created_at")),
            updated_at=self._optional_text(result.get("updated_at")),
            provider=str(metadata.get("provider") or ""),
            method=str(metadata.get("method") or ""),
            matching_method=str(
                metadata.get("matching_method")
                or self._product_matching_method(evaluation)
            ),
            degraded=bool(metadata.get("degraded", False)),
            rule_based=(
                bool(metadata["rule_based"]) if "rule_based" in metadata else None
            ),
            target_type=str(metadata.get("target_type") or result.get("target_type") or "standard_position"),
            use_enterprise_weights=bool(metadata.get("use_enterprise_weights", False)),
            generate_learning_path=bool(metadata.get("generate_learning_path", False)),
            stale_reason_codes=tuple(str(value) for value in result.get("stale_reason_codes", [])),
            algorithm_versions=dict(metadata.get("algorithm_versions") or {}),
            data_versions=dict(metadata.get("data_versions") or {}),
            radar_dimensions=tuple(dict(value) for value in radar if isinstance(value, dict)),
        )

    def generate_learning_path(
        self,
        identity: MatchingIdentity,
        evaluation: Mapping[str, object],
        *,
        correlation_id: str,
        time_budget_hours: float | None = None,
        cv_profile: Mapping[str, object] | None = None,
        position_profile: Mapping[str, object] | None = None,
        target_type: str = "standard_position",
        use_enterprise_weights: bool = False,
    ) -> dict[str, object]:
        body = self._request(
            "POST",
            "/api/v1/learning-paths",
            identity,
            correlation_id=correlation_id,
            payload={
                "evaluation": dict(evaluation),
                **(
                    {"time_budget_hours": time_budget_hours}
                    if time_budget_hours is not None
                    else {}
                ),
                **(
                    {
                        "cv_profile": dict(cv_profile),
                        "position_profile": dict(position_profile),
                        "target_type": target_type,
                        "use_enterprise_weights": use_enterprise_weights,
                    }
                    if cv_profile is not None and position_profile is not None
                    else {}
                ),
            },
        )
        return self._data(body)

    def evaluate_what_if(
        self,
        identity: MatchingIdentity,
        *,
        baseline_evaluation: Mapping[str, object],
        cv_profile: Mapping[str, object],
        position_profile: Mapping[str, object],
        actions: tuple[Mapping[str, object], ...],
        target_type: str,
        use_enterprise_weights: bool,
        correlation_id: str,
    ) -> dict[str, object]:
        body = self._request(
            "POST",
            "/api/v1/what-if",
            identity,
            correlation_id=correlation_id,
            payload={
                "baseline_evaluation": dict(baseline_evaluation),
                "cv_profile": dict(cv_profile),
                "position_profile": dict(position_profile),
                "actions": [dict(item) for item in actions],
                "target_type": target_type,
                "use_enterprise_weights": use_enterprise_weights,
            },
        )
        return self._data(body)

    def evaluate_explanation_deletion(
        self,
        identity: MatchingIdentity,
        *,
        baseline_evaluation: Mapping[str, object],
        cv_profile: Mapping[str, object],
        position_profile: Mapping[str, object],
        deletion_kind: str,
        evidence_source_ids: tuple[str, ...],
        target_type: str,
        use_enterprise_weights: bool,
        correlation_id: str,
    ) -> dict[str, object]:
        body = self._request(
            "POST",
            "/api/v1/explanation-deletions",
            identity,
            correlation_id=correlation_id,
            payload={
                "baseline_evaluation": dict(baseline_evaluation),
                "cv_profile": dict(cv_profile),
                "position_profile": dict(position_profile),
                "deletion_kind": deletion_kind,
                "evidence_source_ids": list(evidence_source_ids),
                "target_type": target_type,
                "use_enterprise_weights": use_enterprise_weights,
            },
        )
        return self._data(body)

    def _request(
        self,
        method: str,
        path: str,
        identity: MatchingIdentity,
        *,
        correlation_id: str,
        idempotency_key: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> object:
        headers = {
            "Authorization": f"Bearer {self._credential(identity)}",
            "X-Access-Scope": identity.access_scope,
            "X-Request-ID": correlation_id,
            "X-Correlation-ID": correlation_id,
            "Accept": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        url = f"{self._base_url}{path}"
        for attempt in range(self._max_retries + 1):
            try:
                response = httpx.request(
                    method, url, json=payload, headers=headers, timeout=self._timeout
                )
            except httpx.TimeoutException as exc:
                if attempt < self._max_retries:
                    self._sleep(attempt)
                    continue
                raise MatchingServiceError(
                    "MATCHING_SERVICE_TIMEOUT", "matching service timed out", status_code=503
                ) from exc
            except httpx.RequestError as exc:
                if attempt < self._max_retries:
                    self._sleep(attempt)
                    continue
                raise MatchingServiceError(
                    "MATCHING_SERVICE_UNAVAILABLE", "matching service is unavailable", status_code=503
                ) from exc
            if response.status_code in {429, 502, 503, 504} and attempt < self._max_retries:
                self._sleep(attempt)
                continue
            if response.status_code == 404:
                raise MatchingServiceError("MATCHING_RESOURCE_NOT_FOUND", "matching resource was not found", status_code=404)
            if response.status_code in {401, 403}:
                raise MatchingServiceError("MATCHING_SERVICE_AUTH_REJECTED", "matching service rejected authorization", status_code=502)
            if response.status_code == 429:
                raise MatchingServiceError("MATCHING_SERVICE_RATE_LIMITED", "matching service is busy", status_code=503)
            if response.status_code >= 500:
                raise MatchingServiceError("MATCHING_SERVICE_UNAVAILABLE", "matching service is unavailable", status_code=503)
            if response.status_code >= 400:
                code = "MATCHING_REQUEST_REJECTED"
                try:
                    body = response.json()
                    data = body.get("data", {}) if isinstance(body, dict) else {}
                    if isinstance(data, dict) and data.get("error_code"):
                        code = str(data["error_code"])
                except ValueError:
                    pass
                raise MatchingServiceError(
                    code, "matching request was rejected", status_code=response.status_code
                )
            try:
                return response.json()
            except ValueError as exc:
                raise MatchingServiceError("MATCHING_RESPONSE_INVALID", "matching response is invalid") from exc
        raise AssertionError("retry loop must return or raise")

    def _credential(self, identity: MatchingIdentity) -> str:
        now = datetime.now(timezone.utc)
        return jwt.encode(
            {
                "sub": identity.subject_id,
                "tenant_id": identity.tenant_id,
                "roles": list(identity.roles),
                "jti": str(uuid4()),
                "iat": now,
                "exp": now + timedelta(minutes=5),
                "iss": self._issuer,
                "aud": self._audience,
            },
            self._key,
            algorithm="HS256",
        )

    @staticmethod
    def _data(body: object) -> dict[str, object]:
        if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
            raise MatchingServiceError("MATCHING_RESPONSE_INVALID", "matching response is invalid")
        return body["data"]

    @staticmethod
    def _task(task: dict[str, object], *, created: bool) -> RemoteTask:
        return RemoteTask(
            task_id=str(task["task_id"]),
            status=str(task["status"]),
            evaluation_id=HttpMatchingServiceAdapter._optional_text(task.get("evaluation_id")),
            created=created,
            error_code=HttpMatchingServiceAdapter._optional_text(task.get("error_code")),
            error_message=HttpMatchingServiceAdapter._optional_text(task.get("error_message")),
            attempt=int(task.get("attempt", 0)),
            created_at=HttpMatchingServiceAdapter._optional_text(task.get("created_at")),
            updated_at=HttpMatchingServiceAdapter._optional_text(task.get("updated_at")),
            raw=task,
            target_type=str(
                task.get("target_type")
                or (task.get("versions") or {}).get("target_type", "standard_position")
            ),
        )

    @staticmethod
    def _product_matching_method(evaluation: dict[str, object]) -> str:
        """Fallback for older service versions that omit the product field."""

        results = evaluation.get("responsibility_results")
        if not isinstance(results, list):
            return "rule"
        semantic_verified = any(
            isinstance(item, dict)
            and (
                item.get("ce_score") is not None
                or item.get("retrieval_score") is not None
                or bool(item.get("top_candidates"))
            )
            for item in results
        )
        return "semantic_verified" if semantic_verified else "rule"

    @staticmethod
    def _optional_text(value: object) -> str | None:
        return str(value) if value is not None else None

    def _sleep(self, attempt: int) -> None:
        if self._backoff:
            time.sleep(self._backoff * (2**attempt))
