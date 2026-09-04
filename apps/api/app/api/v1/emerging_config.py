from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.system import get_system_config_use_cases
from app.contexts.platform import ManageSystemConfigs
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.errors import PermissionDenied
from app.schemas.api_requests import GerminationScoreConfigRequest


router = APIRouter(prefix="/emerging-positions", tags=["emerging-positions"])


@router.put("/score-config")
def update_germination_score_config(
    payload: GerminationScoreConfigRequest = Body(
        default_factory=lambda: GerminationScoreConfigRequest({})
    ),
    actor: AccountActor = Depends(get_account_actor),
    configs: ManageSystemConfigs = Depends(get_system_config_use_cases),
):
    try:
        record = configs.update(actor, "germination-score", payload.root)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return success_response(data=dict(record.config))


@router.get("/score-config")
def get_germination_score_config(
    actor: AccountActor = Depends(get_account_actor),
    configs: ManageSystemConfigs = Depends(get_system_config_use_cases),
):
    try:
        record = configs.get(actor, "germination-score")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return success_response(data=dict(record.config))
