from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.evidence_rag import get_evidence_rag_handlers
from app.contexts.evidence_rag import EvidenceRagError, ManageEvidenceRag
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.json_types import freeze_json_object, thaw_json_object
from app.schemas.evidence_rag import (
    EvidenceCitationResolveRequest,
    EvidenceCitationResolution,
    EvidenceRagBFFRequest,
    EvidenceRagIndexBody,
    EvidenceRagScopeFilter,
)
from app.contexts.evidence_rag import rag_index_status


router = APIRouter(prefix="/rag", tags=["rag"])


def _raise(exc: Exception) -> None:
    if isinstance(exc, EvidenceRagError) and exc.code in {
        "CITATION_PERMISSION_DENIED",
        "RAG_INDEX_STATUS_PERMISSION_DENIED",
    }:
        status = 403
    elif isinstance(exc, EvidenceRagError) and exc.code in {
        "CITATION_NOT_FOUND",
        "CITATION_TARGET_NOT_FOUND",
    }:
        status = 404
    elif isinstance(exc, EvidenceRagError) and exc.code == "CITATION_VERSION_INVALID":
        status = 409
    elif isinstance(exc, EvidenceRagError) and exc.code == "RAG_EVIDENCE_DISABLED":
        status = 503
    elif isinstance(exc, EvidenceRagError) and exc.code == "CITATION_SOURCE_UNSUPPORTED":
        status = 422
    elif isinstance(exc, EvidenceRagError):
        status = 422 if exc.code.endswith("_INVALID") else 503
    else:
        status = 422
    detail = (
        {"error_code": exc.code, "message": str(exc)}
        if isinstance(exc, EvidenceRagError)
        else str(exc)
    )
    raise HTTPException(status_code=status, detail=detail) from exc


@router.post("/evidence")
def query_evidence_rag(
    payload: EvidenceRagBFFRequest,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageEvidenceRag = Depends(get_evidence_rag_handlers),
):
    response = handlers.query(
        actor, freeze_json_object(payload.model_dump(mode="json"))
    )
    return success_response(data=response.model_dump(mode="json"))


@router.post("/evidence/citations/resolve")
def resolve_evidence_citation(
    payload: EvidenceCitationResolveRequest,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageEvidenceRag = Depends(get_evidence_rag_handlers),
):
    try:
        result = handlers.resolve_citation(
            actor, freeze_json_object(payload.model_dump(mode="json"))
        )
    except Exception as exc:
        _raise(exc)
    response = EvidenceCitationResolution.model_validate(thaw_json_object(result))
    return success_response(data=response.model_dump(mode="json"))


@router.post("/evidence/index")
def index_evidence_rag(
    payload: EvidenceRagIndexBody,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageEvidenceRag = Depends(get_evidence_rag_handlers),
):
    try:
        result = handlers.index(
            actor,
            tuple(freeze_json_object(item.model_dump()) for item in payload.items),
        )
    except Exception as exc:
        _raise(exc)
    return success_response(data=thaw_json_object(result))


@router.get("/evidence/index-status")
def evidence_rag_index_status(
    business_object_type: str = Query(min_length=1, max_length=80),
    business_object_id: str = Query(min_length=1, max_length=120),
    graph_version_id: int | None = Query(default=None, ge=1),
    graph_version: str | None = Query(default=None, max_length=80),
    business_version: str | None = Query(default=None, max_length=120),
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageEvidenceRag = Depends(get_evidence_rag_handlers),
):
    try:
        result = rag_index_status(
            actor=actor,
            business_object_type=business_object_type,
            business_object_id=business_object_id,
            graph_version_id=graph_version_id,
            graph_version=graph_version,
            business_version=business_version,
            rag=handlers,
        )
    except Exception as exc:
        _raise(exc)
    return success_response(data=result)


@router.post("/evidence/invalidate")
def invalidate_evidence_rag(
    payload: EvidenceRagScopeFilter,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageEvidenceRag = Depends(get_evidence_rag_handlers),
):
    try:
        result = handlers.deactivate(actor, freeze_json_object(payload.model_dump()))
    except Exception as exc:
        _raise(exc)
    return success_response(data=thaw_json_object(result))


@router.delete("/evidence")
def delete_evidence_rag(
    payload: EvidenceRagScopeFilter,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageEvidenceRag = Depends(get_evidence_rag_handlers),
):
    try:
        result = handlers.delete(actor, freeze_json_object(payload.model_dump()))
    except Exception as exc:
        _raise(exc)
    return success_response(data=thaw_json_object(result))


__all__ = ["router"]
