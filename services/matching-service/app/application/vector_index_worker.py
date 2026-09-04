"""C3 transactional-outbox consumer for the derived vector index."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager
from threading import Event, Thread

from pydantic import Field

from app.application.contract_mapping import (
    map_authoritative_cv_profile,
    map_authoritative_position_profile,
)
from app.application.validation import ProfileValidationService
from app.application.vector_indexing import VectorOutboxLifecycleService
from app.domain.profiles import ImmutableDTO
from app.domain.semantic_fragments import fragment_cv_profile, fragment_position_profile
from app.domain.vector_contracts import (
    EmbeddingRequest,
    VectorContractViolation,
    VectorRecord,
)
from app.domain.vector_indexing import VectorOutboxEvent
from app.ports.observability import (
    EventLogger,
    MetricsCollector,
    NullEventLogger,
    NullMetricsCollector,
)
from app.ports.profile_sources import CVProfileSource, PositionProfileSource
from app.ports.repositories import UnitOfWorkFactory
from app.ports.upstream_contracts import UpstreamResponseError, UpstreamTimeoutError
from app.ports.vectors import EmbeddingPort, VectorStorePort


class VectorWorkerResult(ImmutableDTO):
    outcome: str = Field(pattern=r"^(idle|processed|retrying|dead_letter|lost_claim|stale)$")
    event_id: str | None = None
    error_code: str | None = None


class VectorIndexWorker:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        lifecycle: VectorOutboxLifecycleService,
        cv_source: CVProfileSource,
        position_source: PositionProfileSource,
        embedding: EmbeddingPort,
        vectors: VectorStorePort,
        embedding_model: str,
        embedding_revision: str,
        embedding_dimension: int,
        index_revision: str | None = None,
        batch_size: int = 32,
        heartbeat_interval_seconds: float = 5,
        metrics: MetricsCollector | None = None,
        logger: EventLogger | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not embedding_model or not embedding_revision or embedding_dimension <= 0:
            raise ValueError("vector worker embedding lineage is invalid")
        if batch_size <= 0:
            raise ValueError("vector worker batch size must be positive")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("vector worker heartbeat interval must be positive")
        self._unit_of_work = unit_of_work
        self._lifecycle = lifecycle
        self._cv_source = cv_source
        self._position_source = position_source
        self._embedding = embedding
        self._vectors = vectors
        self._model = embedding_model
        self._revision = embedding_revision
        self._dimension = embedding_dimension
        self._index_revision = index_revision
        self._batch_size = batch_size
        self._heartbeat_interval = heartbeat_interval_seconds
        self._metrics = metrics or NullMetricsCollector()
        self._logger = logger or NullEventLogger()
        self._monotonic = monotonic
        self._validation = ProfileValidationService()

    def run_once(self, worker_id: str, event_id: str | None = None) -> VectorWorkerResult:
        started = self._monotonic()
        claim = self._lifecycle.claim(worker_id, event_id)
        event = claim.event
        if event is None:
            return VectorWorkerResult(outcome="idle")
        try:
            with self._heartbeat(event.event_id, worker_id) as claim_lost:
                outcome = self._process(event, worker_id)
                if claim_lost.is_set() and outcome not in {"processed", "stale"}:
                    outcome = "lost_claim"
            self._record(event, outcome, None, started, worker_id)
            return VectorWorkerResult(outcome=outcome, event_id=event.event_id)
        except (VectorContractViolation, UpstreamTimeoutError, UpstreamResponseError) as exc:
            code = getattr(exc, "code", None) or (
                "UPSTREAM_TIMEOUT"
                if isinstance(exc, UpstreamTimeoutError)
                else "UPSTREAM_HTTP_ERROR"
            )
            return self._fail(event, worker_id, code, started)
        except (KeyError, ValueError) as exc:
            code = (
                "UPSTREAM_CONTRACT_NOT_FOUND"
                if isinstance(exc, KeyError)
                else "VECTOR_CONTRACT_INVALID"
            )
            return self._fail(event, worker_id, code, started)
        except Exception as exc:  # crash-safe boundary: leave staged points inactive
            return self._fail(
                event,
                worker_id,
                f"VECTOR_WORKER_{type(exc).__name__.upper()}",
                started,
            )

    def run_batch(self, worker_id: str, *, limit: int = 20) -> tuple[VectorWorkerResult, ...]:
        if limit <= 0:
            raise ValueError("vector worker batch limit must be positive")
        results: list[VectorWorkerResult] = []
        for _ in range(limit):
            result = self.run_once(worker_id)
            if result.outcome == "idle":
                break
            results.append(result)
        return tuple(results)

    def _process(self, event: VectorOutboxEvent, worker_id: str) -> str:
        payload = event.payload
        if event.event_type.endswith("_revoked"):
            references = self._event_references(event)
            self._vectors.delete(
                tenant_ref=payload.tenant_ref,
                point_ids=tuple(item.point_id for item in references),
            )
            acknowledged = self._lifecycle.acknowledge(
                event.event_id,
                worker_id,
                tuple(item.reference_id for item in references),
            )
            return acknowledged.outcome

        profile, fragments = self._authoritative_fragments(event)
        current_profile_version = profile.profile_version or profile.source_version
        if (
            current_profile_version != payload.profile_version
            or profile.source_version != payload.source_version
        ):
            result = self._lifecycle.discard_stale(event.event_id, worker_id)
            return "stale" if result.outcome == "processed" else result.outcome
        if payload.requested_embedding_revision != self._revision:
            raise VectorContractViolation(
                "EMBEDDING_REVISION_UNAVAILABLE",
                "worker does not serve the requested embedding revision",
            )
        expected = {item.fragment_id: item for item in self._event_references(event)}
        if set(expected) != {item.fragment_id for item in fragments}:
            raise VectorContractViolation(
                "VECTOR_FRAGMENT_PLAN_MISMATCH",
                "authoritative fragments differ from persisted index references",
            )
        if not self._lifecycle.heartbeat(event.event_id, worker_id):
            return "lost_claim"
        for offset in range(0, len(fragments), self._batch_size):
            batch = fragments[offset : offset + self._batch_size]
            if (
                self._lifecycle.mark_references(event.event_id, worker_id, "embedding").outcome
                == "lost_claim"
            ):
                return "lost_claim"
            embedded = self._embedding.embed(
                EmbeddingRequest(
                    tenant_ref=payload.tenant_ref,
                    embedding_model=self._model,
                    embedding_revision=self._revision,
                    dimension=self._dimension,
                    fragments=batch,
                )
            )
            if embedded.fragment_ids != tuple(item.fragment_id for item in batch):
                raise VectorContractViolation(
                    "EMBEDDING_RESPONSE_INVALID",
                    "embedding result fragment order differs from request",
                )
            records = tuple(
                VectorRecord.build(
                    fragment=fragment,
                    embedding=vector,
                    embedding_model=self._model,
                    embedding_revision=self._revision,
                    payload={
                        key: value
                        for key, value in {
                            "grant_id": payload.grant_id,
                            "grant_version": payload.grant_version,
                            "personal_tenant_ref": payload.personal_tenant_ref,
                            "enterprise_tenant_ref": payload.enterprise_tenant_ref,
                            "target_type": payload.target_type,
                            "index_revision": self._index_revision,
                        }.items()
                        if value is not None
                    },
                    active=False,
                    index_revision=self._index_revision,
                    collection=getattr(self._vectors, "collection_name", None),
                    text_derivation_version="semantic-fragment.v1",
                )
                for fragment, vector in zip(batch, embedded.vectors, strict=True)
            )
            if any(
                record.point_id != expected[record.fragment.fragment_id].point_id
                for record in records
            ):
                raise VectorContractViolation(
                    "VECTOR_POINT_LINEAGE_MISMATCH",
                    "generated point identity differs from persisted reference",
                )
            if (
                self._lifecycle.mark_references(event.event_id, worker_id, "upserting").outcome
                == "lost_claim"
            ):
                return "lost_claim"
            indexed = self._vectors.upsert(records)
            expected_results = {
                (record.point_id, record.fragment.fragment_id): record for record in records
            }
            actual_results = {(item.point_id, item.fragment_id): item for item in indexed}
            if set(actual_results) != set(expected_results) or any(
                item.tenant_ref != expected_results[key].tenant_ref
                or item.active
                for key, item in actual_results.items()
            ):
                raise VectorContractViolation(
                    "VECTOR_UPSERT_PARTIAL",
                    "vector store did not confirm every requested point",
                )
            if not self._lifecycle.heartbeat(event.event_id, worker_id):
                return "lost_claim"
        superseded = self._previous_references(event)
        acknowledged = self._lifecycle.acknowledge(
            event.event_id,
            worker_id,
            tuple(item.reference_id for item in expected.values()),
        )
        if acknowledged.outcome != "processed":
            return acknowledged.outcome
        point_ids = tuple(item.point_id for item in expected.values())
        self._vectors.activate(tenant_ref=payload.tenant_ref, point_ids=point_ids)
        self._vectors.deactivate(
            tenant_ref=payload.tenant_ref,
            point_ids=tuple(item.point_id for item in superseded),
        )
        return acknowledged.outcome

    def _authoritative_fragments(self, event: VectorOutboxEvent):
        payload = event.payload
        source_entity_id = payload.source_entity_id or payload.entity_id
        if payload.entity_type == "cv":
            mapping = map_authoritative_cv_profile(
                self._cv_source.fetch_cv_profile(source_entity_id)
            )
            if mapping.value is None:
                raise ValueError("authoritative CV contract is invalid")
            profile = mapping.value
            self._require_ready(profile, "cv")
            fragments = fragment_cv_profile(profile, tenant_ref=payload.tenant_ref)
            if payload.grant_id is not None:
                fragments = tuple(
                    fragment.model_copy(
                        update={
                            "grant_id": payload.grant_id,
                            "grant_version": payload.grant_version,
                            "personal_tenant_ref": payload.personal_tenant_ref,
                            "enterprise_tenant_ref": payload.enterprise_tenant_ref,
                        }
                    )
                    for fragment in fragments
                )
            return profile, fragments
        mapping = map_authoritative_position_profile(
            self._position_source.fetch_enterprise_job_profile(payload.entity_id)
            if payload.target_type == "enterprise_job"
            else self._position_source.fetch_position_profile(source_entity_id)
        )
        if mapping.value is None:
            raise ValueError("authoritative position contract is invalid")
        profile = mapping.value
        self._require_ready(profile, "position")
        return profile, fragment_position_profile(
            profile,
            tenant_ref=payload.tenant_ref,
            target_type=payload.target_type or "standard_position",
        )

    def _require_ready(self, profile, entity_type: str) -> None:
        result = (
            self._validation.validate_cv(profile.model_dump(mode="python"))
            if entity_type == "cv"
            else self._validation.validate_position(profile.model_dump(mode="python"))
        )
        if result.profile_status != "ready":
            raise VectorContractViolation(
                "PROFILE_NOT_READY",
                "authoritative profile is not approved for vector indexing",
            )

    def _event_references(self, event: VectorOutboxEvent):
        payload = event.payload
        with self._unit_of_work() as uow:
            references = uow.vector_references.list_for_entity(
                payload.tenant_ref, payload.entity_type, payload.entity_id
            )
            uow.commit()
        if event.event_type.endswith("_revoked"):
            return tuple(item for item in references if item.status == "deleted")
        return tuple(
            item
            for item in references
            if item.profile_version == payload.profile_version
            and item.embedding_revision == payload.requested_embedding_revision
            and item.status not in {"deleted", "superseded"}
        )

    def _previous_references(self, event: VectorOutboxEvent):
        payload = event.payload
        with self._unit_of_work() as uow:
            references = uow.vector_references.list_for_entity(
                payload.tenant_ref, payload.entity_type, payload.entity_id
            )
            uow.commit()
        return tuple(
            item
            for item in references
            if item.created_at < event.created_at
            and item.status not in {"deleted", "superseded"}
            and (
                item.profile_version != payload.profile_version
                or item.embedding_revision != payload.requested_embedding_revision
            )
        )

    @contextmanager
    def _heartbeat(self, event_id: str, worker_id: str):
        stop = Event()
        claim_lost = Event()

        def renew() -> None:
            try:
                while not stop.wait(self._heartbeat_interval):
                    if self._lifecycle.heartbeat(event_id, worker_id):
                        continue
                    claim_lost.set()
                    return
            except Exception as exc:
                claim_lost.set()
                self._logger.event(
                    "vector_index_heartbeat_failed",
                    worker_id=worker_id,
                    outcome="lost_claim",
                    error_code=type(exc).__name__,
                )

        thread = Thread(target=renew, name="vector-outbox-heartbeat", daemon=True)
        thread.start()
        try:
            yield claim_lost
        finally:
            stop.set()
            thread.join(timeout=self._heartbeat_interval)

    def _fail(self, event, worker_id, code, started):
        failed = self._lifecycle.fail(event.event_id, worker_id, code)
        self._record(event, failed.outcome, code, started, worker_id)
        return VectorWorkerResult(outcome=failed.outcome, event_id=event.event_id, error_code=code)

    def _record(self, event, outcome, error_code, started, worker_id):
        self._metrics.increment("matching_vector_events_total", outcome=outcome)
        self._metrics.observe("matching_vector_event_duration_seconds", self._monotonic() - started)
        self._logger.event(
            "vector_index_event",
            correlation_id=event.payload.correlation_id,
            trace_id=event.payload.correlation_id,
            worker_id=worker_id,
            operation="vector-index",
            model_id=self._model,
            model_revision=self._revision,
            dimension=self._dimension,
            outcome=outcome,
            error_code=error_code,
        )
