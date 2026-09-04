from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.positions import get_position_use_cases
from app.api.position_mapping import (
    position_data,
    position_skill_from_data,
)
from app.contexts.catalog import (
    ManagePositions,
    PositionNotFound,
)
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.positions import PositionCatalogConflict, PositionRuleViolation
from app.contexts.catalog import PositionChanges, PositionDraft
from app.schemas.position import (
    StandardPositionCreate,
    StandardPositionUpdate,
)


router = APIRouter(tags=["positions"])
SEQUENCE_FIELDS = frozenset(
    {"core_responsibilities", "required_skills", "bonus_skills", "industry_scenarios", "skill_domain_codes"}
)


def _raise(exc: Exception) -> None:
    if isinstance(exc, PositionNotFound):
        code = 404
    elif isinstance(exc, PositionCatalogConflict):
        code = 400
    elif isinstance(exc, PositionRuleViolation):
        code = 403
    else:
        code = 400
    raise HTTPException(status_code=code, detail=str(exc)) from exc


def _changes(payload: StandardPositionUpdate) -> PositionChanges:
    raw = payload.model_dump(exclude_unset=True)
    values = {}
    for name, value in raw.items():
        if name in {"required_skills", "bonus_skills"} and value is not None:
            default_level = "core" if name == "required_skills" else "bonus"
            values[name] = tuple(position_skill_from_data(item, default_level) for item in value)
        elif name in SEQUENCE_FIELDS and value is not None:
            values[name] = tuple(value)
        else:
            values[name] = value
    return PositionChanges(frozenset(raw), **values)


@router.post("/positions")
def create_standard_position(
    payload: StandardPositionCreate,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManagePositions = Depends(get_position_use_cases),
):
    draft = PositionDraft(
        payload.position_name,
        None,
        tuple(payload.core_responsibilities),
        tuple(position_skill_from_data(item, "core") for item in payload.required_skills),
        tuple(position_skill_from_data(item, "bonus") for item in payload.bonus_skills),
        tuple(payload.industry_scenarios),
        payload.status,
        payload.taxonomy_family_code,
        payload.taxonomy_family_name,
        payload.position_code,
        tuple(payload.skill_domain_codes),
    )
    try:
        item = use_cases.create(actor, draft)
    except (PositionRuleViolation, PositionCatalogConflict) as exc:
        _raise(exc)
    return success_response(data=position_data(item))


@router.get("/positions")
def get_standard_positions(use_cases: ManagePositions = Depends(get_position_use_cases)):
    return success_response(data=[position_data(item) for item in use_cases.list()])


@router.get("/position-categories/tree")
def get_position_categories_tree(use_cases: ManagePositions = Depends(get_position_use_cases)):
    families: dict[str, dict[str, Any]] = {}
    for item in use_cases.list():
        family_code = item.taxonomy_family_code or "UNCLASSIFIED"
        family = families.setdefault(
            family_code,
            {
                "family_code": family_code,
                "category": item.taxonomy_family_name or "未分类岗位",
                "skill_domain_codes": list(item.skill_domain_codes),
                "positions": [],
            },
        )
        family["positions"].append(position_data(item))
    return success_response(
        data=list(families.values())
    )


@router.get("/positions/{position_id}")
def get_standard_position_detail(
    position_id: str, use_cases: ManagePositions = Depends(get_position_use_cases)
):
    try:
        item = use_cases.get(position_id)
    except PositionNotFound as exc:
        _raise(exc)
    return success_response(data=position_data(item))


@router.put("/positions/{position_id}")
def edit_standard_position(
    position_id: str,
    payload: StandardPositionUpdate,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManagePositions = Depends(get_position_use_cases),
):
    try:
        item = use_cases.update(actor, position_id, _changes(payload))
    except (PositionNotFound, PositionRuleViolation) as exc:
        _raise(exc)
    return success_response(data=position_data(item))


@router.delete("/positions/{position_id}")
def delete_standard_position(
    position_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManagePositions = Depends(get_position_use_cases),
):
    try:
        use_cases.delete(actor, position_id)
    except (PositionNotFound, PositionRuleViolation) as exc:
        _raise(exc)
    return success_response(data={"position_id": position_id, "deleted": True})
