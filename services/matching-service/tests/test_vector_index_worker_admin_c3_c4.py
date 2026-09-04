from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.vector_worker as vector_worker_module
from app.application.contract_mapping import map_cv_bundle
from app.application.vector_index_admin import (
    VectorIndexAdminService,
    VectorProfileEventRequest,
    VectorReconcileRequest,
    VectorReindexRequest,
)
from app.application.vector_index_worker import VectorIndexWorker
from app.application.vector_indexing import (
    VectorIndexPlanningService,
    VectorOutboxLifecycleResult,
    VectorOutboxLifecycleService,
)
from app.bootstrap.application import create_app
from app.domain.auth import AuthContext, derive_access_scope
from app.domain.semantic_fragments import fragment_cv_profile
from app.domain.vector_contracts import VectorContractViolation
from app.domain.vector_indexing import VectorOutboxPayload
from app.infrastructure.authentication import FakeAuthenticationProvider
from app.infrastructure.fake_vector_adapters import (
    FakeEmbeddingAdapter,
    FakeVectorStoreAdapter,
)
from app.infrastructure.memory_repositories import InMemoryPersistence
from app.infrastructure.memory_sources import (
    InMemoryCVProfileSource,
    InMemoryPositionProfileSource,
)
from app.ports.upstream_contracts import UpstreamTimeoutError
from app.vector_worker import VectorWorkerProcess, build_process


def _setup(payload: dict, *, max_attempts: int = 3):
    mapped = map_cv_bundle(payload).value
    assert mapped is not None
    storage = InMemoryPersistence()
    planner = VectorIndexPlanningService(storage.unit_of_work)
    fragments = fragment_cv_profile(mapped, tenant_ref="tenant-a")
    planned = planner.plan(
        event_type="cv_profile_published",
        payload=VectorOutboxPayload(
            entity_type="cv",
            entity_id=mapped.cv_id,
            tenant_ref="tenant-a",
            profile_version=mapped.profile_version or "profile-source.v1",
            source_version=mapped.source_version,
            requested_embedding_revision="revision-1",
            correlation_id="corr-c3",
        ),
        fragments=fragments,
        embedding_model="model-1",
        embedding_dimension=8,
        max_attempts=max_attempts,
    )
    source = InMemoryCVProfileSource({mapped.cv_id: payload})
    vectors = FakeVectorStoreAdapter()
    lifecycle = VectorOutboxLifecycleService(
        storage.unit_of_work, lease_seconds=10, retry_seconds=0
    )
    worker = VectorIndexWorker(
        unit_of_work=storage.unit_of_work,
        lifecycle=lifecycle,
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        embedding=FakeEmbeddingAdapter(model="model-1", revision="revision-1", dimension=8),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
        batch_size=2,
        heartbeat_interval_seconds=0.01,
    )
    return mapped, storage, planner, planned, source, vectors, lifecycle, worker


def test_worker_processes_batches_idempotently_and_admin_reports_status(
    upstream_cv_anonymized,
):
    mapped, storage, planner, planned, source, vectors, _lifecycle, worker = _setup(
        upstream_cv_anonymized
    )
    result = worker.run_once("worker-1")
    assert result.outcome == "processed"
    assert len(vectors.list_points(tenant_ref="tenant-a")) == len(planned.references)
    assert worker.run_once("worker-1").outcome == "idle"

    admin = VectorIndexAdminService(
        unit_of_work=storage.unit_of_work,
        planning=planner,
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    )
    status = admin.status()
    assert status["references"] == {"indexed": len(planned.references)}
    assert status["events"] == {"processed": 1}
    reindexed = admin.reindex(
        VectorReindexRequest(
            tenant_ref="tenant-a",
            entity_type="cv",
            entity_id=mapped.cv_id,
            correlation_id="manual-reindex",
        )
    )
    assert reindexed["selected"] == 1
    by_tenant = admin.reindex(
        VectorReindexRequest(tenant_ref="tenant-a", correlation_id="tenant-reindex")
    )
    assert by_tenant["selected"] == 1
    by_revision = admin.reindex(
        VectorReindexRequest(embedding_revision="revision-1", correlation_id="revision-reindex")
    )
    assert by_revision["selected"] == 1


def test_authoritative_profile_event_creates_transactional_vector_plan(
    upstream_cv_anonymized,
):
    mapped = map_cv_bundle(upstream_cv_anonymized).value
    assert mapped is not None
    storage = InMemoryPersistence()
    authoritative_cv = mapped.model_dump(mode="json")
    source = InMemoryCVProfileSource({mapped.cv_id: authoritative_cv})
    admin = VectorIndexAdminService(
        unit_of_work=storage.unit_of_work,
        planning=VectorIndexPlanningService(storage.unit_of_work),
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        vectors=FakeVectorStoreAdapter(),
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    )
    request = VectorProfileEventRequest(
        event_type="cv_profile_published",
        entity_type="cv",
        entity_id=mapped.cv_id,
        tenant_ref="tenant-a",
        profile_version=mapped.profile_version or "profile-source.v1",
        source_version=mapped.source_version,
        correlation_id="profile-publication-1",
    )

    first = admin.ingest_profile_event(request)
    duplicate = admin.ingest_profile_event(request)

    assert first["created"] is True
    assert duplicate == {**first, "created": False}
    assert admin.status()["events"] == {"pending": 1}


def test_profile_event_accepts_main_backend_snapshot_lineage_fields(
    upstream_cv_anonymized,
):
    mapped = map_cv_bundle(upstream_cv_anonymized).value
    assert mapped is not None
    storage = InMemoryPersistence()
    authoritative_cv = mapped.model_dump(mode="json")
    source = InMemoryCVProfileSource({mapped.cv_id: authoritative_cv})
    admin = VectorIndexAdminService(
        unit_of_work=storage.unit_of_work,
        planning=VectorIndexPlanningService(storage.unit_of_work),
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        vectors=FakeVectorStoreAdapter(),
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    )
    request = VectorProfileEventRequest(
        event_type="cv_profile_published",
        entity_type="cv",
        entity_id=mapped.cv_id,
        tenant_ref="tenant-a",
        target_type="candidate_cv",
        source_version=mapped.source_version,
        correlation_id="snapshot-lineage-1",
        snapshot_id="snapshot-1",
        snapshot_revision=1,
    )

    created = admin.ingest_profile_event(request)

    assert created["created"] is True


def test_enterprise_grant_projection_refresh_and_revoke_control_visibility(
    upstream_cv_anonymized,
):
    mapped = map_cv_bundle(upstream_cv_anonymized).value
    assert mapped is not None
    storage = InMemoryPersistence()
    authoritative_cv = mapped.model_dump(mode="json")
    source = InMemoryCVProfileSource({mapped.cv_id: authoritative_cv})
    vectors = FakeVectorStoreAdapter()
    planner = VectorIndexPlanningService(storage.unit_of_work)
    admin = VectorIndexAdminService(
        unit_of_work=storage.unit_of_work,
        planning=planner,
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    )
    lifecycle = VectorOutboxLifecycleService(
        storage.unit_of_work, lease_seconds=10, retry_seconds=0
    )
    worker = VectorIndexWorker(
        unit_of_work=storage.unit_of_work,
        lifecycle=lifecycle,
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        embedding=FakeEmbeddingAdapter(model="model-1", revision="revision-1", dimension=8),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
        batch_size=20,
    )
    base = {
        "entity_type": "cv",
        "entity_id": f"{mapped.cv_id}@grant:grant-1",
        "source_entity_id": mapped.cv_id,
        "tenant_ref": "enterprise-tenant",
        "target_type": "candidate_cv",
        "grant_id": "grant-1",
        "grant_version": 1,
        "personal_tenant_ref": "personal-tenant",
        "enterprise_tenant_ref": "enterprise-tenant",
    }
    created = admin.ingest_profile_event(
        VectorProfileEventRequest(
            event_type="cv_profile_published",
            correlation_id="grant-created",
            **base,
        )
    )
    assert worker.run_once("projection-worker", created["event_id"]).outcome == "processed"
    first_active = {
        point.point_id
        for point in vectors.list_points(tenant_ref="enterprise-tenant")
        if point.active
    }
    assert first_active
    with storage.unit_of_work() as uow:
        references = uow.vector_references.list_for_entity(
            "enterprise-tenant", "cv", base["entity_id"]
        )
        uow.commit()
    assert all(
        reference.grant_id == "grant-1"
        and reference.grant_version == 1
        and reference.personal_tenant_ref == "personal-tenant"
        for reference in references
    )

    updated_payload = deepcopy(authoritative_cv)
    updated_payload["source_version"] = "cv.updated.v2"
    updated_payload.pop("profile_version")
    source._profiles[mapped.cv_id] = updated_payload
    updated = admin.ingest_profile_event(
        VectorProfileEventRequest(
            event_type="cv_profile_updated",
            correlation_id="profile-updated",
            **base,
        )
    )
    assert worker.run_once("projection-worker", updated["event_id"]).outcome == "processed"
    points = vectors.list_points(tenant_ref="enterprise-tenant")
    assert all(not point.active for point in points if point.point_id in first_active)
    assert any(point.active and point.point_id not in first_active for point in points)

    revoked = admin.ingest_profile_event(
        VectorProfileEventRequest(
            event_type="cv_profile_revoked",
            correlation_id="grant-revoked",
            **{**base, "grant_version": 2},
        )
    )
    assert worker.run_once("projection-worker", revoked["event_id"]).outcome == "processed"
    assert vectors.list_points(tenant_ref="enterprise-tenant") == ()


def test_enterprise_job_profile_event_indexes_in_enterprise_tenant(
    ready_position_json,
):
    storage = InMemoryPersistence()
    vectors = FakeVectorStoreAdapter()
    position_source = InMemoryPositionProfileSource(
        enterprise_profiles={"job-1": ready_position_json}
    )
    admin = VectorIndexAdminService(
        unit_of_work=storage.unit_of_work,
        planning=VectorIndexPlanningService(storage.unit_of_work),
        cv_source=InMemoryCVProfileSource(),
        position_source=position_source,
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    )
    planned = admin.ingest_profile_event(
        VectorProfileEventRequest(
            event_type="position_profile_published",
            entity_type="position",
            entity_id="job-1",
            tenant_ref="enterprise-tenant",
            target_type="enterprise_job",
            correlation_id="job-published",
        )
    )
    worker = VectorIndexWorker(
        unit_of_work=storage.unit_of_work,
        lifecycle=VectorOutboxLifecycleService(
            storage.unit_of_work, lease_seconds=10, retry_seconds=0
        ),
        cv_source=InMemoryCVProfileSource(),
        position_source=position_source,
        embedding=FakeEmbeddingAdapter(model="model-1", revision="revision-1", dimension=8),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
        batch_size=20,
    )
    assert worker.run_once("job-worker", planned["event_id"]).outcome == "processed"
    points = vectors.list_points(tenant_ref="enterprise-tenant")
    assert points and all(point.active for point in points)


def test_worker_rejects_embedding_fragment_reordering(upstream_cv_anonymized):
    _mapped, storage, _planner, _planned, source, vectors, lifecycle, _worker = _setup(
        upstream_cv_anonymized
    )

    class ReorderedEmbedding:
        def __init__(self):
            self.delegate = FakeEmbeddingAdapter(
                model="model-1", revision="revision-1", dimension=8
            )

        def embed(self, request):
            result = self.delegate.embed(request)
            return result.model_copy(update={"fragment_ids": result.fragment_ids[::-1]})

    worker = VectorIndexWorker(
        unit_of_work=storage.unit_of_work,
        lifecycle=lifecycle,
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        embedding=ReorderedEmbedding(),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
        batch_size=2,
    )
    result = worker.run_once("worker-1")
    assert result.error_code == "EMBEDDING_RESPONSE_INVALID"
    assert vectors.list_points() == ()


def test_ack_failure_keeps_points_hidden_and_reconcile_deactivates_exposure(
    upstream_cv_anonymized,
):
    _mapped, storage, planner, _planned, source, vectors, _lifecycle, _worker = _setup(
        upstream_cv_anonymized
    )

    class AckFailureLifecycle(VectorOutboxLifecycleService):
        def acknowledge(self, *_args, **_kwargs):
            raise RuntimeError("postgres ack failed")

    lifecycle = AckFailureLifecycle(storage.unit_of_work, lease_seconds=10, retry_seconds=0)
    worker = VectorIndexWorker(
        unit_of_work=storage.unit_of_work,
        lifecycle=lifecycle,
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        embedding=FakeEmbeddingAdapter(model="model-1", revision="revision-1", dimension=8),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
        batch_size=20,
    )
    result = worker.run_once("worker-ack-failure")
    points = vectors.list_points(tenant_ref="tenant-a")
    assert result.outcome == "retrying"
    assert points and all(not point.active for point in points)

    vectors.activate(
        tenant_ref="tenant-a", point_ids=tuple(point.point_id for point in points)
    )
    admin = VectorIndexAdminService(
        unit_of_work=storage.unit_of_work,
        planning=planner,
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    )
    repaired = admin.reconcile(
        VectorReconcileRequest(
            tenant_ref="tenant-a", repair=True, correlation_id="ack-reconcile"
        )
    )
    assert repaired["issue_counts"] == {
        "unacknowledged_point_active": len(points)
    }
    assert all(
        not point.active for point in vectors.list_points(tenant_ref="tenant-a")
    )


def test_worker_does_not_upsert_after_claim_is_lost(upstream_cv_anonymized, monkeypatch):
    _mapped, _storage, _planner, planned, _source, vectors, lifecycle, worker = _setup(
        upstream_cv_anonymized
    )
    original = lifecycle.mark_references

    def lose_before_upsert(event_id, worker_id, status):
        if status == "upserting":
            return VectorOutboxLifecycleResult(outcome="lost_claim")
        return original(event_id, worker_id, status)

    monkeypatch.setattr(lifecycle, "mark_references", lose_before_upsert)
    result = worker.run_once("worker-1", planned.event.event_id)
    assert result.outcome == "lost_claim"
    assert vectors.list_points() == ()


def test_worker_does_not_ack_partial_upsert_confirmation(upstream_cv_anonymized):
    mapped, storage, _planner, planned, source, vectors, lifecycle, _worker = _setup(
        upstream_cv_anonymized
    )

    class PartialConfirmationStore:
        def upsert(self, records):
            return vectors.upsert(records)[:-1]

        def deactivate(self, **kwargs):
            return vectors.deactivate(**kwargs)

        def delete(self, **kwargs):
            return vectors.delete(**kwargs)

        def list_points(self, **kwargs):
            return vectors.list_points(**kwargs)

        def health(self):
            return vectors.health()

        def search(self, query):
            return vectors.search(query)

    worker = VectorIndexWorker(
        unit_of_work=storage.unit_of_work,
        lifecycle=lifecycle,
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        embedding=FakeEmbeddingAdapter(model="model-1", revision="revision-1", dimension=8),
        vectors=PartialConfirmationStore(),
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    )
    result = worker.run_once("worker-1", planned.event.event_id)
    assert result.outcome == "retrying"
    assert result.error_code == "VECTOR_UPSERT_PARTIAL"
    with storage.unit_of_work() as uow:
        event = uow.vector_outbox.get(planned.event.event_id)
        references = uow.vector_references.list_for_entity("tenant-a", "cv", mapped.cv_id)
        uow.commit()
    assert event is not None and event.status == "retrying"
    assert {item.status for item in references} == {"retrying"}


def test_worker_discards_stale_lineage_without_writing_points(upstream_cv_anonymized):
    mapped, storage, _planner, _planned, _source, vectors, lifecycle, _worker = _setup(
        upstream_cv_anonymized
    )
    changed = deepcopy(upstream_cv_anonymized)
    changed["source_version"] = "cv-new-version"
    worker = VectorIndexWorker(
        unit_of_work=storage.unit_of_work,
        lifecycle=lifecycle,
        cv_source=InMemoryCVProfileSource({mapped.cv_id: changed}),
        position_source=InMemoryPositionProfileSource(),
        embedding=FakeEmbeddingAdapter(model="model-1", revision="revision-1", dimension=8),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    )
    assert worker.run_once("worker-1").outcome == "stale"
    assert vectors.list_points() == ()


def test_worker_rejects_profile_that_is_not_ready(upstream_cv_anonymized):
    mapped, storage, _planner, _planned, _source, vectors, lifecycle, _worker = _setup(
        upstream_cv_anonymized
    )
    pending = deepcopy(upstream_cv_anonymized)
    pending["review_status"] = "pending"
    worker = VectorIndexWorker(
        unit_of_work=storage.unit_of_work,
        lifecycle=lifecycle,
        cv_source=InMemoryCVProfileSource({mapped.cv_id: pending}),
        position_source=InMemoryPositionProfileSource(),
        embedding=FakeEmbeddingAdapter(model="model-1", revision="revision-1", dimension=8),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    )
    result = worker.run_once("worker-1")
    assert result.error_code == "PROFILE_NOT_READY"
    assert vectors.list_points() == ()


class _RejectedEmbedding:
    def embed(self, request):
        raise VectorContractViolation("EMBEDDING_UNAVAILABLE", "offline")


def test_worker_retries_then_dead_letters_and_admin_requeues(upstream_cv_anonymized):
    mapped, storage, planner, planned, source, vectors, lifecycle, _worker = _setup(
        upstream_cv_anonymized, max_attempts=2
    )
    worker = VectorIndexWorker(
        unit_of_work=storage.unit_of_work,
        lifecycle=lifecycle,
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        embedding=_RejectedEmbedding(),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    )
    assert worker.run_once("worker-1").outcome == "retrying"
    assert worker.run_once("worker-1").outcome == "dead_letter"
    admin = VectorIndexAdminService(
        unit_of_work=storage.unit_of_work,
        planning=planner,
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    )
    assert admin.retry_failed((planned.event.event_id,)) == {
        "requested": 1,
        "retried": 1,
    }
    with storage.unit_of_work() as uow:
        audits = uow.vector_outbox_audits.list_for_event(planned.event.event_id)
        uow.commit()
    assert audits[-1].from_status == "dead_letter"
    assert audits[-1].to_status == "retrying"
    assert audits[-1].reason_code == "MANUAL_RETRY"


def test_reconcile_detects_missing_point_and_repairs_by_reindex(upstream_cv_anonymized):
    _mapped, storage, planner, planned, source, vectors, _lifecycle, worker = _setup(
        upstream_cv_anonymized
    )
    assert worker.run_once("worker-1").outcome == "processed"
    missing = planned.references[0]
    vectors.delete(tenant_ref="tenant-a", point_ids=(missing.point_id,))
    admin = VectorIndexAdminService(
        unit_of_work=storage.unit_of_work,
        planning=planner,
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    )
    dry_run = admin.reconcile(
        VectorReconcileRequest(tenant_ref="tenant-a", correlation_id="reconcile-1")
    )
    assert dry_run["issue_counts"] == {"point_missing": 1}
    repaired = admin.reconcile(
        VectorReconcileRequest(tenant_ref="tenant-a", repair=True, correlation_id="reconcile-2")
    )
    assert repaired["repaired"] == 1


def test_reconcile_detects_indexed_point_that_became_inactive(upstream_cv_anonymized):
    _mapped, storage, planner, planned, source, vectors, _lifecycle, worker = _setup(
        upstream_cv_anonymized
    )
    assert worker.run_once("worker-1").outcome == "processed"
    reference = planned.references[0]
    vectors.deactivate(tenant_ref="tenant-a", point_ids=(reference.point_id,))
    admin = VectorIndexAdminService(
        unit_of_work=storage.unit_of_work,
        planning=planner,
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    )
    result = admin.reconcile(
        VectorReconcileRequest(tenant_ref="tenant-a", correlation_id="inactive-point")
    )
    assert result["issue_counts"] == {"indexed_point_inactive": 1}


def test_old_revision_is_deactivated_only_by_explicit_repair(upstream_cv_anonymized):
    _mapped, storage, planner, planned, source, vectors, _lifecycle, worker = _setup(
        upstream_cv_anonymized
    )
    assert worker.run_once("worker-1").outcome == "processed"
    admin = VectorIndexAdminService(
        unit_of_work=storage.unit_of_work,
        planning=planner,
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-2",
        embedding_dimension=8,
    )
    result = admin.reconcile(
        VectorReconcileRequest(
            tenant_ref="tenant-a",
            embedding_revision="revision-1",
            repair=True,
            deactivate_revision=True,
            correlation_id="deactivate-old-revision",
        )
    )
    assert result["deactivated"] == len(planned.references)
    assert all(not point.active for point in vectors.list_points())
    with storage.unit_of_work() as uow:
        references = uow.vector_references.list_all(
            tenant_ref="tenant-a", embedding_revision="revision-1"
        )
        uow.commit()
    assert {item.status for item in references} == {"superseded"}

    with pytest.raises(ValueError, match="active embedding revision"):
        VectorIndexAdminService(
            unit_of_work=storage.unit_of_work,
            planning=planner,
            cv_source=source,
            position_source=InMemoryPositionProfileSource(),
            vectors=vectors,
            embedding_model="model-1",
            embedding_revision="revision-2",
            embedding_dimension=8,
        ).reconcile(
            VectorReconcileRequest(
                embedding_revision="revision-2",
                repair=True,
                deactivate_revision=True,
                correlation_id="reject-active-revision",
            )
        )


def test_worker_revocation_removes_all_derived_points(upstream_cv_anonymized):
    mapped, storage, planner, _planned, _source, vectors, lifecycle, worker = _setup(
        upstream_cv_anonymized
    )
    assert worker.run_once("worker-1").outcome == "processed"
    revoked = planner.plan(
        event_type="cv_profile_revoked",
        payload=VectorOutboxPayload(
            entity_type="cv",
            entity_id=mapped.cv_id,
            tenant_ref="tenant-a",
            profile_version=mapped.profile_version or "profile-source.v1",
            source_version=mapped.source_version,
            requested_embedding_revision="revision-1",
            correlation_id="revoke-c3",
        ),
        fragments=(),
        embedding_model="model-1",
        embedding_dimension=8,
    )
    revocation_worker = VectorIndexWorker(
        unit_of_work=storage.unit_of_work,
        lifecycle=lifecycle,
        cv_source=InMemoryCVProfileSource(),
        position_source=InMemoryPositionProfileSource(),
        embedding=FakeEmbeddingAdapter(model="model-1", revision="revision-1", dimension=8),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    )
    assert revocation_worker.run_once("worker-1", revoked.event.event_id).outcome == "processed"
    assert vectors.list_points() == ()


class _AdminStub:
    def status(self):
        return {"events": {}}

    def reindex(self, payload):
        return {"selected": 0, "event_ids": []}

    def reconcile(self, payload):
        return {"issues": [], "issue_counts": {}, "repaired": 0}

    def retry_failed(self, event_ids):
        return {"requested": len(event_ids), "retried": 0}


def _context(role: str):
    roles = frozenset({role})
    return AuthContext(
        subject_id=f"subject-{role}",
        tenant_id="platform",
        roles=roles,
        access_scope=derive_access_scope(f"subject-{role}", "platform", roles),
        token_id=f"token-{role}",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def test_internal_vector_endpoints_require_service_identity():
    service = _context("matching.service")
    candidate = _context("candidate")
    app = create_app(
        authentication_provider=FakeAuthenticationProvider(
            {"service": service, "candidate": candidate}
        ),
        vector_index_admin_service=_AdminStub(),
    )
    client = TestClient(app)
    denied = client.get(
        "/internal/vector-index/status",
        headers={"Authorization": "Bearer candidate"},
    )
    allowed = client.get(
        "/internal/vector-index/status",
        headers={"Authorization": "Bearer service"},
    )
    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["data"] == {"events": {}}
    headers = {"Authorization": "Bearer service"}
    assert (
        client.post(
            "/internal/vector-index/reindex",
            headers=headers,
            json={"tenant_ref": "tenant-a", "correlation_id": "api-reindex"},
        ).status_code
        == 200
    )

    disabled = create_app(authentication_provider=FakeAuthenticationProvider({"service": service}))
    assert (
        TestClient(disabled)
        .get(
            "/internal/vector-index/status",
            headers={"Authorization": "Bearer service"},
        )
        .status_code
        == 503
    )
    assert (
        client.post(
            "/internal/vector-index/reconcile",
            headers=headers,
            json={"tenant_ref": "tenant-a", "correlation_id": "api-reconcile"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/internal/vector-index/retry-failed",
            headers=headers,
            json={"event_ids": ["event-1"]},
        ).status_code
        == 200
    )


def test_vector_worker_process_stops_after_one_iteration(monkeypatch):
    class WorkerStub:
        process = None

        def run_batch(self, worker_id, *, limit):
            assert worker_id == "worker-process"
            assert limit == 1
            self.process.stop()
            return ()

    stub = WorkerStub()
    process = VectorWorkerProcess(stub, "worker-process", object())
    stub.process = process
    monkeypatch.setenv("MATCHING_VECTOR_WORKER_EVENT_BATCH_SIZE", "1")
    monkeypatch.setenv("MATCHING_VECTOR_WORKER_IDLE_SECONDS", "0")
    assert process.readiness_status == "ready"
    process.run_forever()
    assert process.is_stopping is True
    assert process.readiness_status == "stopping"


def test_vector_worker_process_requires_explicit_configuration(monkeypatch):
    for name in (
        "MATCHING_DATABASE_URL",
        "MATCHING_CV_SOURCE_URL",
        "MATCHING_POSITION_SOURCE_URL",
        "MATCHING_UPSTREAM_SERVICE_TOKEN",
        "MATCHING_VECTOR_EMBEDDING_MODEL",
        "MATCHING_VECTOR_EMBEDDING_REVISION",
        "MATCHING_EMBEDDING_ENDPOINT",
        "MATCHING_QDRANT_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    try:
        build_process()
    except ValueError as exc:
        assert "configuration is incomplete" in str(exc)
    else:
        raise AssertionError("incomplete vector worker configuration must fail")


def test_vector_worker_process_builds_from_explicit_environment(monkeypatch):
    values = {
        "MATCHING_DATABASE_URL": "postgresql+psycopg://unused",
        "MATCHING_CV_SOURCE_URL": "http://cv",
        "MATCHING_POSITION_SOURCE_URL": "http://position",
        "MATCHING_UPSTREAM_SERVICE_TOKEN": "service-token",
        "MATCHING_VECTOR_EMBEDDING_MODEL": "model-1",
        "MATCHING_VECTOR_EMBEDDING_REVISION": "revision-1",
        "MATCHING_EMBEDDING_ENDPOINT": "http://embedding",
        "MATCHING_QDRANT_URL": "http://qdrant",
        "MATCHING_QDRANT_DIMENSION": "8",
        "MATCHING_VECTOR_WORKER_ID": "configured-worker",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    storage = InMemoryPersistence()
    monkeypatch.setattr(
        vector_worker_module,
        "build_persistence",
        lambda env: SimpleNamespace(unit_of_work=storage.unit_of_work),
    )
    monkeypatch.setattr(
        vector_worker_module,
        "HttpCVProfileSource",
        lambda *args, **kwargs: InMemoryCVProfileSource(),
    )
    monkeypatch.setattr(
        vector_worker_module,
        "HttpPositionProfileSource",
        lambda *args, **kwargs: InMemoryPositionProfileSource(),
    )
    monkeypatch.setattr(
        vector_worker_module,
        "HttpEmbeddingAdapter",
        lambda *args, **kwargs: FakeEmbeddingAdapter(
            model="model-1", revision="revision-1", dimension=8
        ),
    )
    monkeypatch.setattr(
        vector_worker_module,
        "QdrantVectorStoreAdapter",
        lambda *args, **kwargs: FakeVectorStoreAdapter(),
    )
    process, metrics = build_process()
    assert process.readiness_status == "ready"
    assert metrics.render() == "\n"


def test_vector_worker_failure_boundaries_and_validation(upstream_cv_anonymized):
    _mapped, storage, _planner, _planned, _source, vectors, lifecycle, _worker = _setup(
        upstream_cv_anonymized
    )
    missing_source_worker = VectorIndexWorker(
        unit_of_work=storage.unit_of_work,
        lifecycle=lifecycle,
        cv_source=InMemoryCVProfileSource(),
        position_source=InMemoryPositionProfileSource(),
        embedding=FakeEmbeddingAdapter(model="model-1", revision="revision-1", dimension=8),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    )
    assert missing_source_worker.run_once("worker-1").error_code == ("UPSTREAM_CONTRACT_NOT_FOUND")
    try:
        missing_source_worker.run_batch("worker-1", limit=0)
    except ValueError as exc:
        assert "batch limit" in str(exc)
    else:
        raise AssertionError("non-positive event batch must fail")

    common = {
        "unit_of_work": storage.unit_of_work,
        "lifecycle": lifecycle,
        "cv_source": InMemoryCVProfileSource(),
        "position_source": InMemoryPositionProfileSource(),
        "embedding": FakeEmbeddingAdapter(model="model-1", revision="revision-1", dimension=8),
        "vectors": vectors,
        "embedding_model": "model-1",
        "embedding_revision": "revision-1",
        "embedding_dimension": 8,
    }
    for overrides in (
        {"embedding_model": ""},
        {"embedding_dimension": 0},
        {"batch_size": 0},
        {"heartbeat_interval_seconds": 0},
    ):
        with pytest.raises(ValueError):
            VectorIndexWorker(**(common | overrides))


def test_vector_worker_reports_timeout_and_unavailable_revision(upstream_cv_anonymized):
    mapped, storage, _planner, _planned, source, vectors, lifecycle, _worker = _setup(
        upstream_cv_anonymized
    )

    class TimeoutSource:
        def fetch_cv_profile(self, cv_id):
            raise UpstreamTimeoutError("timeout")

    timed_out = VectorIndexWorker(
        unit_of_work=storage.unit_of_work,
        lifecycle=lifecycle,
        cv_source=TimeoutSource(),
        position_source=InMemoryPositionProfileSource(),
        embedding=FakeEmbeddingAdapter(model="model-1", revision="revision-1", dimension=8),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="revision-1",
        embedding_dimension=8,
    ).run_once("worker-1")
    assert timed_out.error_code == "UPSTREAM_TIMEOUT"

    unavailable = VectorIndexWorker(
        unit_of_work=storage.unit_of_work,
        lifecycle=lifecycle,
        cv_source=source,
        position_source=InMemoryPositionProfileSource(),
        embedding=FakeEmbeddingAdapter(model="model-1", revision="other-revision", dimension=8),
        vectors=vectors,
        embedding_model="model-1",
        embedding_revision="other-revision",
        embedding_dimension=8,
    ).run_once("worker-1")
    assert unavailable.error_code == "EMBEDDING_REVISION_UNAVAILABLE"
    assert unavailable.event_id is not None
    assert mapped.cv_id


@pytest.mark.parametrize(
    "payload",
    [
        {"entity_type": "cv", "correlation_id": "missing-id"},
        {"entity_type": "cv", "entity_id": "cv-1", "correlation_id": "missing-tenant"},
        {"correlation_id": "missing-selector"},
    ],
)
def test_reindex_requires_a_complete_selector(payload):
    with pytest.raises(ValueError):
        VectorReindexRequest.model_validate(payload)


def test_vector_worker_main_starts_and_stops_monitor(monkeypatch):
    events: list[str] = []

    class ProcessStub:
        def run_forever(self):
            events.append("run")

        def stop(self):
            events.append("stop")

    class MonitorStub:
        def __init__(self, *args, **kwargs):
            events.append("monitor-created")

        def start(self):
            events.append("monitor-start")

        def stop(self):
            events.append("monitor-stop")

    monkeypatch.setattr(
        vector_worker_module,
        "build_process",
        lambda: (ProcessStub(), object()),
    )
    monkeypatch.setattr(vector_worker_module, "WorkerMonitoringServer", MonitorStub)
    monkeypatch.setattr(vector_worker_module.signal, "signal", lambda *args: None)
    assert vector_worker_module.main() == 0
    assert events == [
        "monitor-created",
        "monitor-start",
        "run",
        "stop",
        "monitor-stop",
    ]
