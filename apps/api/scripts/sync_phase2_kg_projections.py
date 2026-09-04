from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import Settings  # noqa: E402
from app.core.database import create_database  # noqa: E402
from app.infrastructure.accounts import Pbkdf2PasswordAdapter  # noqa: E402
from app.integrations.knowledge_graph.client import KnowledgeGraphClient  # noqa: E402
from app.models.standard_position import StandardPosition  # noqa: E402
from app.models.user import User  # noqa: E402


ACTOR_ID = "phase2-graph-publisher"


def _skill(relation: dict[str, object]) -> dict[str, object]:
    return {
        "skill_id": relation["skill_id"],
        "skill_name": relation["canonical_name"],
        "weight": relation["final_weight"],
        "confidence": relation["final_confidence"],
    }


def main() -> int:
    settings = Settings()
    if not settings.KNOWLEDGE_GRAPH_ENABLED:
        raise ValueError("KNOWLEDGE_GRAPH_ENABLED must be true")
    database = create_database(settings.DATABASE_URL)
    client = KnowledgeGraphClient(
        base_url=settings.KNOWLEDGE_GRAPH_BASE_URL,
        username=settings.KNOWLEDGE_GRAPH_SERVICE_USERNAME,
        password=settings.KNOWLEDGE_GRAPH_SERVICE_PASSWORD,
        timeout_seconds=settings.KNOWLEDGE_GRAPH_TIMEOUT_SECONDS,
    )
    synced: list[dict[str, object]] = []
    try:
        with database.session_factory() as session:
            if session.get(User, ACTOR_ID) is None:
                session.add(
                    User(
                        id=ACTOR_ID,
                        username=ACTOR_ID,
                        hashed_password=Pbkdf2PasswordAdapter().hash(
                            secrets.token_urlsafe(32)
                        ),
                        role="admin",
                    )
                )
                session.flush()
            for position in session.query(StandardPosition).order_by(StandardPosition.id):
                graph = client.graph(position.id).data
                if not isinstance(graph, dict) or graph.get("position_id") != position.id:
                    raise RuntimeError(f"KG returned an invalid graph: {position.id}")
                version_id = graph.get("version_id")
                relations = graph.get("skill_relations")
                if not isinstance(version_id, int) or not isinstance(relations, list) or not relations:
                    raise RuntimeError(f"KG graph is not published or empty: {position.id}")
                ordered = sorted(
                    relations,
                    key=lambda item: (
                        -float(item["final_weight"]),
                        -float(item["final_confidence"]),
                        str(item["skill_id"]),
                    ),
                )
                selected = ordered[:20]
                required = selected[:10]
                bonus = selected[10:]
                position.required_skills = [_skill(item) for item in required]
                position.bonus_skills = [_skill(item) for item in bonus]
                # Matching consumes the normalized skill projection. Raw responsibility
                # evidence remains in KG and is not copied across the PII-safe boundary.
                position.core_responsibilities = []
                synced.append(
                    {
                        "position_id": position.id,
                        "kg_version_id": version_id,
                        "required_skills": len(required),
                        "bonus_skills": len(bonus),
                    }
                )
            session.commit()
        print(json.dumps({"synced": synced}, ensure_ascii=False, indent=2))
        return 0
    finally:
        client.close()
        database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
