from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
import redis
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import DBAPIError

from app.application.evaluation import MatchEvaluationService
from app.application.evaluation_tasks import EvaluationTaskService
from app.application.learning_paths import LearningPathService
from app.domain.outbox import outbox_id_for_task
from app.domain.queue import TaskQueueMessage, task_message_id
from app.infrastructure.redis_task_queue import RedisTaskQueue
from app.infrastructure.sqlalchemy_repositories import SQLAlchemyPersistence

ROOT = Path(__file__).parents[1]
POSTGRES_URL = os.getenv("MATCHING_TEST_POSTGRES_URL")
REDIS_URL = os.getenv("MATCHING_TEST_REDIS_URL")


def _alembic(url: str, action: str, revision: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    getattr(command, action)(config, revision)


def _payload(cv: dict, position: dict) -> dict:
    return {"cv_profile": cv, "position_profile": position}


def _service(url: str, **kwargs) -> tuple[SQLAlchemyPersistence, EvaluationTaskService]:
    persistence = SQLAlchemyPersistence.from_url(url, pool_pre_ping=True)
    evaluation = MatchEvaluationService()
    service = EvaluationTaskService(
        persistence.unit_of_work,
        evaluation,
        LearningPathService(evaluation),
        **kwargs,
    )
    return persistence, service


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.mark.postgres_integration
@pytest.mark.skipif(not POSTGRES_URL, reason="dedicated PostgreSQL is not configured")
def test_live_postgresql_migration_lifecycle_and_schema_contract():
    assert POSTGRES_URL is not None
    _alembic(POSTGRES_URL, "downgrade", "base")
    _alembic(POSTGRES_URL, "upgrade", "head")
    _alembic(POSTGRES_URL, "upgrade", "head")
    _alembic(POSTGRES_URL, "downgrade", "base")
    _alembic(POSTGRES_URL, "upgrade", "head")

    with psycopg.connect(_psycopg_url(POSTGRES_URL)) as connection:
        jsonb_columns = connection.execute(
            """
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema='public' AND data_type='jsonb'
            """
        ).fetchall()
        assert ("evaluation_tasks", "versions_json") in jsonb_columns
        assert ("outbox_records", "payload") in jsonb_columns
        assert ("vector_outbox_events", "payload") in jsonb_columns
        constraints = {
            row[0]
            for row in connection.execute(
                "SELECT conname FROM pg_constraint WHERE connamespace='public'::regnamespace"
            )
        }
        assert {
            "fk_audit_records_task",
            "fk_persisted_evaluations_task",
            "fk_outbox_records_task",
            "uq_evaluation_tasks_idempotency",
            "uq_outbox_records_task",
            "uq_outbox_records_message",
            "uq_vector_index_reference_lineage",
            "uq_vector_outbox_deduplication",
            "uq_vector_outbox_audits_sequence",
            "fk_vector_outbox_audits_event",
        } <= constraints
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname='public'"
            )
        }
        assert {
            "ix_evaluation_tasks_claim",
            "ix_outbox_records_dispatch",
            "ix_audit_records_scope_task_time",
            "ix_vector_index_references_entity",
            "ix_vector_outbox_events_claim",
            "ix_vector_outbox_audits_event_time",
        } <= indexes


@pytest.mark.postgres_integration
@pytest.mark.skipif(not POSTGRES_URL, reason="dedicated PostgreSQL is not configured")
def test_live_postgresql_dispatcher_and_worker_concurrency(
    ready_cv_json, ready_position_json
):
    assert POSTGRES_URL is not None
    _alembic(POSTGRES_URL, "upgrade", "head")
    scope = f"postgres-concurrency-{uuid.uuid4().hex}"
    p1, service1 = _service(POSTGRES_URL)
    p2, service2 = _service(POSTGRES_URL)
    try:
        task = service1.submit(
            _payload(ready_cv_json, ready_position_json),
            f"claim-{uuid.uuid4().hex}",
            scope,
            execute_immediately=False,
            create_outbox=True,
        ).task
        assert task is not None
        target_outbox_id = outbox_id_for_task(task.task_id)

        def claim_outbox(persistence, owner):
            claim_now = datetime.now(timezone.utc)
            with persistence.unit_of_work() as uow:
                record = uow.outbox.claim(
                    owner,
                    claim_now,
                    claim_now + timedelta(seconds=1),
                    target_outbox_id,
                )
                uow.commit()
            return record

        with ThreadPoolExecutor(max_workers=2) as executor:
            records = list(
                executor.map(
                    lambda item: claim_outbox(*item),
                    ((p1, "dispatcher-a"), (p2, "dispatcher-b")),
                )
            )
        claimed = [item for item in records if item is not None]
        assert len(claimed) == 1

        before_expiry = claim_outbox(p2, "dispatcher-c")
        assert before_expiry is None
        time.sleep(1.05)
        recovered = claim_outbox(p2, "dispatcher-c")
        assert recovered is not None
        assert recovered.claimed_by == "dispatcher-c"

        worker_scope = f"postgres-worker-{uuid.uuid4().hex}"
        worker_task = service1.submit(
            _payload(ready_cv_json, ready_position_json),
            f"worker-{uuid.uuid4().hex}",
            worker_scope,
            execute_immediately=False,
        ).task
        assert worker_task is not None

        def claim_task(service, owner):
            result = service.claim(
                worker_task.task_id,
                worker_scope,
                lease_owner=owner,
                lease_seconds=30,
            )
            return owner, result.task

        with ThreadPoolExecutor(max_workers=2) as executor:
            worker_claims = list(
                executor.map(
                    lambda item: claim_task(*item),
                    ((service1, "worker-a"), (service2, "worker-b")),
                )
            )
        winners = [owner for owner, item in worker_claims if item.lease_owner == owner]
        assert len(winners) == 1
        winner = winners[0]
        loser_service = service2 if winner == "worker-a" else service1
        denied = loser_service.execute_claimed(
            worker_task.task_id, worker_scope, lease_owner="not-the-owner"
        )
        assert denied.error_code == "TASK_LEASE_NOT_OWNED"
        winner_service = service1 if winner == "worker-a" else service2
        succeeded = winner_service.execute_claimed(
            worker_task.task_id, worker_scope, lease_owner=winner
        ).task
        assert succeeded is not None and succeeded.status == "succeeded"
        audits = winner_service.audit_records(worker_task.task_id, worker_scope)
        assert sum(item.event_type == "task_started" for item in audits) == 1
        assert sum(item.event_type == "task_succeeded" for item in audits) == 1
    finally:
        p1.dispose()
        p2.dispose()


@pytest.mark.postgres_integration
@pytest.mark.skipif(not POSTGRES_URL, reason="dedicated PostgreSQL is not configured")
def test_live_postgresql_idempotency_and_atomic_disconnects(
    ready_cv_json, ready_position_json
):
    assert POSTGRES_URL is not None
    _alembic(POSTGRES_URL, "upgrade", "head")
    scope = f"postgres-idempotency-{uuid.uuid4().hex}"
    key = f"idem-{uuid.uuid4().hex}"
    p1, service1 = _service(POSTGRES_URL)
    p2, service2 = _service(POSTGRES_URL)
    try:
        def submit(service):
            return service.submit(
                _payload(ready_cv_json, ready_position_json),
                key,
                scope,
                execute_immediately=False,
                create_outbox=True,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            submissions = list(executor.map(submit, (service1, service2)))
        assert len({item.task.task_id for item in submissions}) == 1
        task_id = submissions[0].task.task_id
        with psycopg.connect(_psycopg_url(POSTGRES_URL)) as connection:
            counts = connection.execute(
                """
                SELECT
                  (SELECT count(*) FROM evaluation_tasks WHERE access_scope=%s),
                  (SELECT count(*) FROM outbox_records WHERE access_scope=%s),
                  (SELECT count(*) FROM audit_records WHERE access_scope=%s),
                  (SELECT count(*) FROM persisted_evaluations WHERE access_scope=%s)
                """,
                (scope, scope, scope, scope),
            ).fetchone()
        assert counts == (1, 1, 1, 0)

        # Simulate response/connection loss after a successful commit: replay is safe.
        p1.dispose()
        replay_persistence, replay_service = _service(POSTGRES_URL)
        try:
            replay = submit(replay_service)
            assert replay.task.task_id == task_id
            assert replay.created is False
        finally:
            replay_persistence.dispose()

        rollback_scope = f"postgres-rollback-{uuid.uuid4().hex}"
        with psycopg.connect(_psycopg_url(POSTGRES_URL)) as connection:
            connection.execute(
                """
                CREATE OR REPLACE FUNCTION acceptance_reject_outbox() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'acceptance failure'; END $$;
                DROP TRIGGER IF EXISTS acceptance_reject_outbox ON outbox_records;
                CREATE TRIGGER acceptance_reject_outbox BEFORE INSERT ON outbox_records
                FOR EACH ROW EXECUTE FUNCTION acceptance_reject_outbox()
                """
            )
            connection.commit()
        try:
            with pytest.raises(Exception, match="acceptance failure"):
                service2.submit(
                    _payload(ready_cv_json, ready_position_json),
                    f"rollback-{uuid.uuid4().hex}",
                    rollback_scope,
                    execute_immediately=False,
                    create_outbox=True,
                )
        finally:
            with psycopg.connect(_psycopg_url(POSTGRES_URL)) as connection:
                connection.execute(
                    "DROP TRIGGER IF EXISTS acceptance_reject_outbox ON outbox_records"
                )
                connection.execute("DROP FUNCTION IF EXISTS acceptance_reject_outbox()")
                connection.commit()
        with psycopg.connect(_psycopg_url(POSTGRES_URL)) as connection:
            rollback_counts = connection.execute(
                """
                SELECT
                  (SELECT count(*) FROM evaluation_tasks WHERE access_scope=%s),
                  (SELECT count(*) FROM outbox_records WHERE access_scope=%s),
                  (SELECT count(*) FROM audit_records WHERE access_scope=%s)
                """,
                (rollback_scope, rollback_scope, rollback_scope),
            ).fetchone()
        assert rollback_counts == (0, 0, 0)

        success_scope = f"postgres-success-rollback-{uuid.uuid4().hex}"
        pending = service2.submit(
            _payload(ready_cv_json, ready_position_json),
            f"success-rollback-{uuid.uuid4().hex}",
            success_scope,
            execute_immediately=False,
        ).task
        assert pending is not None
        with psycopg.connect(_psycopg_url(POSTGRES_URL)) as connection:
            connection.execute(
                """
                CREATE OR REPLACE FUNCTION acceptance_reject_success() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                  IF NEW.event_type = 'task_succeeded' THEN
                    RAISE EXCEPTION 'reject succeeded audit';
                  END IF;
                  RETURN NEW;
                END $$;
                DROP TRIGGER IF EXISTS acceptance_reject_success ON audit_records;
                CREATE TRIGGER acceptance_reject_success BEFORE INSERT ON audit_records
                FOR EACH ROW EXECUTE FUNCTION acceptance_reject_success()
                """
            )
            connection.commit()
        try:
            claimed = service2.claim(
                pending.task_id,
                success_scope,
                lease_owner="atomic-worker",
                lease_seconds=30,
            ).task
            assert claimed is not None
            failed = service2.execute_claimed(
                pending.task_id, success_scope, lease_owner="atomic-worker"
            ).task
            assert failed is not None and failed.status == "failed"
        finally:
            with psycopg.connect(_psycopg_url(POSTGRES_URL)) as connection:
                connection.execute(
                    "DROP TRIGGER IF EXISTS acceptance_reject_success ON audit_records"
                )
                connection.execute("DROP FUNCTION IF EXISTS acceptance_reject_success()")
                connection.commit()
        with psycopg.connect(_psycopg_url(POSTGRES_URL)) as connection:
            atomic_counts = connection.execute(
                """
                SELECT
                  (SELECT count(*) FROM persisted_evaluations WHERE access_scope=%s),
                  (SELECT count(*) FROM audit_records
                   WHERE access_scope=%s AND event_type='task_succeeded'),
                  (SELECT count(*) FROM audit_records
                   WHERE access_scope=%s AND event_type='task_failed')
                """,
                (success_scope, success_scope, success_scope),
            ).fetchone()
        assert atomic_counts == (0, 0, 1)

        disconnect_scope = f"postgres-disconnect-{uuid.uuid4().hex}"
        with psycopg.connect(_psycopg_url(POSTGRES_URL)) as connection:
            connection.execute(
                """
                CREATE OR REPLACE FUNCTION acceptance_disconnect_outbox() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                  PERFORM pg_terminate_backend(pg_backend_pid());
                  RETURN NEW;
                END $$;
                DROP TRIGGER IF EXISTS acceptance_disconnect_outbox ON outbox_records;
                CREATE TRIGGER acceptance_disconnect_outbox BEFORE INSERT ON outbox_records
                FOR EACH ROW EXECUTE FUNCTION acceptance_disconnect_outbox()
                """
            )
            connection.commit()
        try:
            with pytest.raises(DBAPIError):
                service2.submit(
                    _payload(ready_cv_json, ready_position_json),
                    f"disconnect-{uuid.uuid4().hex}",
                    disconnect_scope,
                    execute_immediately=False,
                    create_outbox=True,
                )
        finally:
            with psycopg.connect(_psycopg_url(POSTGRES_URL)) as connection:
                connection.execute(
                    "DROP TRIGGER IF EXISTS acceptance_disconnect_outbox ON outbox_records"
                )
                connection.execute(
                    "DROP FUNCTION IF EXISTS acceptance_disconnect_outbox()"
                )
                connection.commit()
        with psycopg.connect(_psycopg_url(POSTGRES_URL)) as connection:
            disconnect_counts = connection.execute(
                """
                SELECT
                  (SELECT count(*) FROM evaluation_tasks WHERE access_scope=%s),
                  (SELECT count(*) FROM outbox_records WHERE access_scope=%s),
                  (SELECT count(*) FROM audit_records WHERE access_scope=%s)
                """,
                (disconnect_scope, disconnect_scope, disconnect_scope),
            ).fetchone()
        assert disconnect_counts == (0, 0, 0)
    finally:
        p1.dispose()
        p2.dispose()


def _redis_client() -> redis.Redis:
    assert REDIS_URL is not None
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def _queue(name: str) -> RedisTaskQueue:
    assert REDIS_URL is not None
    return RedisTaskQueue.from_url(
        REDIS_URL, queue_name=name, visibility_timeout_seconds=0.05
    )


def _message(task_id: str) -> TaskQueueMessage:
    version = "versions-v1"
    return TaskQueueMessage(
        message_id=task_message_id(task_id, version),
        task_id=task_id,
        access_scope="user:0123456789abcdef01234567:abcdef0123456789abcdef01",
        version_signature=version,
        published_at=datetime.now(timezone.utc),
    )


@pytest.mark.redis_integration
@pytest.mark.skipif(not REDIS_URL, reason="dedicated Redis is not configured")
def test_live_redis_stream_retry_dlq_visibility_and_poison():
    client = _redis_client()
    client.flushdb()
    queue = _queue(f"acceptance:{uuid.uuid4().hex}")

    first_message = _message("task-live-first")
    queue.publish(first_message)
    first = queue.consume("worker-a")
    assert first is not None and first.message == first_message
    time.sleep(0.07)
    reclaimed = queue.consume("worker-b")
    assert reclaimed is not None
    assert reclaimed.receipt_id == first.receipt_id
    assert reclaimed.delivery_count == 2
    queue.retry(reclaimed, delay_seconds=0, reason_code="TRANSIENT_FAILURE")
    assert client.zcard(queue.retry_queue_name) == 1
    retried = queue.consume("worker-c")
    assert retried is not None and retried.delivery_count == 3
    queue.dead_letter(retried, reason_code="TASK_ATTEMPTS_EXHAUSTED")
    assert client.hlen(queue.dead_letter_queue_name) == 1
    envelope = json.loads(next(iter(client.hgetall(queue.dead_letter_queue_name).values())))
    assert "payload" not in envelope and "message" not in envelope
    assert first_message.access_scope not in json.dumps(envelope)

    client.xadd(
        queue.queue_name,
        {"payload": "{malformed alice@example.com", "delivery_count": "0"},
    )
    valid = _message("task-after-live-poison")
    queue.publish(valid)
    delivery = queue.consume("worker-after-poison")
    assert delivery is not None and delivery.message == valid
    assert client.hlen(queue.dead_letter_queue_name) == 2
    assert "alice@example.com" not in json.dumps(client.hgetall(queue.dead_letter_queue_name))
    queue.acknowledge(delivery)


@pytest.mark.redis_integration
@pytest.mark.skipif(not REDIS_URL, reason="dedicated Redis is not configured")
@pytest.mark.parametrize("operation", ["consume", "acknowledge", "retry", "dead_letter"])
def test_live_redis_nogroup_settlement_and_current_process_recovery(operation):
    client = _redis_client()
    client.flushdb()
    queue = _queue(f"nogroup:{operation}:{uuid.uuid4().hex}")
    message = _message(f"task-live-{operation}")
    queue.publish(message)
    queue._ensure_group()

    if operation == "consume":
        client.xgroup_destroy(queue.queue_name, queue.consumer_group)
        delivery = queue.consume("worker")
        assert delivery is not None and delivery.message == message
        queue.acknowledge(delivery)
        return

    delivery = queue.consume("worker")
    assert delivery is not None
    client.xgroup_destroy(queue.queue_name, queue.consumer_group)
    if operation == "acknowledge":
        queue.acknowledge(delivery)
    elif operation == "retry":
        queue.retry(delivery, delay_seconds=60, reason_code="TRANSIENT_FAILURE")
        queue.retry(delivery, delay_seconds=60, reason_code="TRANSIENT_FAILURE")
        assert client.zcard(queue.retry_queue_name) == 1
    else:
        queue.dead_letter(delivery, reason_code="TASK_ATTEMPTS_EXHAUSTED")
        queue.dead_letter(delivery, reason_code="TASK_ATTEMPTS_EXHAUSTED")
        assert client.hlen(queue.dead_letter_queue_name) == 1
    assert client.xlen(queue.queue_name) == 0

    client.delete(queue.queue_name)
    client.flushdb()
    recovered = _message(f"task-recovered-{operation}")
    queue.publish(recovered)
    next_delivery = queue.consume("worker-next")
    assert next_delivery is not None and next_delivery.message == recovered
    queue.acknowledge(next_delivery)
