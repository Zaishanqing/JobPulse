import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.api.dependencies.container import get_application_container
from app.contexts.matching_learning.contracts_service import (
    MatchingContractNotFound,
    MatchingContractService,
    StandardPositionProfileInsufficient,
)
from app.domain.input_limits import MAX_BATCH_SIZE


router = APIRouter(tags=["matching-service-contracts"])
service_bearer = HTTPBearer(auto_error=False)


class CVOwnerAuthorizationRequest(BaseModel):
    subject_id: str
    tenant_id: str
    cv_id: str


class ApplicationGrantAuthorizationRequest(CVOwnerAuthorizationRequest):
    position_id: str


def _service(request: Request) -> MatchingContractService:
    service = get_application_container(request).matching_contracts
    if service is None:
        raise HTTPException(status_code=503, detail="MATCHING_CONTRACTS_UNAVAILABLE")
    return service


def _authenticate_service(
    request: Request,
    credential: HTTPAuthorizationCredentials | None = Depends(service_bearer),
) -> None:
    expected = request.app.extra["runtime_settings"].MATCHING_UPSTREAM_SERVICE_TOKEN or ""
    supplied = credential.credentials.strip() if credential and credential.scheme == "Bearer" else ""
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="SERVICE_CREDENTIAL_INVALID")


@router.get("/contracts/cv-profiles/{cv_id}", dependencies=[Depends(_authenticate_service)])
def cv_profile_contract(cv_id: str, service: MatchingContractService = Depends(_service)):
    try:
        return service.cv_profile(cv_id)
    except MatchingContractNotFound as exc:
        raise HTTPException(status_code=404, detail="CONTRACT_RESOURCE_NOT_FOUND") from exc


@router.get(
    "/contracts/position-profiles/{position_id}",
    dependencies=[Depends(_authenticate_service)],
)
def position_profile_contract(
    position_id: str, service: MatchingContractService = Depends(_service)
):
    try:
        return service.position_profile(position_id)
    except StandardPositionProfileInsufficient as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": exc.code,
                "message": str(exc),
                "reason_code": exc.reason_code,
            },
        ) from exc
    except MatchingContractNotFound as exc:
        raise HTTPException(status_code=404, detail="CONTRACT_RESOURCE_NOT_FOUND") from exc


@router.get(
    "/contracts/enterprise-job-profiles/{job_id}",
    dependencies=[Depends(_authenticate_service)],
    include_in_schema=False,
)
def enterprise_job_profile_contract(
    job_id: str, service: MatchingContractService = Depends(_service)
):
    try:
        return service.enterprise_job_profile(job_id)
    except MatchingContractNotFound as exc:
        raise HTTPException(status_code=404, detail="CONTRACT_RESOURCE_NOT_FOUND") from exc


@router.post("/authorization/cv-owner", dependencies=[Depends(_authenticate_service)])
def cv_owner_authorization(
    payload: CVOwnerAuthorizationRequest,
    service: MatchingContractService = Depends(_service),
):
    authorized = service.cv_owner(payload.subject_id, payload.cv_id)
    if not authorized:
        raise HTTPException(status_code=404, detail="AUTHORIZATION_NOT_FOUND")
    return {"data": {"authorized": True}}


@router.post("/authorization/application-grant", dependencies=[Depends(_authenticate_service)])
def application_grant_authorization(
    payload: ApplicationGrantAuthorizationRequest,
    service: MatchingContractService = Depends(_service),
):
    authorized = service.application_grant(
        payload.subject_id,
        payload.tenant_id,
        payload.cv_id,
        payload.position_id,
    )
    if not authorized:
        raise HTTPException(status_code=404, detail="AUTHORIZATION_NOT_FOUND")
    return {"data": {"authorized": True}}


class EnterpriseJobGrantAuthorizationRequest(BaseModel):
    subject_id: str
    tenant_id: str
    cv_id: str
    enterprise_job_id: str


class SkillRelationQueryRequest(BaseModel):
    contract_version: Literal["skill-relation-query.v1"]
    skill_ids: list[str] = Field(max_length=MAX_BATCH_SIZE)


@router.post(
    "/contracts/skill-relations/query",
    dependencies=[Depends(_authenticate_service)],
)
def skill_relation_query(
    payload: SkillRelationQueryRequest,
    service: MatchingContractService = Depends(_service),
):
    return {"data": service.skill_relations(tuple(payload.skill_ids))}


@router.post(
    "/authorization/enterprise-job-grant",
    dependencies=[Depends(_authenticate_service)],
)
def enterprise_job_grant_authorization(
    payload: EnterpriseJobGrantAuthorizationRequest,
    service: MatchingContractService = Depends(_service),
):
    authorized = service.enterprise_job_grant(
        payload.subject_id,
        payload.tenant_id,
        payload.cv_id,
        payload.enterprise_job_id,
    )
    if not authorized:
        raise HTTPException(status_code=404, detail="AUTHORIZATION_NOT_FOUND")
    return {"data": {"authorized": True}}
