from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.accounts import (
    get_account_actor,
    get_account_handlers_from_request,
)
from app.contexts.access import AccountHandlers, EnterpriseNotFound
from app.contexts.access import EnterpriseUpdateCommand
from app.core.response import success_response
from app.domain.accounts import AccountActor, AccountRuleViolation
from app.contexts.access import EnterpriseRecord
from app.schemas.enterprise import EnterpriseCreateRequest, EnterpriseUpdateRequest
from app.domain.errors import PermissionDenied


router = APIRouter(prefix="/enterprises", tags=["enterprises"])


def _enterprise_data(enterprise: EnterpriseRecord) -> dict:
    return {
        "enterprise_id": enterprise.enterprise_id,
        "owner_user_id": enterprise.owner_user_id,
        "enterprise_name": enterprise.enterprise_name,
        "industry": enterprise.industry,
        "scale": enterprise.scale,
        "location": enterprise.location,
        "description": enterprise.description,
        "status": enterprise.status,
        "created_at": enterprise.created_at.isoformat() if enterprise.created_at else None,
        "updated_at": enterprise.updated_at.isoformat() if enterprise.updated_at else None,
    }


def _map_enterprise_error(exc: Exception) -> HTTPException:
    if isinstance(exc, EnterpriseNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PermissionDenied):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, AccountRuleViolation):
        status_code = 403 if "Only enterprise users" in str(exc) else 400
        return HTTPException(status_code=status_code, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal server error")


@router.post("")
def create_enterprise_profile(
    payload: EnterpriseCreateRequest,
    actor: AccountActor = Depends(get_account_actor),
    handlers: AccountHandlers = Depends(get_account_handlers_from_request),
):
    try:
        enterprise = handlers.enterprises.create(actor, **payload.model_dump())
    except (AccountRuleViolation, PermissionDenied) as exc:
        raise _map_enterprise_error(exc) from exc
    return success_response(
        data={
            "enterprise_id": enterprise.enterprise_id,
            "enterprise_name": enterprise.enterprise_name,
            "status": enterprise.status,
        }
    )


@router.get("/me")
def get_my_enterprise_profile(
    actor: AccountActor = Depends(get_account_actor),
    handlers: AccountHandlers = Depends(get_account_handlers_from_request),
):
    try:
        enterprise = handlers.enterprises.mine(actor)
    except AccountRuleViolation as exc:
        raise _map_enterprise_error(exc) from exc
    return success_response(data=_enterprise_data(enterprise) if enterprise else None)


@router.get("/{enterprise_id}")
def get_enterprise_profile(
    enterprise_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: AccountHandlers = Depends(get_account_handlers_from_request),
):
    try:
        enterprise = handlers.enterprises.get(actor, enterprise_id)
    except (EnterpriseNotFound, PermissionDenied) as exc:
        raise _map_enterprise_error(exc) from exc
    return success_response(data=_enterprise_data(enterprise))


@router.put("/{enterprise_id}")
def update_enterprise_profile(
    enterprise_id: str,
    payload: EnterpriseUpdateRequest,
    actor: AccountActor = Depends(get_account_actor),
    handlers: AccountHandlers = Depends(get_account_handlers_from_request),
):
    try:
        enterprise = handlers.enterprises.update(
            actor, enterprise_id, EnterpriseUpdateCommand(**payload.model_dump(exclude_unset=True))
        )
    except (EnterpriseNotFound, PermissionDenied, AccountRuleViolation) as exc:
        raise _map_enterprise_error(exc) from exc
    return success_response(data=_enterprise_data(enterprise))
