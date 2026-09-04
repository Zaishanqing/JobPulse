from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.contexts.evidence_rag import EvidenceRagError, ManageEvidenceRag
from app.domain.accounts import AccountActor
from app.infrastructure.evidence_rag_auto_index import (
    PublishedGraphVersionIndexer,
    RagIndexStatusService,
)


def _indexer() -> PublishedGraphVersionIndexer:
    return PublishedGraphVersionIndexer(
        kg_database_url=None,
        main_database_url=None,
        rag=ManageEvidenceRag(
            None,
            None,
            None,
            False,
            lambda actor: ("jobgraph-platform-public", "platform:public"),
        ),
    )


def test_main_position_id_resolves_confirmed_mapping():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE knowledge_graph_entity_mappings ("
                "entity_type TEXT, main_system_id TEXT, "
                "knowledge_graph_id TEXT, sync_status TEXT, updated_at TEXT)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_graph_entity_mappings VALUES "
                "('position', 'main-position-1', 'kg-position-1', "
                "'confirmed', '2026-01-01')"
            )
        )

    indexer = _indexer()

    assert indexer._main_position_id(engine, "kg-position-1") == "main-position-1"


def test_main_position_id_returns_none_without_mapping():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE knowledge_graph_entity_mappings ("
                "entity_type TEXT, main_system_id TEXT, "
                "knowledge_graph_id TEXT, sync_status TEXT, updated_at TEXT)"
            )
        )

    indexer = _indexer()

    assert indexer._main_position_id(engine, "kg-position-missing") is None


class _StatusStore:
    def count(self, **_filters: object) -> int:
        return 0


def _status_service(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'status.db').as_posix()}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE resumes ("
                "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
                "validated_cv_snapshot_id TEXT, source_cv_version_id TEXT)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE validated_cv_snapshots ("
                "id TEXT PRIMARY KEY, source_cv_version_id TEXT)"
            )
        )
        connection.execute(
            text("CREATE TABLE source_cv_versions (id TEXT PRIMARY KEY)")
        )
        connection.execute(
            text(
                "CREATE TABLE enterprises ("
                "id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE enterprise_jobs ("
                "id TEXT PRIMARY KEY, enterprise_id TEXT NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO resumes VALUES "
                "('resume-1', 'user-1', 'snapshot-1', 'source-version-1')"
            )
        )
        connection.execute(
            text("INSERT INTO validated_cv_snapshots VALUES ('snapshot-1', 'source-version-1')")
        )
        connection.execute(
            text("INSERT INTO source_cv_versions VALUES ('source-version-1')")
        )
        connection.execute(
            text("INSERT INTO enterprises VALUES ('enterprise-1', 'enterprise-owner')")
        )
        connection.execute(
            text("INSERT INTO enterprise_jobs VALUES ('job-1', 'enterprise-1')")
        )
    engine.dispose()
    rag = ManageEvidenceRag(
        None,
        _StatusStore(),
        None,
        True,
        lambda actor: ("tenant", f"personal:{actor.account_id}"),
    )
    return RagIndexStatusService(
        kg_database_url=None,
        main_database_url=database_url,
        rag=rag,
    )


def test_index_status_requires_cv_owner_or_internal_role(tmp_path):
    service = _status_service(tmp_path)

    result = service.status(
        actor=AccountActor("user-1", "personal_user"),
        business_object_type="cv_profile",
        business_object_id="resume-1",
    )
    assert result["status"] == "unknown"

    with pytest.raises(EvidenceRagError, match="outside") as exc_info:
        service.status(
            actor=AccountActor("other-user", "personal_user"),
            business_object_type="cv_profile",
            business_object_id="resume-1",
        )
    assert exc_info.value.code == "RAG_INDEX_STATUS_PERMISSION_DENIED"


def test_index_status_requires_enterprise_owner(tmp_path):
    service = _status_service(tmp_path)

    result = service.status(
        actor=AccountActor("enterprise-owner", "enterprise_user"),
        business_object_type="enterprise_job",
        business_object_id="enterprise-job:job-1",
    )
    assert result["status"] == "unknown"

    with pytest.raises(EvidenceRagError, match="outside"):
        service.status(
            actor=AccountActor("other-owner", "enterprise_user"),
            business_object_type="enterprise_job",
            business_object_id="job-1",
        )


__all__ = []
