from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.application.evaluation_tasks import DEFAULT_PERSISTENCE_VERSIONS
from app.domain.privacy import UrlSafetyPolicy, find_pii
from app.domain.profiles import CVMatchProfile
from app.domain.queue import TaskQueueMessage, task_message_id
from app.domain.tasks import AuditRecord, AuditSecurityError
from app.infrastructure.memory_repositories import InMemoryPersistence
from app.infrastructure.memory_task_queue import InMemoryTaskQueue
from app.infrastructure.sqlalchemy_repositories import SQLAlchemyPersistence


@pytest.mark.parametrize(
    "value",
    [
        "+81-3-1234-5678",
        "(415)5552671",
        "+44 (0) 20-7946-0958",
        "北京市海淀区中关村大街27号",
        "地址：上海浦东新区世纪大道100号",
        "221B Baker Street, London",
        "联系人：张三",
        "My name is John Smith",
        "微信：john_123",
        "QQ: 12345678",
        "Telegram: @john_doe",
        "@john_doe",
        "https://linkedin.com/in/john-doe",
        "https://people.example/alice/contact",
        "reach me at john_doe",
    ],
)
def test_expanded_pii_positive_matrix(value):
    assert find_pii(value), value


@pytest.mark.parametrize(
    "value",
    [
        "Python 3.12 and Redis 7.2",
        "Kubernetes, PostgreSQL, SQLAlchemy",
        "availability improved by 99.5% across 120 nodes",
        "Tsinghua University distributed systems laboratory",
        "Acme Technology platform migration project",
        "https://docs.python.org/3/library/asyncio.html",
        "https://kubernetes.io/docs/concepts/workloads/",
        "https://github.com/redis/redis",
    ],
)
def test_expanded_pii_negative_matrix(value):
    assert not find_pii(value), value


def test_technical_url_policy_is_explicitly_configurable(monkeypatch):
    value = "https://engineering.example/runtime"
    assert find_pii(value)
    policy = UrlSafetyPolicy(technical_hosts=frozenset({"engineering.example"}))
    assert not find_pii(value, url_policy=policy)
    monkeypatch.setenv("MATCHING_PII_TECHNICAL_URL_HOSTS", "engineering.example")
    assert not find_pii(value)
    monkeypatch.setenv("MATCHING_PII_TECHNICAL_URL_HOSTS", "linkedin.com")
    assert find_pii("https://linkedin.com/in/john-doe")


def test_profile_boundary_allows_technical_url_and_rejects_personal_url(
    ready_cv_json, clone
):
    safe = clone(ready_cv_json)
    safe["work_experiences"][0]["responsibilities"].append(
        "Reference https://docs.python.org/3/library/asyncio.html"
    )
    CVMatchProfile.model_validate(safe)

    unsafe = clone(ready_cv_json)
    unsafe["work_experiences"][0]["responsibilities"].append(
        "Profile https://linkedin.com/in/john-doe"
    )
    with pytest.raises(ValidationError):
        CVMatchProfile.model_validate(unsafe)


def _audit() -> AuditRecord:
    return AuditRecord(
        audit_id="audit_safe123",
        task_id="task_safe123",
        access_scope="user:0123456789abcdef01234567:abcdef0123456789abcdef01",
        event_type="task_created",
        from_status=None,
        to_status="pending",
        attempt=0,
        reason_code=None,
        idempotency_key="a" * 64,
        algorithm_version=DEFAULT_PERSISTENCE_VERSIONS.signature,
        occurred_at=datetime.now(timezone.utc),
    )


def test_audit_model_rejects_raw_pii_without_echo():
    with pytest.raises(ValidationError) as exc:
        AuditRecord.model_validate(
            _audit().model_dump() | {"access_scope": "alice@example.com"}
        )
    assert "alice@example.com" not in str(exc.value)

    with pytest.raises(ValidationError) as reason_exc:
        AuditRecord.model_validate(
            _audit().model_dump()
            | {
                "access_scope": _audit().access_scope,
                "reason_code": "contact alice@example.com",
            }
        )
    assert "alice@example.com" not in str(reason_exc.value)


@pytest.mark.parametrize("provider", ["memory", "sql"])
@pytest.mark.parametrize("unsafe_scope", ["alice@example.com", "+81-3-1234-5678"])
def test_audit_repository_last_line_guard_rejects_model_bypass(
    provider, unsafe_scope
):
    unsafe = _audit().model_copy(update={"access_scope": unsafe_scope})
    persistence = (
        InMemoryPersistence()
        if provider == "memory"
        else SQLAlchemyPersistence.from_url("sqlite+pysqlite:///:memory:")
    )
    with pytest.raises(AuditSecurityError) as exc, persistence.unit_of_work() as uow:
        uow.audits.append(unsafe)
    assert str(exc.value) == "AUDIT_VALUE_UNSAFE"


def test_memory_worker_dlq_is_safe_envelope_without_payload_or_pii():
    queue = InMemoryTaskQueue()
    message = TaskQueueMessage(
        message_id=task_message_id("task-safe", "version-v1"),
        task_id="task-safe",
        access_scope="user:0123456789abcdef01234567:abcdef0123456789abcdef01",
        version_signature="version-v1",
        published_at=datetime.now(timezone.utc),
    )
    queue.publish(message)
    delivery = queue.consume("worker-safe")
    assert delivery is not None
    queue.dead_letter(delivery, reason_code="TASK_ATTEMPTS_EXHAUSTED")

    envelope = queue.dead_letters[0].model_dump(mode="json")
    assert "message" not in envelope
    assert "payload" not in envelope
    assert message.access_scope not in str(envelope)
    assert not find_pii(envelope)
