from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.application.vector_indexing import (
    VectorIndexPlanningService,
    VectorOutboxLifecycleService,
)
from app.domain.feature_flags import FeatureFlagController, StageFlag
from app.domain.profiles import Evidence
from app.domain.vector_contracts import SemanticFragment
from app.domain.vector_indexing import VectorOutboxEvent, VectorOutboxPayload
from app.infrastructure.memory_repositories import (
    InMemoryPersistence,
    InMemoryVectorOutboxAuditRepository,
)
from app.infrastructure.sqlalchemy_repositories import SQLAlchemyPersistence

ROOT = Path(__file__).parents[1]


def _upgrade(database_url: str, revision: str = "head") -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, revision)


def _payload(
    *,
    fingerprint: str = "a" * 64,
    source_version: str = "cv.v1",
    correlation_id: str = "corr-1",
) -> VectorOutboxPayload:
    return VectorOutboxPayload(
        entity_type="cv",
        entity_id="cv:opaque-1",
        tenant_ref="tenant-a",
        profile_version=fingerprint,
        source_version=source_version,
        requested_embedding_revision="revision-1",
        correlation_id=correlation_id,
    )


def _fragment(
    *,
    fingerprint: str = "a" * 64,
    source_version: str = "cv.v1",
    fragment_id: str = "fragment-1",
) -> SemanticFragment:
    return SemanticFragment(
        tenant_ref="tenant-a",
        fragment_id=fragment_id,
        source_type="cv",
        target_type="candidate_cv",
        source_id="cv:opaque-1",
        source_version=source_version,
        source_profile_id=fingerprint,
        fragment_type="skill_context",
        normalized_text="Python services",
        evidence_ref=Evidence(source_id="cv:evidence:1", quote="Python services"),
        language="en",
        sequence=0,
        taxonomy_version="taxonomy.v1",
    )


def _plan(
    service: VectorIndexPlanningService,
    *,
    event_type: str = "cv_profile_published",
    payload: VectorOutboxPayload | None = None,
    fragments: tuple[SemanticFragment, ...] | None = None,
    max_attempts: int = 5,
):
    current_payload = payload or _payload()
    current_fragments = fragments if fragments is not None else (_fragment(),)
    return service.plan(
        event_type=event_type,
        payload=current_payload,
        fragments=current_fragments,
        embedding_model="BAAI/bge-m3",
        embedding_dimension=1024,
        max_attempts=max_attempts,
    )


def test_indexing_feature_flag_fails_closed_before_writes() -> None:
    persistence = InMemoryPersistence()
    controller = FeatureFlagController(
        flags={"indexing": StageFlag(enabled=True, percentage=0)}
    )
    service = VectorIndexPlanningService(
        persistence.unit_of_work, feature_flags=controller
    )
    with pytest.raises(ValueError, match="VECTOR_INDEXING_FEATURE_DISABLED"):
        _plan(service)


@pytest.mark.parametrize("adapter", ["memory", "sql"])
def test_reference_and_outbox_are_idempotent_and_share_transaction(
    adapter: str, tmp_path: Path
) -> None:
    if adapter == "memory":
        persistence = InMemoryPersistence()
    else:
        database_url = f"sqlite:///{(tmp_path / 'vector.db').as_posix()}"
        _upgrade(database_url)
        persistence = SQLAlchemyPersistence.from_url(database_url)
    service = VectorIndexPlanningService(persistence.unit_of_work)

    first = _plan(service)
    duplicate = _plan(
        service,
        payload=_payload(correlation_id="same-logical-event-new-correlation"),
    )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.event.event_id == first.event.event_id
    assert set(first.event.payload.model_dump()) == {
        "entity_type",
        "entity_id",
        "tenant_ref",
        "profile_version",
        "source_version",
        "source_entity_id",
        "target_type",
        "grant_id",
        "grant_version",
        "personal_tenant_ref",
        "enterprise_tenant_ref",
        "requested_embedding_revision",
        "correlation_id",
    }
    assert [item.reference_id for item in duplicate.references] == [
        item.reference_id for item in first.references
    ]
    assert duplicate.references == first.references
    with persistence.unit_of_work() as uow:
        assert len(uow.vector_references.list_for_entity("tenant-a", "cv", "cv:opaque-1")) == 1
        assert len(uow.vector_outbox_audits.list_for_event(first.event.event_id)) == 1
        uow.commit()
    if isinstance(persistence, SQLAlchemyPersistence):
        persistence.dispose()


def test_profile_update_supersedes_old_references_only_after_index_ack() -> None:
    persistence = InMemoryPersistence()
    service = VectorIndexPlanningService(persistence.unit_of_work)
    old = _plan(service)
    new_fingerprint = "b" * 64
    updated = _plan(
        service,
        event_type="cv_profile_updated",
        payload=_payload(
            fingerprint=new_fingerprint,
            source_version="cv.v2",
            correlation_id="corr-update",
        ),
        fragments=(
            _fragment(
                fingerprint=new_fingerprint,
                source_version="cv.v2",
                fragment_id="fragment-2",
            ),
        ),
    )
    with persistence.unit_of_work() as uow:
        superseded = uow.vector_references.get(old.references[0].reference_id)
        uow.commit()
    assert superseded is not None and superseded.status == "pending"

    lifecycle = VectorOutboxLifecycleService(persistence.unit_of_work)
    lifecycle.claim("worker-1", updated.event.event_id)
    acknowledged = lifecycle.acknowledge(
        updated.event.event_id,
        "worker-1",
        tuple(item.reference_id for item in updated.references),
    )
    assert acknowledged.outcome == "processed"
    with persistence.unit_of_work() as uow:
        superseded = uow.vector_references.get(old.references[0].reference_id)
        uow.commit()
    assert superseded is not None and superseded.status == "superseded"
    assert superseded.superseded_at is not None

    revoked = _plan(
        service,
        event_type="cv_profile_revoked",
        payload=_payload(
            fingerprint=new_fingerprint,
            source_version="cv.v2",
            correlation_id="corr-revoke",
        ),
        fragments=(),
    )
    duplicate_revoke = _plan(
        service,
        event_type="cv_profile_revoked",
        payload=_payload(
            fingerprint=new_fingerprint,
            source_version="cv.v2",
            correlation_id="corr-revoke-replayed",
        ),
        fragments=(),
    )

    with persistence.unit_of_work() as uow:
        old_reference = uow.vector_references.get(old.references[0].reference_id)
        current_reference = uow.vector_references.get(updated.references[0].reference_id)
        uow.commit()
    assert old_reference is not None and old_reference.status == "deleted"
    assert current_reference is not None and current_reference.status == "deleted"
    assert revoked.references[-1].reference_id == current_reference.reference_id
    assert duplicate_revoke.created is False
    assert duplicate_revoke.references == revoked.references
    lifecycle = VectorOutboxLifecycleService(persistence.unit_of_work)
    assert lifecycle.claim("delete-worker", revoked.event.event_id).outcome == "claimed"
    deleted_ids = tuple(item.reference_id for item in revoked.references)
    acknowledged = lifecycle.acknowledge(revoked.event.event_id, "delete-worker", deleted_ids)
    assert acknowledged.outcome == "processed"
    assert acknowledged.event.acknowledged_reference_ids == tuple(sorted(deleted_ids))


def test_outbox_is_rolled_back_when_transaction_fails(monkeypatch) -> None:
    persistence = InMemoryPersistence()
    service = VectorIndexPlanningService(persistence.unit_of_work)

    def reject_audit(_self, _record):
        raise RuntimeError("transaction rejected")

    monkeypatch.setattr(InMemoryVectorOutboxAuditRepository, "append", reject_audit)
    with pytest.raises(RuntimeError, match="transaction rejected"):
        _plan(service)

    assert persistence.vector_references == {}
    assert persistence.vector_outbox_events == {}
    assert persistence.vector_outbox_audit_records == []


def test_reindex_rejects_stale_profile_without_superseding_current() -> None:
    persistence = InMemoryPersistence()
    service = VectorIndexPlanningService(persistence.unit_of_work)
    _plan(service)
    current_fingerprint = "b" * 64
    updated = _plan(
        service,
        event_type="cv_profile_updated",
        payload=_payload(
            fingerprint=current_fingerprint,
            source_version="cv.v2",
            correlation_id="corr-current",
        ),
        fragments=(
            _fragment(
                fingerprint=current_fingerprint,
                source_version="cv.v2",
                fragment_id="fragment-current",
            ),
        ),
    )

    with pytest.raises(ValueError, match="stale profile"):
        _plan(
            service,
            event_type="vector_reindex_requested",
            payload=_payload(correlation_id="corr-stale-reindex"),
        )

    with persistence.unit_of_work() as uow:
        current = uow.vector_references.get(updated.references[0].reference_id)
        uow.commit()
    assert current is not None and current.status == "pending"


def test_reindex_returns_the_persisted_reference_state() -> None:
    now = [datetime(2026, 7, 29, tzinfo=timezone.utc)]
    persistence = InMemoryPersistence()
    service = VectorIndexPlanningService(persistence.unit_of_work, clock=lambda: now[0])
    published = _plan(service)
    now[0] += timedelta(minutes=1)

    reindexed = _plan(
        service,
        event_type="vector_reindex_requested",
        payload=_payload(correlation_id="corr-reindex"),
    )

    with persistence.unit_of_work() as uow:
        stored = uow.vector_references.get(published.references[0].reference_id)
        uow.commit()
    assert stored is not None
    assert reindexed.references == (stored,)
    assert stored.created_at == published.references[0].created_at
    assert stored.updated_at == now[0]


def test_ack_must_match_index_results_and_marks_references_indexed() -> None:
    now = [datetime(2026, 7, 29, tzinfo=timezone.utc)]
    persistence = InMemoryPersistence()
    planning = VectorIndexPlanningService(persistence.unit_of_work, clock=lambda: now[0])
    planned = _plan(planning)
    lifecycle = VectorOutboxLifecycleService(persistence.unit_of_work, clock=lambda: now[0])
    claimed = lifecycle.claim("worker-1", planned.event.event_id)
    assert claimed.outcome == "claimed"

    with pytest.raises(ValueError, match="ACK"):
        lifecycle.acknowledge(planned.event.event_id, "worker-1", ())
    with persistence.unit_of_work() as uow:
        assert uow.vector_outbox.get(planned.event.event_id).status == "claimed"
        uow.commit()

    reference_ids = tuple(item.reference_id for item in planned.references)
    result = lifecycle.acknowledge(planned.event.event_id, "worker-1", reference_ids)
    assert result.outcome == "processed"
    assert result.event.acknowledged_reference_ids == reference_ids
    with persistence.unit_of_work() as uow:
        reference = uow.vector_references.get(reference_ids[0])
        audits = uow.vector_outbox_audits.list_for_event(planned.event.event_id)
        uow.commit()
    assert reference is not None and reference.status == "indexed"
    assert reference.indexed_at == now[0]
    assert [item.to_status for item in audits] == [
        "pending",
        "claimed",
        "processed",
    ]


def test_failure_retries_then_moves_poison_event_to_dead_letter() -> None:
    now = [datetime(2026, 7, 29, tzinfo=timezone.utc)]
    persistence = InMemoryPersistence()
    planning = VectorIndexPlanningService(persistence.unit_of_work, clock=lambda: now[0])
    planned = _plan(planning, max_attempts=2)
    lifecycle = VectorOutboxLifecycleService(
        persistence.unit_of_work,
        retry_seconds=1,
        clock=lambda: now[0],
    )

    lifecycle.claim("worker-1", planned.event.event_id)
    first = lifecycle.fail(planned.event.event_id, "worker-1", "QDRANT_TIMEOUT")
    assert first.outcome == "retrying"
    with persistence.unit_of_work() as uow:
        reference = uow.vector_references.get(planned.references[0].reference_id)
        uow.commit()
    assert reference is not None and reference.status == "retrying"
    now[0] += timedelta(seconds=1)
    lifecycle.claim("worker-1", planned.event.event_id)
    final = lifecycle.fail(planned.event.event_id, "worker-1", "QDRANT_INVALID")

    assert final.outcome == "dead_letter"
    assert final.event.attempt == 2
    assert final.event.last_error_code == "QDRANT_INVALID"
    with persistence.unit_of_work() as uow:
        reference = uow.vector_references.get(planned.references[0].reference_id)
        uow.commit()
    assert reference is not None and reference.status == "failed"
    assert lifecycle.claim("worker-2", planned.event.event_id).outcome == "idle"
    with persistence.unit_of_work() as uow:
        audits = uow.vector_outbox_audits.list_for_event(planned.event.event_id)
        uow.commit()
    assert [item.to_status for item in audits] == [
        "pending",
        "claimed",
        "retrying",
        "claimed",
        "dead_letter",
    ]
    assert [item.from_status for item in audits] == [
        None,
        "pending",
        "claimed",
        "retrying",
        "claimed",
    ]


@pytest.mark.parametrize("adapter", ["memory", "sql"])
def test_expired_final_attempt_can_be_recovered_then_dead_lettered(
    adapter: str, tmp_path: Path
) -> None:
    now = [datetime(2026, 7, 29, tzinfo=timezone.utc)]
    if adapter == "memory":
        persistence = InMemoryPersistence()
    else:
        database_url = f"sqlite:///{(tmp_path / 'lease-recovery.db').as_posix()}"
        _upgrade(database_url)
        persistence = SQLAlchemyPersistence.from_url(database_url)
    planned = _plan(
        VectorIndexPlanningService(persistence.unit_of_work, clock=lambda: now[0]),
        max_attempts=1,
    )
    lifecycle = VectorOutboxLifecycleService(
        persistence.unit_of_work,
        lease_seconds=1,
        retry_seconds=0,
        clock=lambda: now[0],
    )
    first = lifecycle.claim("worker-crashed", planned.event.event_id)
    assert first.event is not None and first.event.attempt == 1

    now[0] += timedelta(seconds=2)
    recovered = lifecycle.claim("worker-recovery", planned.event.event_id)
    assert recovered.event is not None
    assert recovered.event.attempt == 1
    assert recovered.event.claimed_by == "worker-recovery"
    failed = lifecycle.fail(
        planned.event.event_id, "worker-recovery", "VECTOR_LEASE_RECOVERY_FAILED"
    )
    assert failed.outcome == "dead_letter"
    if isinstance(persistence, SQLAlchemyPersistence):
        persistence.dispose()


def test_sql_outbox_retry_then_ack_persists_reference_and_audit(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'lifecycle.db').as_posix()}"
    _upgrade(database_url)
    persistence = SQLAlchemyPersistence.from_url(database_url)
    now = [datetime(2026, 7, 29, tzinfo=timezone.utc)]
    planning = VectorIndexPlanningService(persistence.unit_of_work, clock=lambda: now[0])
    planned = _plan(planning, max_attempts=3)
    lifecycle = VectorOutboxLifecycleService(
        persistence.unit_of_work,
        retry_seconds=1,
        clock=lambda: now[0],
    )

    assert lifecycle.claim("sql-worker", planned.event.event_id).outcome == "claimed"
    assert (
        lifecycle.fail(planned.event.event_id, "sql-worker", "QDRANT_UNAVAILABLE").outcome
        == "retrying"
    )
    now[0] += timedelta(seconds=1)
    assert lifecycle.claim("sql-worker", planned.event.event_id).outcome == "claimed"
    reference_ids = tuple(item.reference_id for item in planned.references)
    processed = lifecycle.acknowledge(planned.event.event_id, "sql-worker", reference_ids)

    assert processed.outcome == "processed"
    with persistence.unit_of_work() as uow:
        stored_event = uow.vector_outbox.get(planned.event.event_id)
        stored_reference = uow.vector_references.get(reference_ids[0])
        audits = uow.vector_outbox_audits.list_for_event(planned.event.event_id)
        uow.commit()
    assert stored_event is not None and stored_event.status == "processed"
    assert stored_event.acknowledged_reference_ids == reference_ids
    assert stored_reference is not None and stored_reference.status == "indexed"
    assert [item.to_status for item in audits] == [
        "pending",
        "claimed",
        "retrying",
        "claimed",
        "processed",
    ]
    persistence.dispose()


def test_migration_upgrades_history_and_never_stores_vectors(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    _upgrade(database_url, "20260727_0003")
    _upgrade(database_url)
    _upgrade(database_url)
    persistence = SQLAlchemyPersistence.from_url(database_url)
    inspector = inspect(persistence.engine)

    assert {
        "vector_index_references",
        "vector_outbox_events",
        "vector_outbox_audits",
    } <= set(inspector.get_table_names())
    inspected_reference_columns = inspector.get_columns("vector_index_references")
    reference_columns = {item["name"] for item in inspected_reference_columns}
    assert "embedding" not in reference_columns
    assert "vector" not in reference_columns
    assert {
        "point_id",
        "embedding_model",
        "embedding_revision",
        "vector_schema_version",
        "status",
    } <= reference_columns
    reference_id = next(
        item for item in inspected_reference_columns if item["name"] == "reference_id"
    )
    assert reference_id["type"].length == 1024
    payload_columns = {item["name"] for item in inspector.get_columns("vector_outbox_events")}
    assert "payload" in payload_columns
    persistence.dispose()


def test_outbox_event_ids_are_unique_across_shared_dedup_prefixes() -> None:
    first = VectorOutboxEvent.create(
        event_type="cv_profile_published",
        payload=_payload().model_copy(
            update={"entity_id": "semantic_shadow_cv_001"}
        ),
    )
    second = VectorOutboxEvent.create(
        event_type="cv_profile_published",
        payload=_payload().model_copy(
            update={"entity_id": "semantic_shadow_cv_002"}
        ),
    )

    assert first.event_id != second.event_id
