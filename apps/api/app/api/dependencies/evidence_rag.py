from fastapi import Request

from app.api.dependencies.container import get_application_container
from app.contexts.evidence_rag import ManageEvidenceRag


def get_evidence_rag_handlers(request: Request) -> ManageEvidenceRag:
    return get_application_container(request).evidence_rag


__all__ = ["get_evidence_rag_handlers"]
