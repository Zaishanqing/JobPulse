import os
import atexit
from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


# This file is loaded before test modules import app.core.database. Force every
# test to use a disposable database so Base.metadata.drop_all() can never wipe
# the developer's data/dev.db or a configured production database.
os.environ["ENVIRONMENT"] = "test"
os.environ["TREND_INTELLIGENCE_TEST_ADAPTER_ENABLED"] = "true"
# Keep tests hermetic from the developer's .env (pydantic reads it when pytest
# runs inside apps/api): the running stack sets enforce, while test suites pass
# the validation mode explicitly to their UoWs and expect the default here.
os.environ["DATA_VALIDATION_MODE"] = "off"
_test_root = Path(".test-artifacts") / f"pytest_{uuid4().hex}"
_test_root.mkdir(parents=True, exist_ok=True)
atexit.register(shutil.rmtree, _test_root, True)
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", f"sqlite:///{(_test_root.resolve() / 'test.db').as_posix()}"
)
os.environ["UPLOAD_DIR"] = os.environ.get(
    "TEST_UPLOAD_DIR", str(_test_root / "uploads")
)


@pytest.fixture(scope="session", autouse=True)
def application_lifespan():
    from app.main import app

    with TestClient(app):
        yield


@pytest.fixture(autouse=True)
def published_kg_profile_test_adapter(monkeypatch):
    """Tests explicitly emulate the external KG; production has no local fallback."""
    from app.integrations.knowledge_graph.client import KnowledgeGraphClient
    from app.models.standard_position import StandardPosition
    from tests.runtime_database import SessionLocal

    def position_profile(self, position_id: str, *, graph_version_id=None):
        with SessionLocal() as session:
            # The production KG API is addressed by taxonomy position_code.
            # Keep UUID lookup for older focused tests, while making the shared
            # adapter honor the current boundary contract.
            position = session.get(StandardPosition, position_id)
            if position is None:
                position = (
                    session.query(StandardPosition)
                    .filter(StandardPosition.position_code == position_id)
                    .one_or_none()
                )
            if position is None:
                return SimpleNamespace(data=None)
            raw_skills = [
                *(position.required_skills or []),
                *(position.bonus_skills or []),
            ]
            relations = [
                {
                    "skill_id": str(item["skill_id"]),
                    "skill_name": str(item.get("skill_name") or item["skill_id"]),
                    "category_code": item.get("category"),
                    "taxonomy_version": position.taxonomy_family_code,
                    "weight": float(item.get("weight", 0.1)),
                    "confidence": float(item.get("confidence", 0.9)),
                    "importance_level": str(item.get("importance_level", "edge")),
                    "modality": None,
                    "evidence_count": int(item.get("evidence_count", 0)),
                }
                for item in raw_skills
            ]
            return SimpleNamespace(data={
                "contract_version": "position-profile.v3",
                "position_id": position.id,
                "position_code": position.position_code,
                "position_name": position.position_name,
                "graph_version": "test-published-v1",
                "graph_version_id": int(graph_version_id or 1),
                "profile_state": "published",
                "taxonomy_version": position.taxonomy_version,
                "classification_status": "resolved",
                "career_level": None,
                "leadership_scope": None,
                "sample_support_status": position.sample_support_status,
                "responsibilities": [
                    {"text": str(item)} for item in position.core_responsibilities or []
                ],
                "requirements": [],
                "skill_relations": relations,
                "evidence_summary": [],
                "quality": {
                    "included_samples": 1,
                    "excluded_samples": 0,
                    "relation_count": len(relations),
                    "evidence_count": 0,
                    "unresolved_count": 0,
                    "non_exact_evidence_count": 0,
                    "publication_gate_passed": True,
                },
                "content_hash": "test-published-profile-hash",
                "published_at": "2026-08-01T00:00:00Z",
                "dependencies": {
                    "published_fact_versions": ["test:fact@v1"],
                    "skill_catalog_version": "test-taxonomy-v1",
                    "mapping_snapshot_version": "test-mapping-v1",
                    "normalization_algorithm_version": "test-normalizer-v1",
                    "build_config_version": "test-config-v1",
                    "source_time_window": {"start": None, "end": None},
                },
            })

    def skill_relations_batch(self, skill_ids):
        return SimpleNamespace(
            data={"graph_version": "test-kg-published", "relations": []}
        )

    def register_dependency_reference(self, **payload):
        return SimpleNamespace(data={"dependency_reference_id": 1})

    monkeypatch.setattr(KnowledgeGraphClient, "position_profile", position_profile)
    monkeypatch.setattr(
        KnowledgeGraphClient, "skill_relations_batch", skill_relations_batch
    )
    monkeypatch.setattr(
        KnowledgeGraphClient,
        "register_dependency_reference",
        register_dependency_reference,
    )


def pytest_sessionfinish(session, exitstatus):
    from tests.runtime_database import engine

    engine.dispose()
    shutil.rmtree(_test_root, ignore_errors=True)
