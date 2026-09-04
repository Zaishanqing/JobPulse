from __future__ import annotations

from collections.abc import Mapping

from app.contexts.evidence_rag.application import ManageEvidenceRag
from app.core.config import settings
from app.domain.accounts import AccountActor
from app.domain.json_types import FrozenJsonValue
from app.infrastructure.evidence_rag_auto_index import (
    PublishedGraphVersionIndexer,
    RagIndexStatusService,
)


RagIndexStatus = Mapping[str, FrozenJsonValue]


def enqueue_published_graph_auto_index(
    graph_version_id: int,
    rag: ManageEvidenceRag,
) -> None:
    PublishedGraphVersionIndexer(
        kg_database_url=settings.KNOWLEDGE_GRAPH_RAG_DATABASE_URL,
        main_database_url=settings.DATABASE_URL,
        rag=rag,
    ).index_graph_version(graph_version_id)


def rag_index_status(
    *,
    actor: AccountActor,
    business_object_type: str,
    business_object_id: str,
    graph_version_id: int | None = None,
    graph_version: str | None = None,
    business_version: str | None = None,
    rag: ManageEvidenceRag,
) -> RagIndexStatus:
    service = RagIndexStatusService(
        kg_database_url=settings.KNOWLEDGE_GRAPH_RAG_DATABASE_URL,
        main_database_url=settings.DATABASE_URL,
        rag=rag,
    )
    return service.status(
        actor=actor,
        business_object_type=business_object_type,
        business_object_id=business_object_id,
        graph_version_id=graph_version_id,
        graph_version=graph_version,
        business_version=business_version,
    )
