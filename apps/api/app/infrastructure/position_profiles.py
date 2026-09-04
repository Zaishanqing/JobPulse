from __future__ import annotations

from app.contexts.matching_learning import PositionProfile
from app.domain.matching import MatchSkill
from app.integrations.knowledge_graph.client import KnowledgeGraphClient


class KnowledgeGraphPositionProfileAdapter:
    """Read formal position profiles only from the KG published-profile contract."""

    def __init__(self, client: KnowledgeGraphClient) -> None:
        self._client = client

    def get(self, position_id: str) -> PositionProfile | None:
        profile = self._client.position_profile(position_id).data
        if (
            not isinstance(profile, dict)
            or profile.get("profile_state") != "published"
            or profile.get("graph_version_id") is None
        ):
            return None
        self._client.register_dependency_reference(
            consumer_system="matching",
            reference_type="position-profile-input",
            reference_id=position_id,
            graph_version_id=int(profile["graph_version_id"]),
            metadata={"contract_version": profile["contract_version"]},
        )
        skills = tuple(
            MatchSkill(
                str(item["skill_id"]),
                str(item["skill_name"]),
                None,
                str(item.get("category_code") or "未分类"),
                float(item["weight"]),
                float(item["confidence"]),
                str(item["importance_level"]),
                0.0,
                int(item.get("evidence_count", 0)),
                None,
            )
            for item in profile.get("skill_relations", ())
            if isinstance(item, dict)
        )
        return PositionProfile(
            str(profile["position_id"]), skills, str(profile["graph_version_id"])
        )
