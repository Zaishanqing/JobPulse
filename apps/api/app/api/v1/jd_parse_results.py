from fastapi import APIRouter, Depends

from app.api.dependencies.accounts import get_authenticated_account
from app.api.dependencies.use_cases import get_jd_use_cases
from app.api.jd_error_mapping import jd_http_exception
from app.api.jd_mapping import map_jd_output
from app.contexts.access import AccountRecord
from app.contexts.jd_lifecycle import Actor, JDApplicationError, JDUseCases
from app.core.response import success_response
from app.schemas.jd import (
    JDPositionCatalogMappingRequest,
    JDSkillCatalogExclusionRequest,
    JDSkillCatalogMappingRequest,
)


router = APIRouter(prefix="/jd-parse-results", tags=["jd-parse-results"])


def _actor(account: AccountRecord) -> Actor:
    return Actor(id=account.account_id, role=account.role)


@router.post("/{parse_result_id}/position-catalog-mapping")
def map_jd_position_to_catalog(
    parse_result_id: str,
    payload: JDPositionCatalogMappingRequest,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        result = use_cases.map_parse_position_to_catalog(
            _actor(current_user),
            parse_result_id,
            target_position_id=payload.target_position_id,
            career_level=payload.career_level,
            leadership_scope=payload.leadership_scope,
            technology_focus_codes=payload.technology_focus_codes,
            industry_context_codes=payload.industry_context_codes,
        )
    except JDApplicationError as exc:
        raise jd_http_exception(exc) from exc
    return success_response(data=map_jd_output(result))


@router.post("/{parse_result_id}/skill-catalog-mappings")
def map_jd_skill_to_catalog(
    parse_result_id: str,
    payload: JDSkillCatalogMappingRequest,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        result = use_cases.map_parse_skill_to_catalog(
            _actor(current_user),
            parse_result_id,
            source_name=payload.source_name,
            target_skill_id=payload.target_skill_id,
            requirement_id=payload.requirement_id,
        )
    except JDApplicationError as exc:
        raise jd_http_exception(exc) from exc
    return success_response(data=map_jd_output(result))


@router.post("/{parse_result_id}/skill-catalog-exclusions")
def exclude_jd_skill_from_downstream(
    parse_result_id: str,
    payload: JDSkillCatalogExclusionRequest,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        result = use_cases.exclude_parse_skill_from_downstream(
            _actor(current_user),
            parse_result_id,
            source_name=payload.source_name,
            requirement_id=payload.requirement_id,
            reason=payload.reason,
        )
    except JDApplicationError as exc:
        raise jd_http_exception(exc) from exc
    return success_response(data=map_jd_output(result))


@router.post("/{parse_result_id}/publish")
def publish_jd_parse_result(
    parse_result_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        publication = use_cases.publish_parse_result_by_id(
            _actor(current_user), parse_result_id
        )
    except JDApplicationError as exc:
        raise jd_http_exception(exc) from exc
    return success_response(data=map_jd_output(publication))


@router.get("/{parse_result_id}/publication")
def get_jd_publication(
    parse_result_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    use_cases: JDUseCases = Depends(get_jd_use_cases),
):
    try:
        publication = use_cases.get_publication(_actor(current_user), parse_result_id)
    except JDApplicationError as exc:
        raise jd_http_exception(exc) from exc
    return success_response(data=map_jd_output(publication))
