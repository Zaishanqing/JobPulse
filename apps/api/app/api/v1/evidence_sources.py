from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.governance import get_governance_handlers
from app.contexts.governance_feedback import EvidenceNotFound, GovernanceHandlers
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.contexts.governance_feedback import EvidenceDraft, EvidenceRecord
from app.schemas.evidence import EvidenceSourceCreate, EvidenceSourceUpdate
from app.domain.errors import PermissionDenied


router = APIRouter(tags=["evidence-sources"])


def _data(item: EvidenceRecord) -> dict:
    return {
        "evidence_id": item.evidence_id,
        "source_type": item.source_type,
        "source_name": item.source_name,
        "source_platform": item.source_platform,
        "title": item.title,
        "url": item.url,
        "raw_text": item.raw_text,
        "publish_date": item.publish_date.isoformat() if item.publish_date else None,
        "credibility_score": item.credibility_score,
        "related_object_type": item.related_object_type,
        "related_object_id": item.related_object_id,
        "enterprise_id": item.enterprise_id,
        "template_cluster_id": item.template_cluster_id,
        "source_version": item.source_version,
        "source_fact_id": item.source_fact_id,
        "source_jd_id": item.source_jd_id,
        "source_jd_version_id": item.source_jd_version_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _raise(exc: Exception) -> None:
    code = 404 if isinstance(exc, EvidenceNotFound) else 403
    raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.post("/evidence-sources")
def create_evidence_source(payload: EvidenceSourceCreate, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    try:
        item = handlers.evidence.create(actor, EvidenceDraft(**payload.model_dump()))
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=_data(item))


@router.get("/evidence-sources")
def list_evidence_sources(actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    try:
        items = handlers.evidence.list(actor)
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=[_data(item) for item in items])


@router.get("/evidence-sources/{evidence_id}")
def get_evidence_source(evidence_id: str, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    try:
        item = handlers.evidence.get(actor, evidence_id)
    except (EvidenceNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=_data(item))


@router.put("/evidence-sources/{evidence_id}")
def update_evidence_source(evidence_id: str, payload: EvidenceSourceUpdate, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    try:
        item = handlers.evidence.update(actor, evidence_id, payload.model_dump(exclude_unset=True))
    except (EvidenceNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=_data(item))


@router.delete("/evidence-sources/{evidence_id}")
def delete_evidence_source(evidence_id: str, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    try:
        handlers.evidence.delete(actor, evidence_id)
    except (EvidenceNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data={"evidence_id": evidence_id, "deleted": True})


def _related(object_type: str, object_id: str, handlers: GovernanceHandlers):
    return success_response(data=[_data(item) for item in handlers.evidence.related(object_type, object_id)])


@router.get("/skills/{skill_id}/evidence")
def get_skill_evidence_api(skill_id: str, handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    return _related("skill", skill_id, handlers)


@router.get("/positions/{position_id}/evidence")
def get_position_evidence_api(position_id: str, handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    return _related("position", position_id, handlers)


@router.get("/relations/{relation_id}/evidence")
def get_relation_evidence_api(relation_id: str, handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    return _related("relation", relation_id, handlers)
