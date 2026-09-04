from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.contexts.governance_feedback import ManageReviews
from app.contexts.jd_lifecycle import Actor, JDUseCases
from app.contexts.platform import (
    ManageOutboxEvents,
    OutboxRequeueConflict,
)
from app.domain.accounts import AccountActor
from app.infrastructure.governance import SqlAlchemyGovernanceUnitOfWork
from app.infrastructure.jd_export import OpenPyxlJDExporter
from app.infrastructure.jd_repository import SqlAlchemyJDUoW
from app.infrastructure.jd_schema import VersionedJDSchemaAdapter
from app.infrastructure.knowledge_graph import (
    JDPublicationKnowledgeGraphHandler,
    KnowledgeGraphAdapterFactory,
    build_knowledge_graph_outbox_handlers,
)
from app.infrastructure.knowledge_graph_adapter import KnowledgeGraphAdapter
from app.infrastructure.outbox import (
    SqlAlchemyOutboxDispatcher,
    SqlAlchemyOutboxOperationsUnitOfWork,
    SqlAlchemyOutboxRepository,
)
from app.main import app
from app.integrations.knowledge_graph.exceptions import (
    KnowledgeGraphError,
    KnowledgeGraphUnavailable,
)
from app.integrations.knowledge_graph.published_fact import (
    CONTRACT_VERSION_V3,
)
from app.models.knowledge_graph_mapping import KnowledgeGraphEntityMapping
from app.models.jd_publication import JDPublication
from app.models.outbox_message import OutboxMessage
from app.models.review_task import ReviewTask
from app.models.skill import Skill
from app.models.skill_taxonomy import SkillClassification, SkillTaxonomyNode
from app.models.standard_position import StandardPosition
from jobgraph_contracts.normalization_v2 import JobClassification
from tests.runtime_database import reset_database_data, Base, SessionLocal, engine
from tests.test_extraction_draft_import import (
    FakeProvider,
    _envelope,
    _source_and_task,
)
from tests.user_factory import create_internal_user


NOW = datetime.now(timezone.utc) + timedelta(days=1)
ADMIN = Actor("outbox-admin", "admin")
REVIEWER = AccountActor("outbox-reviewer", "reviewer")
api_client = TestClient(app)


class _ResolvedPositionProvider(FakeProvider):
    def extract(self, envelope):
        bundle = super().extract(envelope)
        normalized = bundle.normalized_result.model_copy(
            update={
                "job_classification": JobClassification(
                    source_title=envelope.job_title_raw,
                    position_code="BACKEND_ENGINEER",
                    position_name="Backend Engineer",
                    family_code="SOFTWARE_ENGINEERING",
                    family_name="软件工程与研发",
                    candidate_positions=[
                        {"position_code": "BACKEND_ENGINEER", "score": 0.93}
                    ],
                    confidence=0.93,
                    classification_status="resolved",
                    evidence_refs=["task-1", "skill-1"],
                )
            }
        )
        return bundle.model_copy(update={"normalized_result": normalized})


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


class FakeKGClient:
    def __init__(self) -> None:
        self.import_calls: list[dict] = []
        self.remote_facts: dict[tuple[str, str, str], dict] = {}
        self.skills: list[dict] = []
        self.failure: Exception | None = None
        self.contract_version = CONTRACT_VERSION_V3

    def list_positions(self):
        return []

    def list_skills(self):
        return self.skills

    def upsert_skill_snapshot(self, skill_id, payload, **actor):
        return SimpleNamespace(data={"skill_id": skill_id}, trace_id="trace-skill")

    def import_published_fact_v3(self, payload, **actor):
        assert payload["contract_version"] == CONTRACT_VERSION_V3
        return self._import_published_fact(payload, actor)

    def _import_published_fact(self, payload, actor):
        self.import_calls.append({"payload": payload, "actor": actor})
        if self.failure is not None:
            raise self.failure
        identity = (
            payload["source_system"],
            payload["source_fact_id"],
            payload["source_fact_version"],
        )
        self.remote_facts.setdefault(identity, payload)
        return SimpleNamespace(
            data={
                "contract_version": self.contract_version,
                "document_id": payload["source_jd_id"],
            },
            trace_id="trace-import",
        )


def _jd_use_cases() -> JDUseCases:
    return JDUseCases(
        lambda: SqlAlchemyJDUoW(SessionLocal),
        OpenPyxlJDExporter(),
        VersionedJDSchemaAdapter(),
    )


def _seed_python_taxonomy() -> None:
    with SessionLocal() as session:
        if session.get(Skill, "skill-python") is None:
            skill = Skill(
                id="skill-python",
                catalog_code="LANG_PYTHON",
                skill_name="Python",
                category="programming_language",
            )
            concept = SkillTaxonomyNode(
                facet="concept_class",
                code="technology",
                name_zh="技术实体",
                name_en="Technology",
            )
            kind = SkillTaxonomyNode(
                facet="technology_kind",
                code="language",
                name_zh="编程与查询语言",
                name_en="Programming and query language",
            )
            session.add_all([skill, concept, kind])
            session.flush()
            session.add_all(
                [
                    SkillClassification(
                        skill_id=skill.id,
                        taxonomy_node_id=concept.id,
                        facet=concept.facet,
                        is_primary=True,
                    ),
                    SkillClassification(
                        skill_id=skill.id,
                        taxonomy_node_id=kind.id,
                        facet=kind.facet,
                        is_primary=True,
                    ),
                ]
            )
        if session.query(StandardPosition).filter_by(
            position_code="BACKEND_ENGINEER"
        ).one_or_none() is None:
            session.add(
                StandardPosition(
                    id="position-backend-v3",
                    position_code="BACKEND_ENGINEER",
                    position_name="Backend Engineer",
                    taxonomy_family_code="SOFTWARE_ENGINEERING",
                    taxonomy_family_name="软件工程与研发",
                    taxonomy_version="position-taxonomy.v3.0.0",
                    lifecycle_status="active",
                    sample_support_status="sufficient",
                )
            )
        session.commit()


def _publish(envelope=None):
    _seed_python_taxonomy()
    extraction, _, task = _source_and_task(
        envelope=envelope,
        provider=_ResolvedPositionProvider(),
        seed_catalog=False,
    )
    draft = extraction.import_extraction_bundle(
        task.id,
        position_bindings={
            "BACKEND_ENGINEER": ("position-backend-v3", "Backend Engineer")
        },
    )
    with SessionLocal() as session:
        review = (
            session.query(ReviewTask)
            .filter(
                ReviewTask.object_type == "jd_parse_result",
                ReviewTask.object_id == draft.parse_result_id,
            )
            .one()
        )
        review_id = review.id
    ManageReviews(
        lambda: SqlAlchemyGovernanceUnitOfWork(SessionLocal)
    ).transition(REVIEWER, review_id, "claim")
    ManageReviews(
        lambda: SqlAlchemyGovernanceUnitOfWork(SessionLocal)
    ).transition(REVIEWER, review_id, "approve", "verified")
    publication = _jd_use_cases().publish_parse_result_by_id(
        ADMIN, draft.parse_result_id
    )
    return draft, publication


def _dispatcher(
    client,
    *,
    lease_seconds: int = 60,
    max_attempts: int = 5,
):
    return SqlAlchemyOutboxDispatcher(
        SessionLocal,
        build_knowledge_graph_outbox_handlers(
            SessionLocal, client, enabled=True
        ),
        event_types={JDPublicationKnowledgeGraphHandler.event_type},
        lease_seconds=lease_seconds,
        max_attempts=max_attempts,
    )


def _message() -> OutboxMessage:
    with SessionLocal() as session:
        return (
            session.query(OutboxMessage)
            .filter(
                OutboxMessage.event_type
                == JDPublicationKnowledgeGraphHandler.event_type
            )
            .one()
        )


def _make_due() -> None:
    with SessionLocal() as session:
        session.execute(
            update(OutboxMessage).values(next_attempt_at=NOW - timedelta(seconds=1))
        )
        session.commit()


def _outbox_operations() -> ManageOutboxEvents:
    return ManageOutboxEvents(
        lambda: SqlAlchemyOutboxOperationsUnitOfWork(SessionLocal)
    )


def _token(username: str, role: str) -> str:
    create_internal_user(username, role)
    response = api_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    assert response.status_code == 200
    return response.json()["data"]["access_token"]


def test_pending_publication_is_delivered_once_from_immutable_snapshot():
    _, publication = _publish()
    client = FakeKGClient()
    dispatcher = _dispatcher(client)

    assert dispatcher.dispatch_one("worker-1", NOW).delivered is True
    assert dispatcher.dispatch_one("worker-1", NOW) is None

    message = _message()
    assert message.status == "delivered"
    assert len(client.import_calls) == 1
    payload = client.import_calls[0]["payload"]
    assert payload["contract_version"] == CONTRACT_VERSION_V3
    assert payload["validation_lineage"]["state"] == "absent"
    assert payload["source_fact_id"] == publication.parse_result_id
    assert payload["evidence"][0]["alignment"] == "exact"
    assert len(client.remote_facts) == 1


def test_immutable_publication_snapshot_maps_to_v3_contract():
    draft, publication = _publish()
    client = FakeKGClient()
    with SessionLocal() as session:
        stored = session.get(JDPublication, publication.id)
        assert stored is not None
        result = KnowledgeGraphAdapter(session, client, enabled=True).sync_jd(
            draft.jd_id,
            AccountActor("mapping-regression", "admin"),
            publication_snapshot=dict(stored.snapshot_payload),
        )

    assert result.sync_status == "synced"
    assert client.import_calls[0]["payload"]["contract_version"] == CONTRACT_VERSION_V3


def test_worker_ignores_unrelated_outbox_event_types():
    _publish()
    with SessionLocal() as session:
        from app.domain.json_types import freeze_json_object
        from app.integration_events import (
            IdempotencyKey,
            IntegrationEvent,
            OutboxMessageDraft,
        )

        SqlAlchemyOutboxRepository(session).add(
            OutboxMessageDraft(
                IntegrationEvent(
                    "other-event",
                    "other.integration.event",
                    "other",
                    freeze_json_object({"safe": True}),
                    NOW - timedelta(seconds=1),
                ),
                IdempotencyKey("other:event:key"),
            )
        )
        session.commit()
    client = FakeKGClient()

    assert _dispatcher(client).dispatch_one("worker", NOW).delivered is True

    with SessionLocal() as session:
        unrelated = (
            session.query(OutboxMessage)
            .filter(OutboxMessage.event_type == "other.integration.event")
            .one()
        )
        assert unrelated.status == "pending"


def test_two_workers_claim_publication_only_once():
    _publish()
    barrier = Barrier(2)

    def claim(worker_id: str):
        with SessionLocal() as session:
            barrier.wait()
            result = SqlAlchemyOutboxRepository(session).claim(
                worker_id,
                NOW,
                event_types={JDPublicationKnowledgeGraphHandler.event_type},
            )
            session.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("worker-a", "worker-b")))

    assert sum(result is not None for result in results) == 1


def test_timeout_is_retryable_and_contract_failure_is_dead_lettered():
    _publish()
    client = FakeKGClient()
    client.failure = KnowledgeGraphUnavailable(
        "private upstream response",
        error_code="knowledge_graph_unavailable",
    )
    result = _dispatcher(client).dispatch_one("worker", NOW)
    assert result.retryable is True
    message = _message()
    assert message.status == "retryable"
    assert message.last_error == "knowledge_graph_unavailable"
    assert "private upstream response" not in (message.last_error or "")

    with SessionLocal() as session:
        row = session.get(OutboxMessage, message.id)
        row.status = "pending"
        row.next_attempt_at = NOW - timedelta(seconds=1)
        row.attempts = 0
        session.commit()
    client.failure = None
    client.contract_version = "wrong-contract"
    result = _dispatcher(client).dispatch_one("worker", NOW)
    assert result.retryable is False
    assert result.error == "knowledge_graph_contract_mismatch"
    assert _message().status == "dead_letter"


def test_401_is_retryable_and_recovers_after_credentials_are_fixed():
    _publish()
    client = FakeKGClient()
    client.failure = KnowledgeGraphError(
        "private login response",
        status_code=401,
        error_code="invalid_credentials",
    )
    dispatcher = _dispatcher(client)

    first = dispatcher.dispatch_one("worker", NOW)

    assert first.retryable is True
    assert first.error == "knowledge_graph_401"
    assert _message().status == "retryable"
    assert _message().last_error == "knowledge_graph_401"
    client.failure = None
    _make_due()

    recovered = dispatcher.dispatch_one("worker", NOW)

    assert recovered.delivered is True
    assert _message().status == "delivered"
    assert len(client.remote_facts) == 1


def test_401_obeys_max_attempts_and_403_is_immediately_permanent():
    _publish()
    client = FakeKGClient()
    client.failure = KnowledgeGraphError(
        "login failed",
        status_code=401,
        error_code="invalid_credentials",
    )
    dispatcher = _dispatcher(client, max_attempts=2)

    assert dispatcher.dispatch_one("worker", NOW).retryable is True
    assert _message().status == "retryable"
    _make_due()
    assert dispatcher.dispatch_one("worker", NOW).retryable is True
    assert _message().status == "dead_letter"
    assert _message().attempts == 2

    reset_database_data()
    _publish()
    client.failure = KnowledgeGraphError(
        "permission denied response",
        status_code=403,
        error_code="upstream_permission_denied",
    )

    forbidden = _dispatcher(client).dispatch_one("worker", NOW)

    assert forbidden.retryable is False
    assert forbidden.error == "knowledge_graph_forbidden"
    assert _message().status == "dead_letter"
    assert _message().last_error == "knowledge_graph_forbidden"


def test_5xx_remains_retryable_without_storing_response_body():
    _publish()
    client = FakeKGClient()
    client.failure = KnowledgeGraphError(
        "Authorization secret and full response body",
        status_code=502,
        error_code="knowledge_graph_bad_gateway",
    )

    result = _dispatcher(client).dispatch_one("worker", NOW)

    assert result.retryable is True
    assert result.error == "knowledge_graph_bad_gateway"
    assert _message().status == "retryable"
    assert _message().last_error == "knowledge_graph_bad_gateway"
    assert "Authorization" not in (_message().last_error or "")


def test_max_attempts_and_stale_claim_recovery():
    _publish()
    client = FakeKGClient()
    client.failure = KnowledgeGraphUnavailable("timeout")
    dispatcher = _dispatcher(client, lease_seconds=2, max_attempts=2)

    first = dispatcher.dispatch_one("worker-a", NOW)
    assert first.retryable is True
    _make_due()
    second = dispatcher.dispatch_one("worker-a", NOW)
    assert second.retryable is True
    assert _message().status == "dead_letter"

    reset_database_data()
    _publish()
    with SessionLocal() as session:
        claimed = SqlAlchemyOutboxRepository(
            session, lease_seconds=2
        ).claim(
            "crashed-worker",
            NOW,
            event_types={JDPublicationKnowledgeGraphHandler.event_type},
        )
        session.commit()
        assert claimed is not None
    client = FakeKGClient()
    recovered = _dispatcher(client, lease_seconds=2).dispatch_one(
        "recovery-worker", NOW + timedelta(seconds=3)
    )
    assert recovered.delivered is True
    assert _message().status == "delivered"


def test_dead_letter_requeue_preserves_history_and_is_idempotent():
    _publish()
    client = FakeKGClient()
    client.contract_version = "wrong-contract"
    _dispatcher(client).dispatch_one("worker", NOW)
    failed = _message()
    assert failed.status == "dead_letter"

    first = _outbox_operations().requeue(
        AccountActor("admin-id", "admin"), failed.event_id, now=NOW
    )
    second = _outbox_operations().requeue(
        AccountActor("admin-id", "admin"), failed.event_id, now=NOW
    )

    assert first.status.value == "pending"
    assert second.status.value == "pending"
    assert second.attempts == failed.attempts == 1
    assert second.last_error == failed.last_error
    assert second.lease_owner is None
    assert second.lease_until is None


def test_delivered_and_active_claimed_events_cannot_be_requeued():
    _publish()
    assert _dispatcher(FakeKGClient()).dispatch_one("worker", NOW).delivered
    delivered = _message()
    with pytest.raises(OutboxRequeueConflict, match="Delivered"):
        _outbox_operations().requeue(
            AccountActor("admin-id", "admin"), delivered.event_id, now=NOW
        )

    reset_database_data()
    _publish()
    with SessionLocal() as session:
        claimed = SqlAlchemyOutboxRepository(
            session, lease_seconds=60
        ).claim("active-worker", NOW)
        session.commit()
    assert claimed is not None
    with pytest.raises(OutboxRequeueConflict, match="Active claimed"):
        _outbox_operations().requeue(
            AccountActor("admin-id", "admin"),
            claimed.draft.event.event_id,
            now=NOW + timedelta(seconds=1),
        )


def test_requeue_api_is_strictly_authorized():
    _publish()
    client = FakeKGClient()
    client.contract_version = "wrong-contract"
    _dispatcher(client).dispatch_one("worker", NOW)
    event_id = _message().event_id
    path = f"/api/v1/outbox-events/{event_id}/requeue"

    assert api_client.post(path).status_code == 401
    reviewer = _token("outbox-requeue-reviewer", "reviewer")
    assert (
        api_client.post(
            path, headers={"Authorization": f"Bearer {reviewer}"}
        ).status_code
        == 403
    )
    admin = _token("outbox-requeue-admin", "admin")
    response = api_client.post(
        path, headers={"Authorization": f"Bearer {admin}"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "pending"
    assert response.json()["data"]["attempts"] == 1
    developer = _token("outbox-requeue-developer", "developer")
    repeated = api_client.post(
        path, headers={"Authorization": f"Bearer {developer}"}
    )
    assert repeated.status_code == 200
    assert repeated.json()["data"]["status"] == "pending"


def test_remote_success_before_local_completion_converges_idempotently():
    _publish()
    client = FakeKGClient()
    failed_once = {"value": False}

    class FailAfterRemoteSession(Session):
        def commit(self):
            if client.import_calls and not failed_once["value"]:
                failed_once["value"] = True
                self.rollback()
                raise RuntimeError("simulated local commit failure")
            return super().commit()

    factory = sessionmaker(
        bind=engine,
        class_=FailAfterRemoteSession,
        expire_on_commit=False,
    )
    dispatcher = SqlAlchemyOutboxDispatcher(
        factory,
        build_knowledge_graph_outbox_handlers(
            factory, client, enabled=True
        ),
        event_types={JDPublicationKnowledgeGraphHandler.event_type},
        lease_seconds=2,
    )

    first = dispatcher.dispatch_one("worker", NOW)
    assert first.retryable is True
    failed = _message()
    requeued = _outbox_operations().requeue(
        AccountActor("requeue-admin", "admin"),
        failed.event_id,
        now=NOW + timedelta(seconds=3),
    )
    assert requeued.status.value == "pending"
    second = _dispatcher(client, lease_seconds=2).dispatch_one(
        "recovery", NOW + timedelta(seconds=3)
    )
    assert second.delivered is True
    assert len(client.import_calls) == 2
    assert len(client.remote_facts) == 1
    assert _message().status == "delivered"


def test_identity_mismatch_is_dead_lettered_without_kg_call():
    _publish()
    with SessionLocal() as session:
        row = session.query(OutboxMessage).one()
        row.payload = {**row.payload, "source_jd_id": "stale-source"}
        session.commit()
    client = FakeKGClient()

    result = _dispatcher(client).dispatch_one("worker", NOW)

    assert result.error == "jd_publication_event_identity_mismatch"
    assert _message().status == "dead_letter"
    assert client.import_calls == []


def test_new_source_versions_sync_independently():
    _, first = _publish()
    _, second = _publish(
        _envelope(
            "Backend Engineer uses Python daily and Docker",
            source_version="2",
        )
    )
    client = FakeKGClient()
    dispatcher = _dispatcher(client)

    assert dispatcher.dispatch_one("worker-1", NOW).delivered is True
    assert dispatcher.dispatch_one("worker-2", NOW).delivered is True

    assert first.id != second.id
    assert len(client.remote_facts) == 2
    with SessionLocal() as session:
        assert (
            session.query(OutboxMessage)
            .filter(OutboxMessage.status == "delivered")
            .count()
            == 2
        )


def test_manual_sync_reuses_publication_event_mapper_and_idempotency():
    draft, publication = _publish()
    client = FakeKGClient()
    factory = KnowledgeGraphAdapterFactory(
        SessionLocal, client, enabled=True
    )

    first = factory.sync_jd(
        draft.jd_id, AccountActor("manual-admin", "admin")
    )
    second = factory.sync_jd(
        draft.jd_id, AccountActor("manual-admin", "admin")
    )

    assert first.idempotent is False
    assert second.idempotent is True
    assert len(client.import_calls) == 1
    with SessionLocal() as session:
        assert session.query(OutboxMessage).count() == 1
        message = session.query(OutboxMessage).one()
        assert message.idempotency_key == publication.idempotency_key
        assert message.status == "delivered"
        mapping = (
            session.query(KnowledgeGraphEntityMapping)
            .filter_by(entity_type="document", main_system_id=draft.jd_id)
            .one()
        )
        assert mapping.sync_version == client.import_calls[0]["payload"][
            "source_fact_version"
        ]


def test_manual_sync_rematerializes_delivered_publication_after_mapping_change():
    draft, _ = _publish()
    client = FakeKGClient()
    factory = KnowledgeGraphAdapterFactory(SessionLocal, client, enabled=True)
    actor = AccountActor("manual-admin", "admin")

    first = factory.sync_jd(draft.jd_id, actor)
    client.skills = [{"skill_id": "KG_PY", "canonical_name": "Python"}]
    with SessionLocal() as session:
        KnowledgeGraphAdapter(session, client).set_mapping(
            "skill", "skill-python", "KG_PY"
        )
        session.commit()
    second = factory.sync_jd(draft.jd_id, actor)
    third = factory.sync_jd(draft.jd_id, actor)

    assert first.idempotent is False
    assert second.idempotent is False
    assert third.idempotent is True
    assert len(client.import_calls) == 2
    assert (
        client.import_calls[1]["payload"]["source_fact_version"]
        > client.import_calls[0]["payload"]["source_fact_version"]
    )


def test_stable_error_code_does_not_leak_payload_or_credentials(caplog):
    _publish()
    client = FakeKGClient()
    client.failure = KnowledgeGraphError(
        "TOKEN secret full JD Evidence response body",
        status_code=503,
        error_code="knowledge_graph_unavailable",
    )

    with caplog.at_level("INFO"):
        _dispatcher(client).dispatch_one("safe-worker", NOW)

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "TOKEN" not in rendered
    assert "Evidence" not in rendered
    assert _message().last_error == "knowledge_graph_unavailable"


def test_knowledge_graph_manual_management_api_rejects_anonymous_requests():
    response = api_client.post(
        "/api/v1/integrations/knowledge-graph/jds/example/sync"
    )
    assert response.status_code == 401
