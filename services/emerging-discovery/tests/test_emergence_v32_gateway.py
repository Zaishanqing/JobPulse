from __future__ import annotations

from app.bootstrap.settings import Settings
from app.infrastructure.emergence_v32 import KnowledgeGraphEmergenceV32Client


def test_formal_occupation_cluster_batch_uses_long_running_kg_timeout():
    settings = Settings(ENVIRONMENT="test")
    assert settings.KNOWLEDGE_GRAPH_TIMEOUT_SECONDS == 300.0

    client = KnowledgeGraphEmergenceV32Client(
        "http://knowledge-graph:8000",
        "integration_developer",
        "secret",
        settings.KNOWLEDGE_GRAPH_TIMEOUT_SECONDS,
    )
    assert client.timeout == 300.0
