from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response
from starlette.responses import PlainTextResponse

from app.api.contracts import (
    ContractIntegrationEnvelope,
    EvaluationEnvelope,
    EvidenceDeletionEnvelope,
    GapAnalysisEnvelope,
    HealthData,
    HealthEnvelope,
    LivenessData,
    LivenessEnvelope,
    PersistedEvaluationEnvelope,
    ReadinessEnvelope,
    TaskQueryEnvelope,
    TaskSubmissionEnvelope,
    ValidationData,
    ValidationEnvelope,
    ValidationErrorResponse,
    WhatIfEnvelope,
)
from app.api.security import authenticated_context, security_audit
from app.application.authorization import (
    AuthorizationError,
    request_access_scope,
    require_service,
)
from app.application.contract_mapping import map_cv_bundle, map_position_bundle
from app.application.evaluation import MatchEvaluationService
from app.application.evaluation_tasks import EvaluationTaskService
from app.application.explanation_deletion import ExplanationDeletionService
from app.application.integration import ContractIntegrationService
from app.application.learning_paths import LearningPathService
from app.application.resource_authorization import ResourceAuthorizationService
from app.application.task_submission import TaskSubmissionService
from app.application.validation import ProfileValidationResult, ProfileValidationService
from app.application.vector_index_admin import (
    VectorProfileEventRequest,
    VectorReconcileRequest,
    VectorReindexRequest,
    VectorRetryFailedRequest,
)
from app.application.what_if import WhatIfService
from app.domain.auth import SERVICE_ROLES, AuthContext, derive_subject_ref, derive_tenant_ref
from app.domain.evaluation import MatchEvaluation
from app.domain.gaps import GapAnalysis
from app.domain.integration import ContractIntegrationResult
from app.domain.tasks import TaskSubmissionResult

router = APIRouter()


def _trusted_evaluation_payload(payload: object, context: AuthContext) -> object:
    if not isinstance(payload, Mapping):
        return payload
    trusted_tenant = derive_tenant_ref(context.tenant_id)
    asserted_tenant = payload.get("tenant_ref")
    if asserted_tenant is not None and asserted_tenant != trusted_tenant:
        raise AuthorizationError(
            "TENANT_REF_MISMATCH", "tenant reference does not match authenticated identity"
        )
    trusted = dict(payload)
    trusted["tenant_ref"] = trusted_tenant
    trusted["user_ref"] = derive_subject_ref(context.subject_id)
    return trusted


def _reject_unknown_contract(payload: object) -> None:
    if not isinstance(payload, Mapping):
        return
    request_version = payload.get("schema_version")
    if request_version is not None and request_version != "matching-evaluation-request.v1":
        raise HTTPException(status_code=422, detail="UNSUPPORTED_MATCHING_CONTRACT_VERSION")
    for key, supported in (
        ("cv_profile", {"cv-match-profile.v1", "cv-matching-input-bundle.v1"}),
        ("position_profile", {"position-match-profile.v1", "position-matching-input-bundle.v1"}),
    ):
        profile = payload.get(key)
        if not isinstance(profile, Mapping):
            continue
        declared = {
            profile[name]
            for name in ("schema_version", "contract_version")
            if name in profile
        }
        if declared and not declared.issubset(supported):
            raise HTTPException(status_code=422, detail="UNSUPPORTED_MATCHING_CONTRACT_VERSION")


def _map_inline_contracts(payload: object) -> object:
    """Map upstream bundles at the task boundary before strict profile validation."""
    if not isinstance(payload, Mapping):
        return payload
    mapped = dict(payload)
    cv = mapped.get("cv_profile")
    if isinstance(cv, Mapping) and (
        cv.get("schema_version") == "cv-matching-input-bundle.v1"
        or cv.get("contract_version") == "cv-matching-input-bundle.v1"
    ):
        result = map_cv_bundle(cv)
        if result.value is None:
            raise HTTPException(status_code=422, detail="UPSTREAM_CONTRACT_INCOMPATIBLE")
        mapped["cv_profile"] = result.value.model_dump(mode="json")
    position = mapped.get("position_profile")
    if isinstance(position, Mapping) and (
        position.get("schema_version") == "position-matching-input-bundle.v1"
        or position.get("contract_version") == "position-matching-input-bundle.v1"
    ):
        result = map_position_bundle(position)
        if result.value is None:
            raise HTTPException(status_code=422, detail="UPSTREAM_CONTRACT_INCOMPATIBLE")
        mapped["position_profile"] = result.value.model_dump(mode="json")
    return mapped


def _response(result: ProfileValidationResult) -> ValidationEnvelope:
    return ValidationEnvelope(
        code=0,
        message="success",
        data=ValidationData(
            profile_status=result.profile_status,
            profile_id=result.profile_id,
            profile_version=result.profile_version,
            unresolved_items=[item.model_dump(mode="json") for item in result.unresolved_items],
            validation_errors=[
                ValidationErrorResponse(
                    path=item.path,
                    message=item.message,
                    error_type=item.error_type,
                )
                for item in result.validation_errors
            ],
        ),
    )


def _service(request: Request) -> ProfileValidationService:
    return request.app.state.profile_validation_service


def _evaluation_service(request: Request) -> MatchEvaluationService:
    return request.app.state.match_evaluation_service


def _learning_path_service(request: Request) -> LearningPathService:
    return request.app.state.learning_path_service


def _what_if_service(request: Request) -> WhatIfService:
    return request.app.state.what_if_service


def _explanation_deletion_service(request: Request) -> ExplanationDeletionService:
    return request.app.state.explanation_deletion_service


def _integration_service(request: Request) -> ContractIntegrationService:
    return request.app.state.contract_integration_service


def _task_service(request: Request) -> EvaluationTaskService:
    return request.app.state.evaluation_task_service


def _task_submission_service(request: Request) -> TaskSubmissionService:
    return request.app.state.task_submission_service


def _resource_authorization(request: Request) -> ResourceAuthorizationService:
    return request.app.state.resource_authorization_service


def _vector_admin(request: Request):
    service = request.app.state.vector_index_admin_service
    if service is None:
        raise HTTPException(status_code=503, detail="vector index admin is disabled")
    return service


def internal_service_context(
    request: Request,
    auth_context: Annotated[AuthContext, Depends(authenticated_context)],
) -> AuthContext:
    try:
        require_service(auth_context)
    except AuthorizationError as exc:
        security_audit(request, decision="denied", reason_code=exc.code, context=auth_context)
        raise
    return auth_context


def business_context(
    request: Request,
    auth_context: Annotated[AuthContext, Depends(authenticated_context)],
    access_scope: Annotated[str | None, Header(alias="X-Access-Scope")] = None,
) -> AuthContext:
    try:
        request_access_scope(auth_context, access_scope)
    except AuthorizationError as exc:
        security_audit(request, decision="denied", reason_code=exc.code, context=auth_context)
        raise
    return auth_context


@router.get("/health", response_model=HealthEnvelope)
def health() -> HealthEnvelope:
    return HealthEnvelope(
        code=0,
        message="success",
        data=HealthData(status="ok", service="matching-service", version="0.14.0"),
    )


@router.get("/health/live", response_model=LivenessEnvelope)
def liveness() -> LivenessEnvelope:
    return LivenessEnvelope(
        code=0,
        message="success",
        data=LivenessData(status="alive", service="matching-service", version="0.14.0"),
    )


@router.get("/health/ready", response_model=ReadinessEnvelope)
def readiness(request: Request, response: Response) -> ReadinessEnvelope:
    report = request.app.state.health_service.readiness()
    if report.status == "not_ready":
        response.status_code = 503
    request.state.log_context = {
        "status": report.status,
        "error_code": next(
            (item.error_code for item in report.components if item.error_code), None
        ),
    }
    return ReadinessEnvelope(code=0, message="success", data=report)


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(request: Request) -> PlainTextResponse:
    registry = request.app.state.metrics_registry
    try:
        counts = _task_service(request).task_status_counts()
        for status, count in counts.items():
            registry.set_gauge("matching_tasks", count, status=status)
    except Exception:
        registry.increment("matching_dependency_errors_total", component="postgresql")
    return PlainTextResponse(
        registry.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
    )


@router.post("/internal/vector-index/reindex")
def vector_reindex(
    payload: VectorReindexRequest,
    request: Request,
    _context: Annotated[AuthContext, Depends(internal_service_context)],
) -> dict[str, object]:
    data = _vector_admin(request).reindex(payload)
    return {"code": 0, "message": "success", "data": data}


@router.post("/internal/vector-index/profile-events", include_in_schema=False)
def vector_profile_event(
    payload: VectorProfileEventRequest,
    request: Request,
    _context: Annotated[AuthContext, Depends(internal_service_context)],
) -> dict[str, object]:
    data = _vector_admin(request).ingest_profile_event(payload)
    return {"code": 0, "message": "success", "data": data}


@router.get("/internal/vector-index/status")
def vector_status(
    request: Request,
    _context: Annotated[AuthContext, Depends(internal_service_context)],
) -> dict[str, object]:
    return {
        "code": 0,
        "message": "success",
        "data": _vector_admin(request).status(),
    }


@router.post("/internal/vector-index/reconcile")
def vector_reconcile(
    payload: VectorReconcileRequest,
    request: Request,
    _context: Annotated[AuthContext, Depends(internal_service_context)],
) -> dict[str, object]:
    data = _vector_admin(request).reconcile(payload)
    return {"code": 0, "message": "success", "data": data}


@router.post("/internal/vector-index/retry-failed")
def vector_retry_failed(
    payload: VectorRetryFailedRequest,
    request: Request,
    _context: Annotated[AuthContext, Depends(internal_service_context)],
) -> dict[str, object]:
    data = _vector_admin(request).retry_failed(payload.event_ids)
    return {"code": 0, "message": "success", "data": data}


@router.post("/api/v1/profiles/cv/validate", response_model=ValidationEnvelope)
def validate_cv(
    payload: dict[str, Any],
    request: Request,
    auth_context: Annotated[AuthContext, Depends(business_context)],
) -> ValidationEnvelope:
    _reject_unknown_contract({"cv_profile": payload})
    _resource_authorization(request).authorize_cv(auth_context, payload.get("cv_id"))
    return _response(_service(request).validate_cv(payload))


@router.post("/api/v1/profiles/position/validate", response_model=ValidationEnvelope)
def validate_position(
    payload: dict[str, Any],
    request: Request,
    auth_context: Annotated[AuthContext, Depends(business_context)],
) -> ValidationEnvelope:
    _reject_unknown_contract({"position_profile": payload})
    _resource_authorization(request).require_business_role(auth_context)
    return _response(_service(request).validate_position(payload))


@router.post("/api/v1/evaluations", response_model=EvaluationEnvelope)
def create_evaluation(
    request: Request,
    auth_context: Annotated[AuthContext, Depends(business_context)],
    payload: Annotated[Any | None, Body()] = None,
) -> EvaluationEnvelope:
    _reject_unknown_contract(payload)
    payload = _trusted_evaluation_payload(payload, auth_context)
    _resource_authorization(request).authorize_payload(auth_context, payload)
    evaluation: MatchEvaluation = _evaluation_service(request).evaluate(payload)
    final = evaluation.final_match_result
    request.state.log_context = {
        "evaluation_id": evaluation.evaluation_id,
        "algorithm_version": evaluation.algorithm_version,
        "config_version": final.scoring_config_version if final else None,
        "status": evaluation.evaluation_status,
        "error_code": evaluation.error_code or evaluation.semantic_error_code,
    }
    if evaluation.semantic_status == "unavailable":
        semantic_code = evaluation.semantic_error_code or ""
        if semantic_code.startswith(("EMBEDDING", "VECTOR", "QDRANT")):
            component = (
                "vector"
                if semantic_code.startswith(("VECTOR", "QDRANT"))
                else "embedding"
            )
            request.app.state.metrics_registry.increment(
                "matching_dependency_errors_total", component=component
            )
    return EvaluationEnvelope(code=0, message="success", data=evaluation)


@router.post("/api/v1/learning-paths", response_model=GapAnalysisEnvelope)
def create_learning_path(
    request: Request,
    auth_context: Annotated[AuthContext, Depends(business_context)],
    payload: Annotated[Any | None, Body()] = None,
) -> GapAnalysisEnvelope:
    _resource_authorization(request).require_business_role(auth_context)
    _reject_unknown_contract(payload)
    payload = _map_inline_contracts(
        _trusted_evaluation_payload(payload, auth_context)
    )
    _resource_authorization(request).authorize_payload(auth_context, payload)
    analysis: GapAnalysis = _learning_path_service(request).generate(payload)
    request.state.log_context = {
        "algorithm_version": analysis.algorithm_version,
        "config_version": analysis.config_version,
        "status": analysis.generation_status,
        "error_code": analysis.error_code,
    }
    return GapAnalysisEnvelope(code=0, message="success", data=analysis)


@router.post("/api/v1/what-if", response_model=WhatIfEnvelope)
def create_what_if(
    request: Request,
    auth_context: Annotated[AuthContext, Depends(business_context)],
    payload: Annotated[Any | None, Body()] = None,
) -> WhatIfEnvelope:
    payload = _map_inline_contracts(
        _trusted_evaluation_payload(payload, auth_context)
    )
    _resource_authorization(request).authorize_payload(auth_context, payload)
    result = _what_if_service(request).evaluate(payload)
    request.state.log_context = {
        "scenario_id": result.scenario_id,
        "algorithm_version": result.algorithm_version,
        "status": result.generation_status,
        "error_code": result.error_code,
    }
    return WhatIfEnvelope(code=0, message="success", data=result)


@router.post(
    "/api/v1/explanation-deletions", response_model=EvidenceDeletionEnvelope
)
def create_explanation_deletion(
    request: Request,
    auth_context: Annotated[AuthContext, Depends(business_context)],
    payload: Annotated[Any | None, Body()] = None,
) -> EvidenceDeletionEnvelope:
    payload = _map_inline_contracts(
        _trusted_evaluation_payload(payload, auth_context)
    )
    _resource_authorization(request).authorize_payload(auth_context, payload)
    result = _explanation_deletion_service(request).evaluate(payload)
    request.state.log_context = {
        "deletion_run_id": result.deletion_run_id,
        "algorithm_version": result.algorithm_version,
        "status": result.generation_status,
        "error_code": result.error_code,
    }
    return EvidenceDeletionEnvelope(code=0, message="success", data=result)


@router.post("/api/v1/integrations/evaluate", response_model=ContractIntegrationEnvelope)
def create_integrated_evaluation(
    request: Request,
    auth_context: Annotated[AuthContext, Depends(business_context)],
    payload: Annotated[Any | None, Body()] = None,
) -> ContractIntegrationEnvelope:
    _reject_unknown_contract(payload)
    payload = _trusted_evaluation_payload(payload, auth_context)
    _resource_authorization(request).authorize_payload(auth_context, payload)
    result: ContractIntegrationResult = _integration_service(request).run(payload)
    return ContractIntegrationEnvelope(code=0, message="success", data=result)


@router.post("/api/v1/evaluation-tasks", response_model=TaskSubmissionEnvelope)
def create_evaluation_task(
    request: Request,
    response: Response,
    auth_context: Annotated[AuthContext, Depends(authenticated_context)],
    payload: Annotated[Any | None, Body()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    access_scope: Annotated[str | None, Header(alias="X-Access-Scope")] = None,
) -> TaskSubmissionEnvelope:
    try:
        trusted_scope = request_access_scope(auth_context, access_scope)
    except AuthorizationError as exc:
        security_audit(request, decision="denied", reason_code=exc.code, context=auth_context)
        raise
    payload = _trusted_evaluation_payload(payload, auth_context)
    _resource_authorization(request).authorize_payload(auth_context, payload)
    if (
        isinstance(payload, dict)
        and "cv_profile" not in payload
        and "position_profile" not in payload
        and ("cv_id" in payload or "position_id" in payload)
    ):
        payload, error_code, error_message = _integration_service(request).resolve_task_payload(
            payload
        )
        if payload is None:
            return TaskSubmissionEnvelope(
                code=0,
                message="success",
                data=TaskSubmissionResult(
                    created=False,
                    error_code=error_code,
                    error_message=error_message,
                ),
            )
        payload = _trusted_evaluation_payload(payload, auth_context)
    payload = _map_inline_contracts(payload)
    result = _task_submission_service(request).submit(
        payload,
        idempotency_key or "",
        trusted_scope,
        tenant_ref=derive_tenant_ref(auth_context.tenant_id),
    )
    if result.error_code == "UNSUPPORTED_MATCHING_CONTRACT_VERSION":
        response.status_code = 422
    if result.task is not None:
        request.state.log_context = {
            "task_id": result.task.task_id,
            "access_scope": trusted_scope,
            "algorithm_version": result.task.versions.evaluation_algorithm_version,
            "config_version": result.task.versions.scoring_config_version,
            "status": result.task.status,
            "error_code": result.error_code,
        }
    return TaskSubmissionEnvelope(code=0, message="success", data=result)


@router.get("/api/v1/evaluation-tasks/{task_id}", response_model=TaskQueryEnvelope)
def get_evaluation_task(
    task_id: str,
    request: Request,
    response: Response,
    auth_context: Annotated[AuthContext, Depends(authenticated_context)],
    access_scope: Annotated[str | None, Header(alias="X-Access-Scope")] = None,
) -> TaskQueryEnvelope:
    try:
        trusted_scope = request_access_scope(auth_context, access_scope)
    except AuthorizationError as exc:
        security_audit(request, decision="denied", reason_code=exc.code, context=auth_context)
        raise
    result = _task_service(request).get_task(task_id, trusted_scope)
    if result.task is not None:
        _resource_authorization(request).authorize_task(auth_context, result.task)
    if result.task is None and result.error_code == "TASK_NOT_FOUND":
        response.status_code = 404
    if result.task is not None:
        request.state.log_context = {
            "task_id": result.task.task_id,
            "evaluation_id": result.task.evaluation_id,
            "access_scope": trusted_scope,
            "algorithm_version": result.task.versions.evaluation_algorithm_version,
            "config_version": result.task.versions.scoring_config_version,
            "status": result.task.status,
            "error_code": result.error_code,
        }
    return TaskQueryEnvelope(code=0, message="success", data=result)


@router.post(
    "/api/v1/evaluation-tasks/{task_id}/abandon",
    response_model=TaskQueryEnvelope,
)
def abandon_evaluation_task(
    task_id: str,
    request: Request,
    response: Response,
    auth_context: Annotated[AuthContext, Depends(authenticated_context)],
    access_scope: Annotated[str | None, Header(alias="X-Access-Scope")] = None,
) -> TaskQueryEnvelope:
    """Abandon an owned unfinished task and revoke any outstanding worker lease."""
    try:
        trusted_scope = request_access_scope(auth_context, access_scope)
    except AuthorizationError as exc:
        security_audit(request, decision="denied", reason_code=exc.code, context=auth_context)
        raise
    current = _task_service(request).get_task(task_id, trusted_scope)
    if current.task is not None:
        _resource_authorization(request).authorize_task(auth_context, current.task)
    result = _task_service(request).abandon(task_id, trusted_scope)
    if result.task is None:
        response.status_code = 404 if result.error_code == "TASK_NOT_FOUND" else 409
    return TaskQueryEnvelope(code=0, message="success", data=result)


@router.get("/api/v1/evaluations/{evaluation_id}", response_model=PersistedEvaluationEnvelope)
def get_persisted_evaluation(
    evaluation_id: str,
    request: Request,
    response: Response,
    auth_context: Annotated[AuthContext, Depends(authenticated_context)],
    access_scope: Annotated[str | None, Header(alias="X-Access-Scope")] = None,
) -> PersistedEvaluationEnvelope:
    try:
        trusted_scope = request_access_scope(auth_context, access_scope)
    except AuthorizationError as exc:
        security_audit(request, decision="denied", reason_code=exc.code, context=auth_context)
        raise
    service_wide = bool(auth_context.roles & SERVICE_ROLES)
    result = _task_service(request).get_evaluation(
        evaluation_id, trusted_scope, service_wide=service_wide
    )
    if result.result is not None:
        task_result = _task_service(request).get_task(
            result.result.task_id, trusted_scope, service_wide=service_wide
        )
        if task_result.task is None:
            result = type(result)(
                error_code="EVALUATION_NOT_FOUND", error_message="evaluation was not found"
            )
        else:
            _resource_authorization(request).authorize_task(auth_context, task_result.task)
    if result.result is None and result.error_code == "EVALUATION_NOT_FOUND":
        response.status_code = 404
    if result.result is not None:
        request.state.log_context = {
            "task_id": result.result.task_id,
            "evaluation_id": result.result.evaluation_id,
            "access_scope": trusted_scope,
            "algorithm_version": result.result.versions.evaluation_algorithm_version,
            "config_version": result.result.versions.scoring_config_version,
            "status": "stale" if result.result.stale else "current",
            "error_code": result.error_code,
        }
    return PersistedEvaluationEnvelope(code=0, message="success", data=result)
