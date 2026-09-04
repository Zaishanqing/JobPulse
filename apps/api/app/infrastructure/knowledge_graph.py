from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from app.integrations.knowledge_graph.client import KnowledgeGraphClient
from app.infrastructure.knowledge_graph_adapter import KnowledgeGraphAdapter
from app.contexts.knowledge_graph import (
    KnowledgeGraphIntegrationConflict,
    KnowledgeGraphIntegrationDisabled,
    KnowledgeGraphIntegrationNotFound,
    KnowledgeGraphIntegrationPort,
    KnowledgeGraphIntegrationRuleViolation,
    KnowledgeGraphSyncResult,
)
from app.domain.accounts import AccountActor
from app.domain.json_types import thaw_json_object
from app.infrastructure.outbox import OutboxLeaseHeartbeat, SqlAlchemyOutboxRepository
from app.integration_events import (
    DispatchResult,
    IdempotencyKey,
    IntegrationEvent,
    OutboxStatus,
)
from app.integrations.knowledge_graph.exceptions import KnowledgeGraphError
from app.models.jd_parse_result import JDParseResult
from app.models.jd_publication import JDPublication
from app.models.outbox_message import OutboxMessage
from app.models.source_jd import SourceJDVersion


@dataclass(frozen=True)
class PublishedJDFactIdentity:
    source_fact_id: str
    source_fact_version: str
    idempotency_key: str


def published_jd_fact_identity(parsed: JDParseResult) -> PublishedJDFactIdentity:
    source_version = parsed.updated_at or parsed.created_at
    if source_version is None:
        raise ValueError("published_jd_sync_event_stale: source fact has no version")
    if source_version.tzinfo is None:
        source_version = source_version.replace(tzinfo=timezone.utc)
    source_fact_version = source_version.isoformat()
    return PublishedJDFactIdentity(
        parsed.id,
        source_fact_version,
        f"{parsed.id}:{source_fact_version}",
    )


class KnowledgeGraphPublishedJDSyncHandler:
    """Deliver a published-JD integration event through the KG adapter."""

    event_type = "knowledge_graph.published_jd.sync"

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        client: KnowledgeGraphClient,
        *,
        enabled: bool,
    ) -> None:
        self._session_factory = session_factory
        self._client = client
        self._enabled = enabled

    def handle(self, event: IntegrationEvent, idempotency_key: IdempotencyKey) -> DispatchResult:
        payload = thaw_json_object(event.payload)
        document_id = payload.get("document_id")
        actor_id = payload.get("actor_id")
        actor_role = payload.get("actor_role")
        if not all(
            isinstance(value, str) and value for value in (document_id, actor_id, actor_role)
        ):
            return DispatchResult(
                False, False, "Published JD sync event has invalid actor or document payload"
            )

        actor = AccountActor(actor_id, actor_role)
        with self._session_factory() as session:
            current = session.query(JDParseResult).filter_by(jd_id=document_id).one_or_none()
            if current is None:
                return DispatchResult(False, False, "published_jd_sync_event_stale")
            try:
                current_identity = published_jd_fact_identity(current)
            except ValueError as exc:
                return DispatchResult(False, False, str(exc))
            event_identity = PublishedJDFactIdentity(
                str(payload.get("source_fact_id", "")),
                str(payload.get("source_fact_version", "")),
                idempotency_key.value,
            )
            if current_identity != event_identity:
                return DispatchResult(False, False, "published_jd_sync_event_stale")
            adapter = KnowledgeGraphAdapter(session, self._client, enabled=self._enabled)
            try:
                adapter.sync_jd(document_id, actor)
                session.commit()
                return DispatchResult(True)
            except KnowledgeGraphError as exc:
                # The adapter records an integration failure on its mapping;
                # retain that diagnostic before the dispatcher schedules retry.
                session.commit()
                return _kg_dispatch_failure(exc)
            except ValueError:
                session.rollback()
                return DispatchResult(False, False, "jd_publication_contract_invalid")


def _kg_dispatch_failure(exc: KnowledgeGraphError) -> DispatchResult:
    authentication_codes = {
        "invalid_credentials",
        "invalid_token",
        "knowledge_graph_401",
        "knowledge_graph_auth_invalid",
        "token_expired",
    }
    forbidden_codes = {
        "forbidden",
        "insufficient_permissions",
        "knowledge_graph_403",
        "knowledge_graph_forbidden",
        "permission_denied",
    }
    if exc.status_code == 401 or exc.error_code in authentication_codes:
        return DispatchResult(False, True, "knowledge_graph_401")
    if exc.status_code == 403 or exc.error_code in forbidden_codes:
        return DispatchResult(False, False, "knowledge_graph_forbidden")
    permanent_codes = {
        "knowledge_graph_contract_mismatch",
        "knowledge_graph_400",
        "knowledge_graph_404",
        "knowledge_graph_409",
        "knowledge_graph_422",
    }
    retryable = exc.error_code not in permanent_codes and (
        exc.status_code >= 500 or exc.status_code in {408, 429}
    )
    return DispatchResult(False, retryable, exc.error_code)


class JDPublicationKnowledgeGraphHandler:
    """Deliver one immutable JD publication through the shared KG mapper."""

    event_type = "jd.publication.created"

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        client: KnowledgeGraphClient,
        *,
        enabled: bool,
    ) -> None:
        self._session_factory = session_factory
        self._client = client
        self._enabled = enabled

    @staticmethod
    def _invalid(code: str) -> DispatchResult:
        return DispatchResult(False, False, code)

    def handle(self, event: IntegrationEvent, idempotency_key: IdempotencyKey) -> DispatchResult:
        payload = thaw_json_object(event.payload)
        publication_id = payload.get("publication_id")
        if not isinstance(publication_id, str) or not publication_id:
            return self._invalid("jd_publication_event_invalid")
        with self._session_factory() as session:
            publication = session.get(JDPublication, publication_id)
            if publication is None:
                return self._invalid("jd_publication_not_found")
            snapshot = dict(publication.snapshot_payload)
            parsed = session.get(JDParseResult, publication.parse_result_id)
            if parsed is None:
                return self._invalid("jd_publication_parse_result_not_found")
            expected_event_values = {
                "event_id": event.event_id,
                "event_type": self.event_type,
                "aggregate_id": publication.jd_id,
                "parse_result_id": publication.parse_result_id,
                "publication_id": publication.id,
                "published_fact_id": publication.id,
                "source_jd_id": publication.source_jd_id,
                "source_jd_version_id": publication.source_jd_version_id,
                "extraction_task_id": publication.extraction_task_id,
                "document_id": publication.document_id,
                "schema_version": publication.schema_version,
                "normalization_schema_version": (publication.normalization_schema_version),
            }
            if event.event_type != self.event_type or event.aggregate_id != publication.jd_id:
                return self._invalid("jd_publication_event_identity_mismatch")
            if any(payload.get(key) != value for key, value in expected_event_values.items()):
                return self._invalid("jd_publication_event_identity_mismatch")
            source_version = str(
                publication.snapshot_payload.get("source_version") or ""
            )
            stable_key = f"jd-publication:{publication.parse_result_id}:{source_version}"
            if stable_key != publication.idempotency_key or idempotency_key.value != stable_key:
                return self._invalid("jd_publication_identity_mismatch")
            if (
                snapshot.get("publication_id") != publication.id
                or snapshot.get("parse_result_id") != parsed.id
                or snapshot.get("jd_id") != parsed.jd_id
                or parsed.workflow_status != "published"
                or parsed.schema_version != publication.schema_version
                or parsed.normalization_schema_version != publication.normalization_schema_version
            ):
                return self._invalid("jd_publication_snapshot_identity_mismatch")
            if publication.source_jd_version_id is not None:
                version = session.get(SourceJDVersion, publication.source_jd_version_id)
                if version is None or snapshot.get("source_jd_version_id") != version.id:
                    return self._invalid("jd_publication_source_version_mismatch")
            published_by = snapshot.get("published_by")
            published_by_role = snapshot.get("published_by_role")
            if not isinstance(published_by, str) or not isinstance(published_by_role, str):
                return self._invalid("jd_publication_actor_invalid")
            adapter = KnowledgeGraphAdapter(session, self._client, enabled=self._enabled)
            try:
                adapter.sync_jd(
                    publication.jd_id,
                    AccountActor(published_by, published_by_role),
                    publication_snapshot=snapshot,
                )
                session.commit()
                return DispatchResult(True)
            except KnowledgeGraphError as exc:
                session.commit()
                return _kg_dispatch_failure(exc)
            except KnowledgeGraphIntegrationConflict:
                session.rollback()
                return DispatchResult(
                    False,
                    False,
                    "knowledge_graph_integration_conflict",
                )
            except KnowledgeGraphIntegrationDisabled:
                session.rollback()
                return self._invalid("knowledge_graph_integration_disabled")
            except KnowledgeGraphIntegrationNotFound:
                session.rollback()
                return self._invalid("knowledge_graph_integration_not_found")
            except (KnowledgeGraphIntegrationRuleViolation, TypeError, ValueError):
                session.rollback()
                return self._invalid("jd_publication_contract_invalid")


def build_knowledge_graph_outbox_handlers(
    session_factory: sessionmaker[Session],
    client: KnowledgeGraphClient,
    *,
    enabled: bool,
) -> dict[
    str,
    KnowledgeGraphPublishedJDSyncHandler | JDPublicationKnowledgeGraphHandler,
]:
    """Return the complete handler registry used by the outbox CLI worker."""

    legacy = KnowledgeGraphPublishedJDSyncHandler(session_factory, client, enabled=enabled)
    publication = JDPublicationKnowledgeGraphHandler(session_factory, client, enabled=enabled)
    return {
        legacy.event_type: legacy,
        publication.event_type: publication,
    }


class KnowledgeGraphAdapterFactory:
    """Creates one SQLAlchemy-backed adapter scope for each application call."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        client: KnowledgeGraphClient,
        *,
        enabled: bool,
    ) -> None:
        self._session_factory = session_factory
        self._client = client
        self._enabled = enabled

    @contextmanager
    def __call__(self) -> Iterator[KnowledgeGraphIntegrationPort]:
        session = self._session_factory()
        try:
            yield KnowledgeGraphAdapter(session, self._client, enabled=self._enabled)
            session.commit()
        except KnowledgeGraphError:
            session.commit()
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def sync_jd(self, document_id: str, actor: AccountActor) -> KnowledgeGraphSyncResult:
        with self._session_factory() as session:
            publication = (
                session.query(JDPublication)
                .filter(JDPublication.jd_id == document_id)
                .one_or_none()
            )
            parsed = session.query(JDParseResult).filter_by(jd_id=document_id).one_or_none()
            if parsed is None:
                with self() as adapter:
                    return adapter.sync_jd(document_id, actor)
            if publication is None:
                raise KnowledgeGraphError(
                    "position-taxonomy.v3 JD publication is required",
                    status_code=409,
                    error_code="position_v3_publication_required",
                )
            publication_id = publication.id
        return self._sync_publication_event(publication_id, actor)

    def _sync_publication_event(
        self, publication_id: str, actor: AccountActor
    ) -> KnowledgeGraphSyncResult:
        with self._session_factory() as session:
            publication = session.get(JDPublication, publication_id)
            if publication is None:
                raise KnowledgeGraphError(
                    "JD publication not found",
                    status_code=404,
                    error_code="jd_publication_not_found",
                )
            message = (
                session.query(OutboxMessage)
                .filter(
                    OutboxMessage.idempotency_key == publication.idempotency_key,
                    OutboxMessage.event_type == JDPublicationKnowledgeGraphHandler.event_type,
                )
                .one()
            )
            document_id = publication.jd_id
            parse_result_id = publication.parse_result_id
            if message.status == OutboxStatus.DELIVERED.value:
                adapter = KnowledgeGraphAdapter(session, self._client, enabled=self._enabled)
                try:
                    result = adapter.sync_jd(
                        document_id,
                        actor,
                        publication_snapshot=dict(publication.snapshot_payload),
                    )
                    session.commit()
                    return result
                except KnowledgeGraphError:
                    session.commit()
                    raise
            worker_id = f"sync-api:{message.event_id}"
            claimed = SqlAlchemyOutboxRepository(session).claim_by_id(
                message.id, worker_id, datetime.now(timezone.utc)
            )
            session.commit()
        if claimed is None:
            raise KnowledgeGraphError(
                "Knowledge graph sync is already being delivered",
                status_code=409,
                error_code="knowledge_graph_sync_in_progress",
            )
        heartbeat = OutboxLeaseHeartbeat(
            self._session_factory,
            message_id=claimed.message_id,
            worker_id=worker_id,
            lease_seconds=60,
        )
        heartbeat.start()
        try:
            delivery = JDPublicationKnowledgeGraphHandler(
                self._session_factory, self._client, enabled=self._enabled
            ).handle(claimed.draft.event, claimed.draft.idempotency_key)
        finally:
            heartbeat.stop()
        with self._session_factory() as session:
            completed = SqlAlchemyOutboxRepository(session).complete(
                claimed.message_id, worker_id, delivery
            )
            session.commit()
            if not completed:
                raise KnowledgeGraphError(
                    "Knowledge graph sync lost its outbox lease",
                    status_code=409,
                    error_code="knowledge_graph_outbox_lost_lease",
                )
            if not delivery.delivered:
                raise KnowledgeGraphError(
                    "Knowledge graph publication delivery failed",
                    status_code=503 if delivery.retryable else 409,
                    error_code=delivery.error or "knowledge_graph_outbox_permanent",
                )
            mapping = KnowledgeGraphAdapter(
                session, self._client, enabled=self._enabled
            ).mapping_status(document_id)
            return KnowledgeGraphSyncResult(
                document_id,
                mapping.knowledge_graph_id,
                str(parse_result_id),
                mapping.sync_status,
                False,
                mapping.last_trace_id,
            )
