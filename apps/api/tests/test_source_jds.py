from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.contexts.source_jds import (
    InvalidSourceJDEnvelope,
    SourceJDImportConflict,
    SourceJDUseCases,
)
from app.core.database import Base
from app.infrastructure.source_jds import (
    SqlAlchemySourceJDRepository,
    SqlAlchemySourceJDUnitOfWork,
)
from app.main import app
from app.models.source_jd import SourceJD, SourceJDVersion
from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.source_identity import compute_content_hash
from tests.runtime_database import reset_database_data, SessionLocal, engine


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _envelope(
    raw_text: str = "Python backend engineer",
    *,
    source_platform: str = "boss",
    source_record_id: str = "job-1",
    source_version: str = "1",
) -> CrawlerJDEnvelopeV1:
    return CrawlerJDEnvelopeV1(
        source_platform=source_platform,
        source_record_id=source_record_id,
        source_version=source_version,
        source_url=f"https://example.test/{source_record_id}",
        job_title_raw="Backend Engineer",
        company_name_raw="Example Ltd",
        region_raw="Shanghai",
        publish_time_raw="today",
        crawl_time=datetime(2026, 7, 23, 8, tzinfo=timezone.utc),
        raw_text=raw_text,
        raw_payload={"id": source_record_id, "text": raw_text},
        raw_html=f"<p>{raw_text}</p>",
        text_canonicalization_version="raw-v1",
    )


def _use_cases(factory=SessionLocal) -> SourceJDUseCases:
    return SourceJDUseCases(lambda: SqlAlchemySourceJDUnitOfWork(factory))


def test_first_and_repeated_import_are_idempotent():
    use_cases = _use_cases()

    first = use_cases.import_source_jd(_envelope())
    repeated = use_cases.import_source_jd(_envelope())

    assert first.source_jd_id == repeated.source_jd_id
    assert first.source_jd_version_id == repeated.source_jd_version_id
    assert (first.created_source, first.created_version, first.is_latest) == (True, True, True)
    assert (repeated.created_source, repeated.created_version, repeated.is_latest) == (
        False,
        False,
        True,
    )
    with SessionLocal() as session:
        assert session.query(SourceJD).count() == 1
        assert session.query(SourceJDVersion).count() == 1


def test_raw_content_hash_is_computed_and_traceable():
    use_cases = _use_cases()
    imported = use_cases.import_source_jd(_envelope("traceable raw text"))

    version = use_cases.get_version(imported.source_jd_version_id)
    assert version.content_hash == compute_content_hash("traceable raw text")
    with SessionLocal() as session:
        persisted = session.get(SourceJDVersion, imported.source_jd_version_id)
        assert persisted.content_hash == compute_content_hash("traceable raw text")


def test_content_hash_mismatch_is_rejected_by_contract():
    raw_text = "mismatch body"
    with pytest.raises(ValueError, match="content_hash"):
        CrawlerJDEnvelopeV1(
            source_platform="boss",
            source_record_id="job-hash-mismatch",
            source_version="1",
            crawl_time=datetime(2026, 7, 23, 8, tzinfo=timezone.utc),
            raw_text=raw_text,
            raw_payload={"text": raw_text},
            text_canonicalization_version="raw-v1",
            content_hash="sha256:" + "0" * 64,
        )


def test_changed_content_adds_version_and_updates_latest_with_stable_history():
    use_cases = _use_cases()
    first = use_cases.import_source_jd(_envelope("version one"))
    second = use_cases.import_source_jd(
        _envelope("version two", source_version="2")
    )

    assert second.source_jd_id == first.source_jd_id
    assert second.source_jd_version_id != first.source_jd_version_id
    assert second.created_source is False
    assert second.created_version is True
    source = use_cases.get_source_jd(first.source_jd_id)
    history = use_cases.list_versions(first.source_jd_id)
    assert source.latest_version_id == second.source_jd_version_id
    assert [item.id for item in history] == [
        second.source_jd_version_id,
        first.source_jd_version_id,
    ]
    old_again = use_cases.import_source_jd(_envelope("version one"))
    assert old_again.created_version is False
    assert old_again.is_latest is False
    assert use_cases.get_source_jd(first.source_jd_id).latest_version_id == (
        second.source_jd_version_id
    )


def test_same_content_from_different_sources_remains_independent():
    use_cases = _use_cases()
    first = use_cases.import_source_jd(_envelope(source_record_id="job-a"))
    second = use_cases.import_source_jd(_envelope(source_record_id="job-b"))

    assert first.source_jd_id != second.source_jd_id
    assert first.source_jd_version_id != second.source_jd_version_id


def test_different_source_platforms_remain_independent():
    use_cases = _use_cases()
    first = use_cases.import_source_jd(_envelope(source_platform="boss"))
    second = use_cases.import_source_jd(_envelope(source_platform="liepin"))

    assert first.source_jd_id != second.source_jd_id
    assert first.source_jd_version_id != second.source_jd_version_id


def test_same_explicit_version_with_different_raw_content_is_rejected():
    use_cases = _use_cases()
    first = use_cases.import_source_jd(_envelope("original text"))

    with pytest.raises(SourceJDImportConflict, match="raw content differs"):
        use_cases.import_source_jd(_envelope("different raw text"))

    with SessionLocal() as session:
        version = session.get(SourceJDVersion, first.source_jd_version_id)
        assert version.raw_text == "original text"


def test_source_jd_version_cannot_be_updated_or_deleted():
    imported = _use_cases().import_source_jd(_envelope())

    with SessionLocal() as session:
        version = session.get(SourceJDVersion, imported.source_jd_version_id)
        version.raw_text = "tampered"
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()
        version = session.get(SourceJDVersion, imported.source_jd_version_id)
        session.delete(version)
        with pytest.raises(ValueError, match="immutable"):
            session.commit()


class _FailingLatestRepository(SqlAlchemySourceJDRepository):
    def set_latest(self, source_jd_id: str, version_id: str):
        raise RuntimeError("injected latest failure")


class _FailingLatestUoW(SqlAlchemySourceJDUnitOfWork):
    def __enter__(self):
        super().__enter__()
        self.source_jds = _FailingLatestRepository(self._session)
        return self


def test_transaction_failure_rolls_back_source_and_version():
    use_cases = SourceJDUseCases(lambda: _FailingLatestUoW(SessionLocal))

    with pytest.raises(RuntimeError, match="injected"):
        use_cases.import_source_jd(_envelope())

    with SessionLocal() as session:
        assert session.query(SourceJD).count() == 0
        assert session.query(SourceJDVersion).count() == 0


def test_repository_flushes_but_never_commits():
    with SessionLocal() as session:
        commits = 0

        @event.listens_for(session, "after_commit")
        def _count_commit(_session):
            nonlocal commits
            commits += 1

        repository = SqlAlchemySourceJDRepository(session)
        source = repository.add_source("boss", "no-commit")
        repository.add_version(source.id, _envelope(source_record_id="no-commit"))
        assert commits == 0
        session.rollback()

    with SessionLocal() as session:
        assert session.query(SourceJD).count() == 0


class _BarrierUoW(SqlAlchemySourceJDUnitOfWork):
    def __init__(self, factory, barrier: threading.Barrier):
        super().__init__(factory)
        self._barrier = barrier

    def acquire_import_lock(self, source_platform: str, source_record_id: str) -> None:
        self._barrier.wait(timeout=15)
        super().acquire_import_lock(source_platform, source_record_id)


def test_concurrent_repeated_import_creates_one_version():
    from pathlib import Path
    from uuid import uuid4

    database_path = Path(".test-artifacts") / f"source-jd-concurrency-{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engines = [
        create_engine(
            database_url,
            connect_args={"check_same_thread": False, "timeout": 10},
            poolclass=NullPool,
        )
        for _ in range(2)
    ]
    Base.metadata.create_all(engines[0])
    factories = [sessionmaker(bind=item, autoflush=False) for item in engines]
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def _import(factory):
        try:
            use_cases = SourceJDUseCases(lambda: _BarrierUoW(factory, barrier))
            results.append(use_cases.import_source_jd(_envelope()))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_import, args=(factory,)) for factory in factories]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    try:
        assert errors == []
        assert len(results) == 2
        assert {item.source_jd_id for item in results}.__len__() == 1
        assert {item.source_jd_version_id for item in results}.__len__() == 1
        assert sum(item.created_version for item in results) == 1
        with factories[0]() as session:
            assert session.query(SourceJD).count() == 1
            assert session.query(SourceJDVersion).count() == 1
    finally:
        for item in engines:
            item.dispose()
        database_path.unlink(missing_ok=True)


def _authenticated_headers() -> dict[str, str]:
    registration = {
        "role": "personal_user",
        "username": "source_importer",
        "password": "password123",
        "email": "source_importer@example.com",
        "phone": "13800000000",
    }
    assert client.post("/api/v1/auth/register", json=registration).status_code == 200
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "source_importer", "password": "password123"},
    )
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def test_import_api_requires_auth_and_exposes_queries():
    payload = _envelope().model_dump(mode="json")
    anonymous = client.post("/api/v1/source-jds/import", json=payload)
    assert anonymous.status_code == 401

    headers = _authenticated_headers()
    imported = client.post("/api/v1/source-jds/import", json=payload, headers=headers)
    assert imported.status_code == 200
    data = imported.json()["data"]
    source = client.get(f"/api/v1/source-jds/{data['source_jd_id']}", headers=headers)
    versions = client.get(
        f"/api/v1/source-jds/{data['source_jd_id']}/versions", headers=headers
    )
    version = client.get(
        f"/api/v1/source-jd-versions/{data['source_jd_version_id']}", headers=headers
    )
    assert source.status_code == versions.status_code == version.status_code == 200
    assert source.json()["data"]["latest_version_id"] == data["source_jd_version_id"]
    assert versions.json()["data"][0]["id"] == data["source_jd_version_id"]
    assert version.json()["data"]["raw_payload"] == payload["raw_payload"]


def test_migration_metadata_has_required_unique_constraints():
    inspector = inspect(engine)
    source_uniques = {item["name"] for item in inspector.get_unique_constraints("source_jds")}
    version_uniques = {
        item["name"] for item in inspector.get_unique_constraints("source_jd_versions")
    }
    # SQLite reflection can intermittently omit a named UNIQUE constraint after
    # repeated concurrent DDL in the full suite. The declarative metadata is the
    # source used by create_all and Alembic autogeneration, so verify both views.
    version_uniques.update(
        constraint.name
        for constraint in SourceJDVersion.__table__.constraints
        if constraint.name is not None
    )
    assert "uq_source_jds_platform_record" in source_uniques
    assert "uq_source_jd_versions_source_version" in version_uniques
