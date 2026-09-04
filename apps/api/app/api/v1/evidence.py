from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.governance import get_governance_handlers
from app.contexts.governance_feedback import (
    EvidenceNotFound,
    GovernanceHandlers,
    RagConflict,
    RagGenerationNotFound,
)
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.contexts.governance_feedback import RagGenerationRecord
from app.schemas.evidence_generation import (
    EvidenceGenerateRequest,
    EvidenceGenerationUpdate,
    EvidenceRetrieveRequest,
    EvidenceValidateRequest,
)
from app.domain.errors import PermissionDenied


router = APIRouter(prefix="/evidence", tags=["evidence"])


def _data(record: RagGenerationRecord) -> dict:
    return {
        "generation_id": record.generation_id, "text": record.text,
        "prompt": record.prompt, "evidence_ids": list(record.evidence_ids),
        "evidence_titles": [], "citations": list(record.citations),
        "need_review": record.need_review, "status": record.status,
        "created_by": record.created_by, "confirmed_by": record.confirmed_by,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "implementation_status": "database_persisted_extractive_evidence_no_llm",
        "provider": "evidence_retriever", "algorithm_version": "evidence-extractive-v1",
        "mock": False, "rule_based": True,
    }


def _raise(exc: Exception) -> None:
    if isinstance(exc, (EvidenceNotFound, RagGenerationNotFound)):
        code = 404
    elif isinstance(exc, RagConflict):
        code = 409
    else:
        code = 403
    raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.post("/retrieve")
def retrieve_evidence_api(payload: EvidenceRetrieveRequest, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    try:
        result = handlers.rag.retrieve(actor, payload.query, payload.top_k)
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=result)


@router.post("/generate")
def generate_evidence_api(payload: EvidenceGenerateRequest, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    try:
        record = handlers.rag.generate(actor, payload.prompt, payload.evidence_ids)
    except (PermissionDenied, EvidenceNotFound) as exc:
        _raise(exc)
    data = _data(record)
    data["evidence_titles"] = [citation["title"] for citation in record.citations]
    return success_response(data=data)


@router.post("/validate")
def validate_evidence_api(payload: EvidenceValidateRequest, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    try:
        result = handlers.rag.validate(actor, payload.text, payload.evidence_ids, payload.claims)
    except (PermissionDenied, EvidenceNotFound) as exc:
        _raise(exc)
    return success_response(data=result)


@router.get("/low-evidence-results")
def list_low_evidence_results_api(actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    try:
        records = handlers.rag.low_evidence(actor)
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=[_data(record) for record in records])


@router.get("/generations/{generation_id}")
def get_evidence_generation_api(generation_id: str, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    try:
        record = handlers.rag.get(actor, generation_id)
    except (PermissionDenied, RagGenerationNotFound) as exc:
        _raise(exc)
    return success_response(data=_data(record))


@router.put("/generations/{generation_id}")
def update_evidence_generation_api(generation_id: str, payload: EvidenceGenerationUpdate, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    try:
        record = handlers.rag.update(actor, generation_id, payload.text)
    except (PermissionDenied, RagGenerationNotFound, RagConflict) as exc:
        _raise(exc)
    return success_response(data=_data(record))


@router.post("/generations/{generation_id}/confirm")
def confirm_evidence_generation_api(generation_id: str, actor: AccountActor = Depends(get_account_actor), handlers: GovernanceHandlers = Depends(get_governance_handlers)):
    try:
        record = handlers.rag.confirm(actor, generation_id)
    except (PermissionDenied, RagGenerationNotFound, RagConflict) as exc:
        _raise(exc)
    return success_response(data=_data(record))
