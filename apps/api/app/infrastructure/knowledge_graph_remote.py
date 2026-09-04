from __future__ import annotations

from app.integrations.knowledge_graph.client import KnowledgeGraphClient


class KnowledgeGraphRemoteGateway:
    """Remote-only KG gateway; it has no database session or transaction API."""

    def __init__(self, client: KnowledgeGraphClient) -> None:
        self._client = client

    def readiness(self):
        return self._client.readiness()

    def list_positions(self):
        return self._client.list_positions()

    def list_skills(self):
        return self._client.list_skills()

    def import_published_fact_v3(self, payload, **actor: str):
        return self._client.import_published_fact_v3(payload, **actor)

    def upsert_skill_snapshot(self, skill_id: str, payload, **actor: str):
        return self._client.upsert_skill_snapshot(skill_id, payload, **actor)

    def build_graph(self, position_id: str, payload, **actor: str):
        return self._client.build_graph(position_id, payload, **actor)

    def portal_call(self, method: str, path: str, *, payload=None, params=None, **actor: str):
        return self._client.portal_call(
            method,
            path,
            payload=payload,
            params=params,
            **actor,
        )

    def build_runs(self, position_id: str):
        return self._client.build_runs(position_id)

    def build_run(self, run_id: str):
        return self._client.build_run(run_id)

    def graph(self, position_id: str):
        return self._client.graph(position_id)

    def position_profile(self, position_id: str, *, graph_version_id: int | None = None):
        return self._client.position_profile(
            position_id, graph_version_id=graph_version_id
        )

    def versions(self, position_id: str):
        return self._client.versions(position_id)

    def relation_evidence(self, relation_id: str):
        return self._client.relation_evidence(relation_id)
