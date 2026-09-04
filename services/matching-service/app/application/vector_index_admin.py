"""C4 service-only operations for rebuilding and reconciling derived vectors."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from pydantic import Field, model_validator

from app.application.contract_mapping import (
    map_authoritative_cv_profile,
    map_authoritative_position_profile,
)
from app.application.validation import ProfileValidationService
from app.application.vector_indexing import VectorIndexPlanningService
from app.domain.profiles import ImmutableDTO
from app.domain.semantic_fragments import fragment_cv_profile, fragment_position_profile
from app.domain.vector_indexing import (
    VectorEntityType,
    VectorEventType,
    VectorOutboxPayload,
    vector_outbox_audit,
)
from app.ports.observability import EventLogger, NullEventLogger
from app.ports.profile_sources import CVProfileSource, PositionProfileSource
from app.ports.repositories import UnitOfWorkFactory
from app.ports.vectors import VectorStorePort


class VectorReindexRequest(ImmutableDTO):
    tenant_ref: str | None = Field(default=None, min_length=1, max_length=200)
    entity_type: str | None = Field(default=None, pattern=r"^(cv|position)$")
    entity_id: str | None = Field(default=None, min_length=1, max_length=200)
    embedding_revision: str | None = Field(default=None, min_length=1, max_length=200)
    correlation_id: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_selector(self):
        if (self.entity_type is None) != (self.entity_id is None):
            raise ValueError("entity_type and entity_id must be supplied together")
        if self.entity_id is not None and self.tenant_ref is None:
            raise ValueError("single-entity reindex requires tenant_ref")
        if self.tenant_ref is None and self.embedding_revision is None:
            raise ValueError("reindex requires an entity, tenant or embedding revision")
        return self


class VectorReconcileRequest(ImmutableDTO):
    tenant_ref: str | None = Field(default=None, min_length=1, max_length=200)
    embedding_revision: str | None = Field(default=None, min_length=1, max_length=200)
    repair: bool = False
    deactivate_revision: bool = False
    correlation_id: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_mutation(self):
        if self.deactivate_revision and (not self.repair or self.embedding_revision is None):
            raise ValueError("deactivate_revision requires repair=true and embedding_revision")
        return self


class VectorRetryFailedRequest(ImmutableDTO):
    event_ids: tuple[str, ...] = Field(min_length=1, max_length=100)


class VectorProfileEventRequest(ImmutableDTO):
    event_type: VectorEventType
    entity_type: VectorEntityType
    entity_id: str = Field(min_length=1, max_length=200)
    source_entity_id: str | None = Field(default=None, min_length=1, max_length=200)
    tenant_ref: str = Field(min_length=1, max_length=200)
    target_type: str | None = Field(
        default=None, pattern=r"^(candidate_cv|standard_position|enterprise_job)$"
    )
    profile_version: str | None = Field(default=None, min_length=1, max_length=200)
    source_version: str | None = Field(default=None, min_length=1, max_length=200)
    grant_id: str | None = Field(default=None, min_length=1, max_length=200)
    grant_version: int | None = Field(default=None, ge=1)
    personal_tenant_ref: str | None = Field(default=None, min_length=1, max_length=200)
    enterprise_tenant_ref: str | None = Field(default=None, min_length=1, max_length=200)
    correlation_id: str = Field(min_length=1, max_length=200)
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=200)
    snapshot_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_event_entity(self):
        if self.event_type.startswith("cv_") != (self.entity_type == "cv"):
            raise ValueError("profile event type does not match entity type")
        if self.event_type.startswith("position_") != (self.entity_type == "position"):
            raise ValueError("profile event type does not match entity type")
        projection = (
            self.grant_id,
            self.grant_version,
            self.personal_tenant_ref,
            self.enterprise_tenant_ref,
        )
        if any(value is not None for value in projection) and any(
            value is None for value in projection
        ):
            raise ValueError("enterprise projection lineage must be complete")
        if self.enterprise_tenant_ref is not None and self.enterprise_tenant_ref != self.tenant_ref:
            raise ValueError("enterprise projection tenant mismatch")
        return self


class VectorReconcileIssue(ImmutableDTO):
    code: str
    tenant_ref: str
    point_id: str
    reference_id: str | None = None


class VectorIndexAdminService:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        planning: VectorIndexPlanningService,
        cv_source: CVProfileSource,
        position_source: PositionProfileSource,
        vectors: VectorStorePort,
        embedding_model: str,
        embedding_revision: str,
        embedding_dimension: int,
        logger: EventLogger | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._planning = planning
        self._cv_source = cv_source
        self._position_source = position_source
        self._vectors = vectors
        self._model = embedding_model
        self._revision = embedding_revision
        self._dimension = embedding_dimension
        self._validation = ProfileValidationService()
        self._logger = logger or NullEventLogger()

    def status(self) -> dict[str, object]:
        with self._unit_of_work() as uow:
            references = uow.vector_references.list_all()
            events = uow.vector_outbox.list_all()
            uow.commit()
        oldest = min(
            (item.created_at for item in events if item.status in {"pending", "retrying"}),
            default=None,
        )
        return {
            "references": dict(Counter(item.status for item in references)),
            "events": dict(Counter(item.status for item in events)),
            "oldest_pending_seconds": (
                max((datetime.now(timezone.utc) - oldest).total_seconds(), 0) if oldest else 0
            ),
        }

    def ingest_profile_event(self, request: VectorProfileEventRequest) -> dict[str, object]:
        fragments = ()
        profile_version = request.profile_version or "revoked"
        source_version = request.source_version or (
            f"grant-{request.grant_version}" if request.grant_version else "revoked"
        )
        target_type = request.target_type or (
            "candidate_cv" if request.entity_type == "cv" else "standard_position"
        )
        lineage_source_entity_id = request.source_entity_id
        if not request.event_type.endswith("_revoked"):
            profile, fragments = self._resolve(
                request.tenant_ref,
                request.entity_type,
                request.source_entity_id or request.entity_id,
                target_type=target_type,
            )
            authoritative_profile_version = profile.profile_version or profile.source_version
            if (
                request.profile_version
                and authoritative_profile_version != request.profile_version
            ):
                raise ValueError("PROFILE_EVENT_LINEAGE_STALE")
            if request.source_version and profile.source_version != request.source_version:
                raise ValueError("PROFILE_EVENT_LINEAGE_STALE")
            profile_version = authoritative_profile_version
            source_version = profile.source_version
            lineage_source_entity_id = (
                profile.position_id
                if request.entity_type == "position"
                else request.source_entity_id
            )
            if request.grant_id is not None:
                fragments = tuple(
                    fragment.model_copy(
                        update={
                            "grant_id": request.grant_id,
                            "grant_version": request.grant_version,
                            "personal_tenant_ref": request.personal_tenant_ref,
                            "enterprise_tenant_ref": request.enterprise_tenant_ref,
                        }
                    )
                    for fragment in fragments
                )
        result = self._planning.plan(
            event_type=request.event_type,
            payload=VectorOutboxPayload(
                entity_type=request.entity_type,
                entity_id=request.entity_id,
                tenant_ref=request.tenant_ref,
                profile_version=profile_version,
                source_version=source_version,
                source_entity_id=lineage_source_entity_id,
                target_type=target_type,
                grant_id=request.grant_id,
                grant_version=request.grant_version,
                personal_tenant_ref=request.personal_tenant_ref,
                enterprise_tenant_ref=request.enterprise_tenant_ref,
                requested_embedding_revision=self._revision,
                correlation_id=request.correlation_id,
            ),
            fragments=fragments,
            embedding_model=self._model,
            embedding_dimension=self._dimension,
        )
        self._logger.event(
            "vector_profile_event_ingested",
            correlation_id=request.correlation_id,
            outcome="created" if result.created else "duplicate",
            status=len(result.references),
        )
        return {
            "event_id": result.event.event_id,
            "created": result.created,
            "reference_count": len(result.references),
        }

    def reindex(self, request: VectorReindexRequest) -> dict[str, object]:
        entities = self._selected_entities(request)
        event_ids: list[str] = []
        for tenant_ref, entity_type, entity_id in entities:
            profile, fragments = self._resolve(tenant_ref, entity_type, entity_id)
            profile_version = profile.profile_version or profile.source_version
            result = self._planning.plan(
                event_type="vector_reindex_requested",
                payload=VectorOutboxPayload(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    tenant_ref=tenant_ref,
                    profile_version=profile_version,
                    source_version=profile.source_version,
                    requested_embedding_revision=self._revision,
                    correlation_id=request.correlation_id,
                ),
                fragments=fragments,
                embedding_model=self._model,
                embedding_dimension=self._dimension,
            )
            event_ids.append(result.event.event_id)
        self._logger.event(
            "vector_index_admin_reindex",
            correlation_id=request.correlation_id,
            outcome="enqueued",
            status=len(event_ids),
        )
        return {"selected": len(entities), "event_ids": event_ids}

    def retry_failed(self, event_ids: tuple[str, ...]) -> dict[str, int]:
        if not event_ids:
            raise ValueError("event_ids are required")
        with self._unit_of_work() as uow:
            now = datetime.now(timezone.utc)
            failed = tuple(
                event
                for event_id in event_ids
                if (event := uow.vector_outbox.get(event_id)) is not None
                and event.status == "dead_letter"
            )
            next_sequences = {
                event.event_id: max(
                    (
                        audit.sequence
                        for audit in uow.vector_outbox_audits.list_for_event(event.event_id)
                    ),
                    default=-1,
                )
                + 1
                for event in failed
            }
            retried = uow.vector_outbox.retry_failed(event_ids, now)
            for previous in failed:
                current = uow.vector_outbox.get(previous.event_id)
                if current is not None and current.status == "retrying":
                    uow.vector_outbox_audits.append(
                        vector_outbox_audit(
                            current,
                            from_status="dead_letter",
                            to_status="retrying",
                            occurred_at=now,
                            reason_code="MANUAL_RETRY",
                            sequence_override=next_sequences[current.event_id],
                        )
                    )
            uow.commit()
        if retried:
            self._logger.event(
                "vector_index_admin_retry_failed",
                outcome="retried",
                status=retried,
            )
        return {"requested": len(event_ids), "retried": retried}

    def reconcile(self, request: VectorReconcileRequest) -> dict[str, object]:
        if request.deactivate_revision and request.embedding_revision == self._revision:
            raise ValueError("cannot deactivate the configured active embedding revision")
        with self._unit_of_work() as uow:
            references = uow.vector_references.list_all(
                tenant_ref=request.tenant_ref,
                embedding_revision=request.embedding_revision,
            )
            uow.commit()
        points = self._vectors.list_points(
            tenant_ref=request.tenant_ref,
            embedding_revision=request.embedding_revision,
        )
        refs_by_point = {item.point_id: item for item in references}
        points_by_id = {item.point_id: item for item in points}
        issues: list[VectorReconcileIssue] = []
        for reference in references:
            point = points_by_id.get(reference.point_id)
            if point is None and reference.status == "indexed":
                issues.append(self._issue("point_missing", reference, None))
                continue
            if point is None:
                continue
            if point.profile_version != reference.profile_version:
                issues.append(self._issue("profile_version_mismatch", reference, point))
            if point.embedding_revision != reference.embedding_revision:
                issues.append(self._issue("revision_mismatch", reference, point))
            if reference.status == "indexed" and not point.active:
                issues.append(self._issue("indexed_point_inactive", reference, point))
            if (
                reference.status
                in {
                    "pending",
                    "embedding",
                    "upserting",
                    "retrying",
                    "failed",
                }
                and point.active
            ):
                issues.append(self._issue("unacknowledged_point_active", reference, point))
            if reference.status == "superseded" and point.active:
                issues.append(self._issue("superseded_point_active", reference, point))
        for point in points:
            if point.point_id not in refs_by_point:
                code = "reference_missing" if point.active else "orphan_point"
                issues.append(
                    VectorReconcileIssue(
                        code=code,
                        tenant_ref=point.tenant_ref,
                        point_id=point.point_id,
                    )
                )
        repaired = 0
        deactivated: set[tuple[str, str]] = set()
        if request.repair:
            deactivate_by_tenant: dict[str, list[str]] = {}
            reindex_entities: set[tuple[str, str, str]] = set()
            for issue in issues:
                if issue.code in {
                    "reference_missing",
                    "orphan_point",
                    "profile_version_mismatch",
                    "revision_mismatch",
                    "superseded_point_active",
                    "unacknowledged_point_active",
                }:
                    deactivate_by_tenant.setdefault(issue.tenant_ref, []).append(issue.point_id)
                if issue.reference_id is not None and issue.code in {
                    "point_missing",
                    "profile_version_mismatch",
                    "revision_mismatch",
                }:
                    reference = next(
                        item for item in references if item.reference_id == issue.reference_id
                    )
                    reindex_entities.add(
                        (
                            reference.tenant_ref,
                            reference.entity_type,
                            reference.entity_id,
                        )
                    )
            inactive_indexed_by_tenant: dict[str, list[str]] = {}
            for issue in issues:
                if issue.code != "indexed_point_inactive" or issue.reference_id is None:
                    continue
                reference = next(
                    item for item in references if item.reference_id == issue.reference_id
                )
                inactive_indexed_by_tenant.setdefault(reference.tenant_ref, []).append(
                    reference.point_id
                )
            for tenant_ref, point_ids in deactivate_by_tenant.items():
                self._vectors.deactivate(
                    tenant_ref=tenant_ref, point_ids=tuple(sorted(set(point_ids)))
                )
                deactivated.update((tenant_ref, point_id) for point_id in set(point_ids))
            for tenant_ref, point_ids in inactive_indexed_by_tenant.items():
                self._vectors.activate(
                    tenant_ref=tenant_ref, point_ids=tuple(sorted(set(point_ids)))
                )
            for tenant_ref, entity_type, entity_id in sorted(reindex_entities):
                self.reindex(
                    VectorReindexRequest(
                        tenant_ref=tenant_ref,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        correlation_id=request.correlation_id,
                    )
                )
                repaired += 1
            if request.deactivate_revision:
                active_by_tenant: dict[str, list[str]] = {}
                for point in points:
                    if point.active:
                        active_by_tenant.setdefault(point.tenant_ref, []).append(point.point_id)
                for tenant_ref, point_ids in active_by_tenant.items():
                    self._vectors.deactivate(
                        tenant_ref=tenant_ref,
                        point_ids=tuple(sorted(set(point_ids))),
                    )
                    deactivated.update((tenant_ref, point_id) for point_id in set(point_ids))
                now = datetime.now(timezone.utc)
                with self._unit_of_work() as uow:
                    for reference in uow.vector_references.list_all(
                        tenant_ref=request.tenant_ref,
                        embedding_revision=request.embedding_revision,
                    ):
                        if reference.status not in {"deleted", "superseded"}:
                            uow.vector_references.save(
                                reference.model_copy(
                                    update={
                                        "status": "superseded",
                                        "superseded_at": now,
                                        "error_code": None,
                                        "updated_at": now,
                                    }
                                )
                            )
                    uow.commit()
            repaired += len(deactivated)
            self._logger.event(
                "vector_index_admin_reconcile",
                correlation_id=request.correlation_id,
                outcome="repaired",
                status=repaired,
            )
        return {
            "issues": [item.model_dump(mode="json") for item in issues],
            "issue_counts": dict(Counter(item.code for item in issues)),
            "repaired": repaired,
            "deactivated": len(deactivated),
        }

    def _selected_entities(self, request: VectorReindexRequest):
        if request.entity_id is not None:
            return ((request.tenant_ref, request.entity_type, request.entity_id),)
        with self._unit_of_work() as uow:
            references = uow.vector_references.list_all(
                tenant_ref=request.tenant_ref,
                embedding_revision=request.embedding_revision,
            )
            uow.commit()
        return tuple(
            sorted(
                {
                    (item.tenant_ref, item.entity_type, item.entity_id)
                    for item in references
                    if item.status != "deleted"
                }
            )
        )

    def _resolve(self, tenant_ref, entity_type, entity_id, *, target_type=None):
        if entity_type == "cv":
            mapping = map_authoritative_cv_profile(
                self._cv_source.fetch_cv_profile(entity_id)
            )
            if mapping.value is None:
                raise ValueError("authoritative CV contract is invalid")
            if (
                self._validation.validate_cv(mapping.value.model_dump(mode="python")).profile_status
                != "ready"
            ):
                raise ValueError("authoritative CV profile is not ready")
            return mapping.value, fragment_cv_profile(mapping.value, tenant_ref=tenant_ref)
        raw = (
            self._position_source.fetch_enterprise_job_profile(entity_id)
            if target_type == "enterprise_job"
            else self._position_source.fetch_position_profile(entity_id)
        )
        mapping = map_authoritative_position_profile(raw)
        if mapping.value is None:
            raise ValueError("authoritative position contract is invalid")
        if (
            self._validation.validate_position(
                mapping.value.model_dump(mode="python")
            ).profile_status
            != "ready"
        ):
            raise ValueError("authoritative position profile is not ready")
        return mapping.value, fragment_position_profile(
            mapping.value,
            tenant_ref=tenant_ref,
            target_type=target_type or "standard_position",
        )

    @staticmethod
    def _issue(code, reference, point):
        return VectorReconcileIssue(
            code=code,
            tenant_ref=reference.tenant_ref,
            point_id=reference.point_id if point is None else point.point_id,
            reference_id=reference.reference_id,
        )
