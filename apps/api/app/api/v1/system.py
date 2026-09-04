import httpx

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.system import get_system_config_use_cases, get_system_queries
from app.contexts.platform import ManageSystemConfigs, QuerySystemStatus, SystemConfigNotFound
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.system_config import SystemConfigRuleViolation
from app.contexts.platform import SystemConfigRecord
from app.domain.errors import PermissionDenied
from app.schemas.system import ModelServiceConfigRequest, SystemConfigUpdateRequest


router = APIRouter(tags=["system"])


@router.get("/health")
def api_health_check():
    return success_response(data={"status": "ok"})


@router.get("/system/status")
def get_system_status_api(actor: AccountActor = Depends(get_account_actor), queries: QuerySystemStatus = Depends(get_system_queries)):
    return success_response(data=queries.overall(actor))


@router.get("/system/status/databases")
def get_database_status_api(actor: AccountActor = Depends(get_account_actor), queries: QuerySystemStatus = Depends(get_system_queries)):
    return success_response(data=queries.database(actor))


@router.get("/system/status/vector-db")
def get_vector_db_status_api(actor: AccountActor = Depends(get_account_actor), queries: QuerySystemStatus = Depends(get_system_queries)):
    return success_response(data=queries.vector_store(actor))


@router.get("/system/status/model-services")
def get_model_services_status_api(actor: AccountActor = Depends(get_account_actor), queries: QuerySystemStatus = Depends(get_system_queries)):
    return success_response(data=queries.model_services(actor))


def _config_data(item: SystemConfigRecord) -> dict[str, object]:
    visible_config = {
        key: value
        for key, value in item.config.items()
        if key != "api_key_ciphertext"
    }
    return {
        "config_name": item.name, "config": visible_config,
        "version": item.version, "updated_by": item.updated_by,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "implementation_status": "database_persisted_configuration",
    }


@router.get("/system/model-service-config")
def get_model_service_config(
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSystemConfigs = Depends(get_system_config_use_cases),
):
    try:
        value = use_cases.get_model_service(actor)
    except PermissionDenied as exc:
        _raise_config(exc)
    return success_response(data=value)


@router.put("/system/model-service-config")
def update_model_service_config(
    payload: ModelServiceConfigRequest,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSystemConfigs = Depends(get_system_config_use_cases),
):
    try:
        value = use_cases.update_model_service(
            actor,
            base_url=payload.base_url,
            model=payload.model,
            api_key=payload.api_key,
        )
    except (SystemConfigRuleViolation, PermissionDenied) as exc:
        _raise_config(exc)
    return success_response(data=value)


@router.post("/system/model-service-config/test")
def test_model_service_config(
    payload: ModelServiceConfigRequest,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSystemConfigs = Depends(get_system_config_use_cases),
):
    try:
        api_key = payload.api_key or use_cases.resolve_model_api_key(actor)
    except PermissionDenied as exc:
        _raise_config(exc)
    if not api_key:
        raise HTTPException(status_code=400, detail="请先填写并保存 API Key")
    try:
        response = httpx.post(
            f"{payload.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": payload.model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 1,
                "stream": False,
            },
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=400,
            detail="连接失败，请检查 API 地址和 API Key",
        ) from exc
    return success_response(data={"status": "available", "message": "连接成功"})


def _raise_config(exc: Exception) -> None:
    if isinstance(exc, SystemConfigNotFound):
        code = 404
    elif isinstance(exc, PermissionDenied):
        code = 403
    else:
        code = 400
    raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.get("/system/config/{config_name}")
def get_system_config(config_name: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageSystemConfigs = Depends(get_system_config_use_cases)):
    try:
        item = use_cases.get(actor, config_name)
    except (SystemConfigNotFound, PermissionDenied) as exc:
        _raise_config(exc)
    return success_response(data=_config_data(item))


@router.put("/system/config/{config_name}")
def update_system_config(config_name: str, payload: SystemConfigUpdateRequest = Body(default_factory=lambda: SystemConfigUpdateRequest({})), actor: AccountActor = Depends(get_account_actor), use_cases: ManageSystemConfigs = Depends(get_system_config_use_cases)):
    try:
        item = use_cases.update(actor, config_name, payload.root)
    except (SystemConfigNotFound, SystemConfigRuleViolation, PermissionDenied) as exc:
        _raise_config(exc)
    return success_response(data=_config_data(item))


def _fixed_config_get(config_name: str):
    def route(
        actor: AccountActor = Depends(get_account_actor),
        use_cases: ManageSystemConfigs = Depends(get_system_config_use_cases),
    ):
        return get_system_config(config_name, actor, use_cases)

    route.__name__ = f"get_system_config_{config_name.replace('-', '_')}"
    return route


def _fixed_config_update(config_name: str):
    def route(
        payload: SystemConfigUpdateRequest = Body(default_factory=lambda: SystemConfigUpdateRequest({})),
        actor: AccountActor = Depends(get_account_actor),
        use_cases: ManageSystemConfigs = Depends(get_system_config_use_cases),
    ):
        return update_system_config(config_name, payload, actor, use_cases)

    route.__name__ = f"update_system_config_{config_name.replace('-', '_')}"
    return route


for _config_name in (
    "algorithms",
    "clustering",
    "embedding",
    "germination-score",
    "llm",
    "match-weights",
    "trend-analysis",
):
    router.add_api_route(
        f"/system/config/{_config_name}",
        _fixed_config_get(_config_name),
        methods=["GET"],
    )
    router.add_api_route(
        f"/system/config/{_config_name}",
        _fixed_config_update(_config_name),
        methods=["PUT"],
    )
