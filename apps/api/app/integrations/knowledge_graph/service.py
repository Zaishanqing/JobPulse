"""Compatibility exports for tests and scripts during the legacy removal window."""

from app.infrastructure.knowledge_graph_adapter import (
    KnowledgeGraphAdapter,
    serialize_mapping,
)


KnowledgeGraphIntegrationService = KnowledgeGraphAdapter

__all__ = [
    "KnowledgeGraphAdapter",
    "KnowledgeGraphIntegrationService",
    "serialize_mapping",
]
