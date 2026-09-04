from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from app.application.evaluation import MatchEvaluationService
from app.application.evaluation_tasks import EvaluationTaskService
from app.application.learning_paths import LearningPathService
from app.infrastructure.memory_repositories import InMemoryPersistence
from app.infrastructure.persistence_configuration import build_persistence
from app.infrastructure.sqlalchemy_models import (
    OutboxRecordRow,
    PersistedEvaluationRow,
    PersistenceBase,
)
from app.infrastructure.sqlalchemy_repositories import SQLAlchemyPersistence

ROOT = Path(__file__).parents[1]


def _upgrade(database_url: str, revision: str = "head") -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, revision)


def _downgrade(database_url: str, revision: str = "base") -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config, revision)


def _sql_persistence(tmp_path: Path) -> SQLAlchemyPersistence:
    database_url = f"sqlite:///{(tmp_path / 'matching.db').as_posix()}"
    _upgrade(database_url)
    return SQLAlchemyPersistence.from_url(
        database_url, connect_args={"check_same_thread": False}
    )


def _service(persistence: object, **kwargs: object) -> EvaluationTaskService:
    evaluation = MatchEvaluationService()
    return EvaluationTaskService(
        persistence.unit_of_work,
        evaluation,
        LearningPathService(evaluation),
        **kwargs,
    )


def _payload(cv: dict, position: dict) -> dict:
    return {"cv_profile": cv, "position_profile": position}


def test_migration_upgrades_empty_database_twice_and_downgrades(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"

    _upgrade(database_url)
    _upgrade(database_url)
    persistence = SQLAlchemyPersistence.from_url(database_url)
    assert set(inspect(persistence.engine).get_table_names()) >= {
        "alembic_version",
        "evaluation_tasks",
        "persisted_evaluations",
        "audit_records",
        "outbox_records",
        "vector_index_references",
        "vector_outbox_events",
        "vector_outbox_audits",
    }
    task_columns = {
        item["name"] for item in inspect(persistence.engine).get_columns("evaluation_tasks")
    }
    assert {"lease_owner", "lease_expires_at"} <= task_columns
    task_indexes = {
        item["name"] for item in inspect(persistence.engine).get_indexes("evaluation_tasks")
    }
    assert "ix_evaluation_tasks_claim" in task_indexes
    outbox_columns = {
        item["name"] for item in inspect(persistence.engine).get_columns("outbox_records")
    }
    assert {
        "outbox_id",
        "access_scope",
        "task_id",
        "message_id",
        "payload",
        "status",
        "attempt",
        "available_at",
        "claimed_by",
        "claim_expires_at",
        "published_at",
        "last_error_code",
        "created_at",
        "updated_at",
    } <= outbox_columns
    _downgrade(database_url)
    assert set(inspect(persistence.engine).get_table_names()) == {"alembic_version"}
    persistence.dispose()


def test_schema_compiles_for_postgresql_and_sqlite():
    for dialect in (postgresql.dialect(), sqlite.dialect()):
        ddl = "\n".join(
            str(CreateTable(table).compile(dialect=dialect))
            for table in PersistenceBase.metadata.sorted_tables
        )
        assert "evaluation_tasks" in ddl
        assert "persisted_evaluations" in ddl
        assert "audit_records" in ddl
        assert "outbox_records" in ddl
        assert "vector_index_references" in ddl
        assert "vector_outbox_events" in ddl
        assert "vector_outbox_audits" in ddl
        assert "pickle" not in ddl.lower()


@pytest.mark.parametrize("adapter", ["memory", "sql"])
def test_memory_and_sql_adapters_preserve_contract_and_scope(
    adapter, tmp_path, ready_cv_json, ready_position_json
):
    persistence = (
        InMemoryPersistence() if adapter == "memory" else _sql_persistence(tmp_path)
    )
    service = _service(persistence)

    task = service.submit(
        _payload(ready_cv_json, ready_position_json), "parity-key", "tenant-a"
    ).task
    result = service.get_evaluation(task.evaluation_id, "tenant-a").result

    assert task.status == "succeeded"
    assert result.evaluation.model_dump(mode="json")["final_match_result"] is not None
    assert result.gap_analysis.generation_status == "completed"
    assert result.versions.evaluation_algorithm_version == (
        task.versions.evaluation_algorithm_version
    )
    assert service.get_task(task.task_id, "tenant-b").error_code == "TASK_NOT_FOUND"
    assert service.get_evaluation(task.evaluation_id, "tenant-b").error_code == (
        "EVALUATION_NOT_FOUND"
    )
    if isinstance(persistence, SQLAlchemyPersistence):
        persistence.dispose()


def test_sql_unique_constraint_rejects_duplicate_idempotency_tuple(
    tmp_path, ready_cv_json, ready_position_json
):
    persistence = _sql_persistence(tmp_path)
    service = _service(persistence)
    original = service.submit(
        _payload(ready_cv_json, ready_position_json),
        "unique-key",
        "tenant-a",
        execute_immediately=False,
    ).task
    duplicate = original.model_copy(update={"task_id": "task_duplicate_unique_constraint"})

    with pytest.raises(IntegrityError), persistence.unit_of_work() as uow:
        uow.tasks.save(duplicate)
        uow.commit()
    assert service.get_task(original.task_id, "tenant-a").task == original
    persistence.dispose()


def test_sql_task_and_outbox_share_one_transaction(
    tmp_path, ready_cv_json, ready_position_json
):
    persistence = _sql_persistence(tmp_path)
    service = _service(persistence)
    task = service.submit(
        _payload(ready_cv_json, ready_position_json),
        "sql-outbox",
        "tenant-a",
        execute_immediately=False,
        create_outbox=True,
    ).task

    with persistence.session_factory() as session:
        row = session.scalar(
            select(OutboxRecordRow).where(OutboxRecordRow.task_id == task.task_id)
        )
        assert row is not None
        assert row.access_scope == task.access_scope
        assert row.status == "pending"
        assert row.payload["task_id"] == task.task_id
    persistence.dispose()


def test_concurrent_sql_submission_creates_one_idempotent_task(
    tmp_path, ready_cv_json, ready_position_json
):
    persistence = _sql_persistence(tmp_path)
    service = _service(persistence)

    def submit():
        return service.submit(
            _payload(ready_cv_json, ready_position_json),
            "concurrent-key",
            "tenant-a",
            execute_immediately=False,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: submit(), range(16)))

    assert len({item.task.task_id for item in results}) == 1
    assert sum(item.created for item in results) == 1
    task = results[0].task
    assert task.status == "pending"
    assert task.attempt == 0
    persistence.dispose()


def test_sql_success_transaction_rolls_back_partial_evaluation(
    tmp_path, ready_cv_json, ready_position_json
):
    persistence = _sql_persistence(tmp_path)

    def fail_before_commit() -> None:
        raise RuntimeError("transaction failure")

    service = _service(persistence, before_success_commit=fail_before_commit)
    task = service.submit(
        _payload(ready_cv_json, ready_position_json), "rollback-key", "tenant-a"
    ).task

    assert task.status == "failed"
    with persistence.session_factory() as session:
        count = session.scalar(select(func.count()).select_from(PersistedEvaluationRow))
        assert count == 0
    persistence.dispose()


def test_sql_stale_state_and_versions_are_queryable(
    tmp_path, ready_cv_json, ready_position_json, clone
):
    persistence = _sql_persistence(tmp_path)
    service = _service(persistence)
    first = service.submit(
        _payload(ready_cv_json, ready_position_json), "stale-key", "tenant-a"
    ).task
    changed = clone(ready_position_json)
    changed["core_responsibilities"].append("versioned new responsibility")
    changed["profile_version"] = "position-source.v2"
    service.submit(_payload(ready_cv_json, changed), "stale-key", "tenant-a")

    old = service.get_evaluation(first.evaluation_id, "tenant-a").result
    assert old.stale is True
    assert old.stale_reason_codes == ("ALGORITHM_VERSION_CHANGED",)
    assert old.versions.signature == first.versions.signature
    persistence.dispose()


def test_persistence_configuration_defaults_to_memory_and_guards_sqlite(tmp_path):
    default = build_persistence({})
    assert default.provider == "memory"
    assert isinstance(default.resource, InMemoryPersistence)

    database_url = f"sqlite:///{(tmp_path / 'configured.db').as_posix()}"
    with pytest.raises(ValueError, match="explicit SQLite test mode"):
        build_persistence(
            {
                "MATCHING_PERSISTENCE_PROVIDER": "postgres",
                "MATCHING_DATABASE_URL": database_url,
            }
        )
    selected = build_persistence(
        {
            "MATCHING_PERSISTENCE_PROVIDER": "postgres",
            "MATCHING_DATABASE_URL": database_url,
            "MATCHING_PERSISTENCE_SQLITE_TEST_MODE": "true",
        }
    )
    assert isinstance(selected.resource, SQLAlchemyPersistence)
    selected.resource.dispose()

    postgres_environment = {
        "MATCHING_PERSISTENCE_PROVIDER": "postgres",
        "MATCHING_DATABASE_URL": "postgresql+psycopg://matching:secret@localhost/matching",
        "MATCHING_DATABASE_CONNECT_TIMEOUT_SECONDS": "0",
    }
    with pytest.raises(ValueError, match="CONNECT_TIMEOUT_SECONDS must be positive"):
        build_persistence(postgres_environment)


@pytest.mark.skipif(
    not os.getenv("MATCHING_TEST_POSTGRES_URL"),
    reason="dedicated PostgreSQL integration database is not configured",
)
@pytest.mark.postgres_integration
def test_live_postgresql_adapter_matches_sqlite_contract_behavior(
    ready_cv_json, ready_position_json
):
    database_url = os.environ["MATCHING_TEST_POSTGRES_URL"]
    _upgrade(database_url)
    persistence = SQLAlchemyPersistence.from_url(database_url, pool_pre_ping=True)
    try:
        service = _service(persistence)
        first = service.submit(
            _payload(ready_cv_json, ready_position_json),
            "live-postgres-parity",
            "postgres-test-scope",
        )
        replay = service.submit(
            _payload(ready_cv_json, ready_position_json),
            "live-postgres-parity",
            "postgres-test-scope",
        )
        assert first.created is True
        assert replay.created is False
        assert replay.task == first.task
        result = service.get_evaluation(first.task.evaluation_id, "postgres-test-scope")
        assert result.result.gap_analysis.generation_status == "completed"
    finally:
        persistence.dispose()
        _downgrade(database_url)
