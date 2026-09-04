from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.application import ImportPublishedJDFactUseCase
from app.application.contracts import (
    CatalogSnapshotRefInput,
    ImportPublishedJDFactCommand,
    ValidationLineageInput,
)
from app.application.errors import ConflictError, ValidationError
from app.application.lineage_mapper import map_published_fact_lineage
from app.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from app.models import AuditLog, PublishedFactImport, PublishedFactLineageRecord
from tests.test_published_fact_ingestion import published_command


def validation_lineage(conclusion: str = "pass") -> ValidationLineageInput:
    return {
        "data_validation_task_id": "DVT_1",
        "validation_report_id": "DVR_1",
        "validated_bundle_snapshot_id": "VBS_1",
        "validation_policy_version": "cn-jd-policy.2026-07",
        "validation_conclusion": conclusion,
        "bundle_lineage_version": "bundle-lineage-v1",
    }


def catalog_ref(version: str = "cn-capability.2026-07") -> CatalogSnapshotRefInput:
    return {
        "source": "main-system-capability-catalog",
        "catalog_version": version,
        "content_hash": "a1b2c3d4" * 8,
        "source_version": "published-jd-fact-source-v1",
        "effective_at": "2026-07-01T00:00:00+08:00",
        "status": "active",
    }


def command_with_lineage(*, conclusion: str = "pass", version: str = "cn-capability.2026-07"):
    validation = validation_lineage(conclusion)
    if conclusion == "block":
        validation["validated_bundle_snapshot_id"] = None
    base = published_command()
    return ImportPublishedJDFactCommand(
        base.fact,
        map_published_fact_lineage(
            validation=validation,
            catalog=catalog_ref(version),
        ),
    )


def use_case(db):
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    return ImportPublishedJDFactUseCase(
        lambda: SqlAlchemyUnitOfWork(factory)
    )


def test_internal_mapper_preserves_absence_and_rejects_shape_drift():
    assert map_published_fact_lineage().present is False
    invalid = validation_lineage()
    invalid["unexpected"] = "not-accepted"
    with pytest.raises(ValidationError) as exc:
        map_published_fact_lineage(validation=invalid)
    assert exc.value.error_code == "INVALID_LINEAGE_FIELDS"


def test_blocked_validation_never_creates_authoritative_import(db):
    with pytest.raises(ValidationError) as exc:
        use_case(db).execute(command_with_lineage(conclusion="block"))
    assert exc.value.error_code == "VALIDATION_BLOCKED"
    assert db.scalar(select(PublishedFactImport)) is None
    assert db.scalar(select(PublishedFactLineageRecord)) is None


def test_lineage_is_immutable_audited_and_idempotent(db, users):
    command = command_with_lineage()
    application = use_case(db)
    first = application.execute(
        command,
        actor_id=users["admin"].id,
        trace_id="phase1-lineage",
        context={"source": "phase1-test"},
    )
    second = application.execute(command)
    assert first.idempotent is False
    assert second.idempotent is True

    lineage = db.scalar(select(PublishedFactLineageRecord))
    assert lineage is not None
    assert lineage.validation_conclusion == "pass"
    assert lineage.catalog_version == "cn-capability.2026-07"
    assert lineage.lineage_lineage_version == "DVR_1:cn-capability.2026-07"
    assert len(db.scalars(select(PublishedFactLineageRecord)).all()) == 1

    audit = db.scalar(select(AuditLog).where(AuditLog.trace_id == "phase1-lineage"))
    assert audit is not None
    assert audit.after_snapshot["lineage"]["validation"][
        "validation_report_id"
    ] == "DVR_1"
    assert audit.after_snapshot["lineage"]["catalog"][
        "catalog_version"
    ] == "cn-capability.2026-07"

    lineage.catalog_version = "mutated"
    with pytest.raises(ValueError, match="lineage is immutable"):
        db.commit()
    db.rollback()


def test_same_fact_version_cannot_silently_replace_lineage(db):
    application = use_case(db)
    application.execute(command_with_lineage())
    with pytest.raises(ConflictError) as exc:
        application.execute(command_with_lineage(version="cn-capability.2026-08"))
    assert exc.value.error_code == "PUBLISHED_FACT_LINEAGE_CONFLICT"


def test_v1_import_without_internal_lineage_remains_compatible(db):
    result = use_case(db).execute(published_command())
    assert result.idempotent is False
    assert db.scalar(select(PublishedFactImport)) is not None
    assert db.scalar(select(PublishedFactLineageRecord)) is None


def test_0010_explicitly_rejects_destructive_downgrade():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0010_published_fact_lineage.py"
    )
    spec = spec_from_file_location("migration_0010", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    with pytest.raises(
        RuntimeError,
        match="Migration 0010 is forward-only and cannot be downgraded",
    ):
        migration.downgrade()
