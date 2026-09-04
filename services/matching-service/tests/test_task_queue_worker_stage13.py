from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread

import pytest
from fastapi.testclient import TestClient

from app.application.evaluation import MatchEvaluationService
from app.application.evaluation_tasks import EvaluationTaskService
from app.application.learning_paths import LearningPathService
from app.application.task_submission import TaskSubmissionService
from app.application.task_worker import EvaluationTaskWorker
from app.bootstrap.application import create_app
from app.domain.queue import TaskQueueMessage, task_message_id
from app.infrastructure.memory_repositories import InMemoryPersistence
from app.infrastructure.memory_task_queue import InMemoryTaskQueue
from app.infrastructure.queue_configuration import build_task_queue
from app.infrastructure.redis_task_queue import RedisTaskQueue


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 27, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class _FailOnce:
    def __init__(self, exception_type: type[BaseException] = RuntimeError) -> None:
        self.delegate = MatchEvaluationService()
        self.exception_type = exception_type
        self.calls = 0

    def evaluate(self, payload: object, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise self.exception_type("simulated worker interruption")
        return self.delegate.evaluate(payload, **kwargs)


class _AlwaysFail:
    def evaluate(self, payload: object, **kwargs):
        raise RuntimeError("persistent evaluation failure")


class _BlockingEvaluation:
    def __init__(self) -> None:
        self.delegate = MatchEvaluationService()
        self.entered = Event()
        self.release = Event()
        self.lock = Lock()
        self.calls = 0

    def evaluate(self, payload: object, **kwargs):
        with self.lock:
            self.calls += 1
        self.entered.set()
        self.release.wait(5)
        return self.delegate.evaluate(payload, **kwargs)


class _CountingEvaluation:
    def __init__(self) -> None:
        self.delegate = MatchEvaluationService()
        self.calls = 0

    def evaluate(self, payload: object, **kwargs):
        self.calls += 1
        return self.delegate.evaluate(payload, **kwargs)


class _RecordingQueue(InMemoryTaskQueue):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.ack_workers: list[str] = []

    def acknowledge(self, delivery) -> None:
        self.ack_workers.append(delivery.worker_id)
        super().acknowledge(delivery)


def _services(
    evaluation: object | None = None,
    *,
    max_attempts: int = 3,
    before_success_commit=None,
    clock=None,
):
    storage = InMemoryPersistence()
    selected = evaluation or MatchEvaluationService()
    tasks = EvaluationTaskService(
        storage.unit_of_work,
        selected,
        LearningPathService(selected),
        max_attempts=max_attempts,
        before_success_commit=before_success_commit,
        clock=clock,
    )
    return storage, tasks


def _payload(cv: dict, position: dict) -> dict:
    return {"cv_profile": cv, "position_profile": position}


def _task_message(task, message_id: str | None = None) -> TaskQueueMessage:
    return TaskQueueMessage(
        message_id=message_id or task_message_id(task.task_id, task.versions.signature),
        task_id=task.task_id,
        access_scope=task.access_scope,
        version_signature=task.versions.signature,
        published_at=datetime.now(timezone.utc),
    )


def test_api_only_creates_task_and_publishes_lightweight_message(
    ready_cv_json, ready_position_json, auth_provider, auth_headers
):
    application = create_app(authentication_provider=auth_provider)
    client = TestClient(application)
    response = client.post(
        "/api/v1/evaluation-tasks",
        json=_payload(ready_cv_json, ready_position_json),
        headers={**auth_headers, "Idempotency-Key": "queue-api"},
    )

    data = response.json()["data"]
    assert data["task"]["status"] == "pending"
    assert data["task"]["attempt"] == 0
    delivery = application.state.task_queue.consume("inspection-worker")
    message = delivery.message.model_dump(mode="json")
    assert set(message) == {
        "message_id",
        "task_id",
        "access_scope",
        "version_signature",
        "published_at",
    }
    assert "cv_profile" not in message
    assert "position_profile" not in message
    application.state.task_queue.acknowledge(delivery)


def test_duplicate_delivery_executes_idempotently(
    ready_cv_json, ready_position_json, worker_auth_context
):
    evaluation = _CountingEvaluation()
    storage, tasks = _services(evaluation)
    queue = _RecordingQueue()
    submission = TaskSubmissionService(tasks, queue)
    first = submission.submit(
        _payload(ready_cv_json, ready_position_json), "duplicate-key", "tenant-a"
    )
    replay = submission.submit(
        _payload(ready_cv_json, ready_position_json), "duplicate-key", "tenant-a"
    )
    queue.publish(_task_message(first.task))
    owner = EvaluationTaskWorker(
        queue,
        tasks,
        worker_id="owner-worker",
        retry_interval_seconds=0,
        auth_context=worker_auth_context,
    )
    duplicate_cleaner = EvaluationTaskWorker(
        queue,
        tasks,
        worker_id="other-worker",
        retry_interval_seconds=0,
        auth_context=worker_auth_context,
    )

    assert first.task == replay.task
    assert queue.pending_count == 2
    assert owner.run_once().outcome == "acknowledged"
    completed = tasks.get_task(first.task.task_id, "tenant-a").task
    attempt_after_success = completed.attempt
    calls_after_first = evaluation.calls
    assert duplicate_cleaner.run_once().outcome == "acknowledged"
    assert len(storage.evaluations) == 1
    assert evaluation.calls == calls_after_first
    assert tasks.get_task(first.task.task_id, "tenant-a").task.attempt == attempt_after_success
    assert queue.ack_workers == ["owner-worker", "other-worker"]
    events = [item.event_type for item in tasks.audit_records(first.task.task_id, "tenant-a")]
    assert events.count("task_succeeded") == 1


def test_concurrent_workers_only_one_can_claim_and_commit(
    ready_cv_json, ready_position_json, worker_auth_context
):
    evaluation = _BlockingEvaluation()
    _, tasks = _services(evaluation)
    queue = _RecordingQueue()
    submission = TaskSubmissionService(tasks, queue)
    task = submission.submit(
        _payload(ready_cv_json, ready_position_json), "parallel-key", "tenant-a"
    ).task
    submission.submit(
        _payload(ready_cv_json, ready_position_json), "parallel-key", "tenant-a"
    )
    queue.publish(_task_message(task))
    first = EvaluationTaskWorker(
        queue, tasks, worker_id="worker-a", auth_context=worker_auth_context
    )
    second = EvaluationTaskWorker(
        queue, tasks, worker_id="worker-b", auth_context=worker_auth_context
    )
    first_results = []
    thread = Thread(target=lambda: first_results.append(first.run_once()))
    thread.start()
    assert evaluation.entered.wait(2)

    denied = second.run_once()
    assert queue.ack_workers == []
    assert queue.inflight_count == 2
    evaluation.release.set()
    thread.join(5)

    assert denied.outcome == "abandoned"
    assert denied.reason_code == "TASK_LEASE_HELD"
    assert first_results[0].outcome == "acknowledged"
    assert queue.ack_workers == ["worker-a"]
    assert queue.inflight_count == 1
    assert evaluation.calls > 0
    final = tasks.get_task(task.task_id, "tenant-a").task
    assert final.status == "succeeded"
    assert final.attempt == 1
    events = [item.event_type for item in tasks.audit_records(task.task_id, "tenant-a")]
    assert events.count("task_succeeded") == 1


@pytest.mark.parametrize(
    ("message_update", "reason_code"),
    [
        ({"access_scope": "tenant-b"}, "TASK_NOT_FOUND"),
        ({"task_id": "task_forged"}, "TASK_NOT_FOUND"),
        ({"version_signature": "versions-forged"}, "TASK_MESSAGE_VERSION_MISMATCH"),
    ],
)
def test_succeeded_duplicate_with_wrong_identity_is_dead_lettered(
    ready_cv_json,
    ready_position_json,
    worker_auth_context,
    message_update,
    reason_code,
):
    evaluation = _CountingEvaluation()
    _, tasks = _services(evaluation)
    queue = _RecordingQueue()
    submission = TaskSubmissionService(tasks, queue)
    task = submission.submit(
        _payload(ready_cv_json, ready_position_json), "forged-duplicate", "tenant-a"
    ).task
    owner = EvaluationTaskWorker(
        queue, tasks, worker_id="owner-worker", auth_context=worker_auth_context
    )
    assert owner.run_once().outcome == "acknowledged"

    values = {
        "task_id": task.task_id,
        "access_scope": task.access_scope,
        "version_signature": task.versions.signature,
        **message_update,
    }
    queue.publish(
        TaskQueueMessage(
            message_id=task_message_id(
                values["task_id"], values["version_signature"]
            ),
            published_at=datetime.now(timezone.utc),
            **values,
        )
    )
    result = EvaluationTaskWorker(
        queue, tasks, worker_id="other-worker", auth_context=worker_auth_context
    ).run_once()

    assert result.outcome == "dead_lettered"
    assert result.reason_code == reason_code
    assert queue.ack_workers == ["owner-worker"]
    assert queue.dead_letters[-1].reason_code == reason_code
    assert evaluation.calls > 0
    persisted = tasks.get_task(task.task_id, "tenant-a").task
    assert persisted.status == "succeeded"
    assert persisted.attempt == 1
    events = [item.event_type for item in tasks.audit_records(task.task_id, "tenant-a")]
    assert events.count("task_succeeded") == 1


def test_succeeded_duplicate_requires_matching_persisted_evaluation(
    ready_cv_json, ready_position_json, worker_auth_context
):
    storage, tasks = _services()
    queue = _RecordingQueue()
    submission = TaskSubmissionService(tasks, queue)
    first = submission.submit(
        _payload(ready_cv_json, ready_position_json), "missing-result", "tenant-a"
    )
    replay = submission.submit(
        _payload(ready_cv_json, ready_position_json), "missing-result", "tenant-a"
    )
    queue.publish(_task_message(first.task))
    owner = EvaluationTaskWorker(
        queue, tasks, worker_id="owner-worker", auth_context=worker_auth_context
    )
    assert owner.run_once().outcome == "acknowledged"
    completed = tasks.get_task(first.task.task_id, "tenant-a").task
    del storage.evaluations[("tenant-a", completed.evaluation_id)]

    result = EvaluationTaskWorker(
        queue, tasks, worker_id="other-worker", auth_context=worker_auth_context
    ).run_once()

    assert replay.task.task_id == first.task.task_id
    assert result.outcome == "dead_lettered"
    assert result.reason_code == "TASK_TERMINAL_EVALUATION_MISMATCH"
    assert queue.ack_workers == ["owner-worker"]
    events = [
        item.event_type for item in tasks.audit_records(first.task.task_id, "tenant-a")
    ]
    assert events.count("task_succeeded") == 1


def test_worker_restart_recovers_timed_out_running_task(
    ready_cv_json, ready_position_json, worker_auth_context
):
    clock = _Clock()
    evaluation = _FailOnce(SystemExit)
    _, tasks = _services(evaluation, clock=clock)
    queue = InMemoryTaskQueue(visibility_timeout_seconds=10, clock=clock)
    submission = TaskSubmissionService(tasks, queue, clock=clock)
    task = submission.submit(
        _payload(ready_cv_json, ready_position_json), "restart-key", "tenant-a"
    ).task
    first_worker = EvaluationTaskWorker(
        queue, tasks, worker_id="worker-before-crash", auth_context=worker_auth_context
    )

    with pytest.raises(SystemExit):
        first_worker.run_once()
    assert tasks.get_task(task.task_id, "tenant-a").task.status == "running"
    assert queue.inflight_count == 1

    clock.advance(11)
    restarted = EvaluationTaskWorker(
        queue, tasks, worker_id="worker-after-restart", auth_context=worker_auth_context
    )
    assert restarted.run_once().outcome == "acknowledged"
    recovered = tasks.get_task(task.task_id, "tenant-a").task
    assert recovered.status == "succeeded"
    assert recovered.attempt == 2
    assert queue.inflight_count == 0


def test_failed_message_is_retried_then_acknowledged(
    ready_cv_json, ready_position_json, worker_auth_context
):
    clock = _Clock()
    _, tasks = _services(_FailOnce())
    queue = InMemoryTaskQueue(visibility_timeout_seconds=10, clock=clock)
    task = TaskSubmissionService(tasks, queue, clock=clock).submit(
        _payload(ready_cv_json, ready_position_json), "retry-key", "tenant-a"
    ).task
    worker = EvaluationTaskWorker(
        queue,
        tasks,
        worker_id="worker",
        retry_interval_seconds=5,
        auth_context=worker_auth_context,
    )

    first = worker.run_once()
    assert first.outcome == "retried"
    assert tasks.get_task(task.task_id, "tenant-a").task.status == "failed"
    assert worker.run_once().outcome == "idle"
    clock.advance(5)
    assert worker.run_once().outcome == "acknowledged"
    assert tasks.get_task(task.task_id, "tenant-a").task.attempt == 2


def test_max_attempts_moves_message_to_dead_letter(
    ready_cv_json, ready_position_json, worker_auth_context
):
    clock = _Clock()
    _, tasks = _services(_AlwaysFail(), max_attempts=2)
    queue = InMemoryTaskQueue(visibility_timeout_seconds=10, clock=clock)
    task = TaskSubmissionService(tasks, queue, clock=clock).submit(
        _payload(ready_cv_json, ready_position_json), "dead-key", "tenant-a"
    ).task
    worker = EvaluationTaskWorker(
        queue,
        tasks,
        worker_id="worker",
        retry_interval_seconds=1,
        auth_context=worker_auth_context,
    )

    assert worker.run_once().outcome == "retried"
    clock.advance(1)
    result = worker.run_once()
    assert result.outcome == "dead_lettered"
    assert result.reason_code == "TASK_ATTEMPTS_EXHAUSTED"
    assert tasks.get_task(task.task_id, "tenant-a").task.attempt == 2
    assert queue.dead_letters[0].task_id == task.task_id


def test_worker_failure_rolls_back_result_before_dead_letter(
    ready_cv_json, ready_position_json, worker_auth_context
):
    def fail_commit() -> None:
        raise RuntimeError("success transaction failed")

    storage, tasks = _services(max_attempts=1, before_success_commit=fail_commit)
    queue = InMemoryTaskQueue()
    task = TaskSubmissionService(tasks, queue).submit(
        _payload(ready_cv_json, ready_position_json), "rollback-queue", "tenant-a"
    ).task
    worker = EvaluationTaskWorker(
        queue, tasks, worker_id="worker", auth_context=worker_auth_context
    )

    assert worker.run_once().outcome == "dead_lettered"
    assert storage.evaluations == {}
    failed = tasks.get_task(task.task_id, "tenant-a").task
    assert failed.status == "failed"
    assert failed.evaluation_id is None


def test_wrong_access_scope_message_is_dead_lettered_without_task_access(
    ready_cv_json, ready_position_json, worker_auth_context
):
    _, tasks = _services()
    queue = InMemoryTaskQueue()
    task = tasks.submit(
        _payload(ready_cv_json, ready_position_json),
        "scope-key",
        "tenant-a",
        execute_immediately=False,
    ).task
    queue.publish(
        TaskQueueMessage(
            message_id="malicious-scope-message",
            task_id=task.task_id,
            access_scope="tenant-b",
            version_signature=task.versions.signature,
            published_at=datetime.now(timezone.utc),
        )
    )

    result = EvaluationTaskWorker(
        queue, tasks, worker_id="worker", auth_context=worker_auth_context
    ).run_once()
    assert result.outcome == "dead_lettered"
    assert result.reason_code == "TASK_NOT_FOUND"
    assert tasks.get_task(task.task_id, "tenant-a").task.status == "pending"


class _UnavailableRedis:
    def xadd(self, *args, **kwargs):
        raise TimeoutError("redis timeout")


def test_redis_unavailable_returns_stable_submission_error(
    ready_cv_json, ready_position_json
):
    _, tasks = _services()
    queue = RedisTaskQueue(
        _UnavailableRedis(), queue_name="test-queue", visibility_timeout_seconds=30
    )
    result = TaskSubmissionService(tasks, queue).submit(
        _payload(ready_cv_json, ready_position_json), "redis-down", "tenant-a"
    )

    assert result.task.status == "pending"
    assert result.error_code == "TASK_QUEUE_TIMEOUT"
    assert result.task.evaluation_id is None


class _FakeRedisPipeline:
    def __init__(self, client):
        self.client = client

    def __getattr__(self, name):
        def call(*args, **kwargs):
            getattr(self.client, name)(*args, **kwargs)
            return self

        return call

    def execute(self):
        return []


class _FakeRedis:
    def __init__(self):
        self.streams = {}
        self.hashes = {}
        self.seen = set()
        self.sorted_sets = {}
        self.sequence = 0

    def xgroup_create(self, name, group, id, mkstream):
        self.streams.setdefault(name, [])

    def xadd(self, name, fields):
        self.sequence += 1
        stream_id = f"{self.sequence}-0"
        self.streams.setdefault(name, []).append((stream_id, dict(fields)))
        return stream_id

    def xautoclaim(self, *args, **kwargs):
        return ("0-0", [], [])

    def xreadgroup(self, group, worker, streams, count, block):
        name = next(iter(streams))
        for stream_id, fields in self.streams.get(name, []):
            if stream_id not in self.seen:
                self.seen.add(stream_id)
                return [(name, [(stream_id, fields)])]
        return []

    def xack(self, *args):
        return 1

    def xdel(self, name, stream_id):
        self.streams[name] = [item for item in self.streams[name] if item[0] != stream_id]
        return 1

    def xrange(self, name, min, max, count):
        return [item for item in self.streams.get(name, []) if item[0] == min][:count]

    def hsetnx(self, name, key, value):
        values = self.hashes.setdefault(name, {})
        if key in values:
            return 0
        values[key] = value
        return 1

    def hincrby(self, name, key, amount):
        values = self.hashes.setdefault(name, {})
        values[key] = int(values.get(key, 0)) + amount
        return values[key]

    def hdel(self, name, key):
        return int(self.hashes.setdefault(name, {}).pop(key, None) is not None)

    def zadd(self, name, values):
        self.sorted_sets.setdefault(name, {}).update(values)

    def zrangebyscore(self, name, minimum, maximum, start, num):
        values = self.sorted_sets.get(name, {})
        return [value for value, score in values.items() if minimum <= score <= maximum][
            start : start + num
        ]

    def zrem(self, name, value):
        self.sorted_sets.get(name, {}).pop(value, None)

    def pipeline(self, transaction):
        return _FakeRedisPipeline(self)


class _NoGroupOnceRedis(_FakeRedis):
    def __init__(self):
        super().__init__()
        self.group_creations = 0
        self.raise_nogroup = True

    def xgroup_create(self, name, group, id, mkstream):
        self.group_creations += 1
        super().xgroup_create(name, group, id, mkstream)

    def xautoclaim(self, *args, **kwargs):
        if self.raise_nogroup:
            self.raise_nogroup = False
            raise RuntimeError("NOGROUP No such key or consumer group")
        return super().xautoclaim(*args, **kwargs)


class _NoGroupSettlementRedis(_FakeRedis):
    def __init__(self):
        super().__init__()
        self.group_creations = 0
        self.raise_on_ack = True
        self.return_zero_after_rebuild = False

    def xgroup_create(self, name, group, id, mkstream):
        self.group_creations += 1
        super().xgroup_create(name, group, id, mkstream)

    def xack(self, *args):
        if self.raise_on_ack:
            self.raise_on_ack = False
            self.return_zero_after_rebuild = True
            raise RuntimeError("NOGROUP No such key or consumer group")
        if self.return_zero_after_rebuild:
            self.return_zero_after_rebuild = False
            return 0
        return super().xack(*args)


def test_redis_adapter_publish_retry_acknowledge_and_dead_letter():
    client = _FakeRedis()
    queue = RedisTaskQueue(
        client, queue_name="redis-adapter-test", visibility_timeout_seconds=30
    )
    message = TaskQueueMessage(
        message_id=task_message_id("task-redis", "versions-v1"),
        task_id="task-redis",
        access_scope="tenant-a",
        version_signature="versions-v1",
        published_at=datetime.now(timezone.utc),
    )

    queue.publish(message)
    first = queue.consume("worker-a")
    assert first.message == message
    queue.retry(first, delay_seconds=0, reason_code="TRANSIENT_FAILURE")
    second = queue.consume("worker-b")
    assert second.message == message
    assert second.delivery_count == 2
    queue.dead_letter(second, reason_code="TASK_ATTEMPTS_EXHAUSTED")
    dead = [json.loads(value) for value in client.hashes[queue.dead_letter_queue_name].values()]
    assert dead[0]["reason_code"] == "TASK_ATTEMPTS_EXHAUSTED"
    assert "payload" not in dead[0]

    queue.publish(message)
    final = queue.consume("worker-c")
    queue.acknowledge(final)
    assert queue.consume("worker-c") is None


@pytest.mark.parametrize(
    ("fields", "reason_code"),
    [
        (
            {"payload": "{malformed alice@example.com", "delivery_count": "0"},
            "QUEUE_MESSAGE_JSON_INVALID",
        ),
        ({"delivery_count": "0"}, "QUEUE_MESSAGE_FIELDS_INVALID"),
        (
            {"payload": '{"task_id":7}', "delivery_count": "0"},
            "QUEUE_MESSAGE_CONTRACT_INVALID",
        ),
    ],
)
def test_redis_poison_is_sanitized_to_dlq_and_does_not_block_next_message(
    fields, reason_code
):
    client = _FakeRedis()
    queue = RedisTaskQueue(
        client, queue_name="redis-poison-test", visibility_timeout_seconds=30
    )
    client.xadd(queue.queue_name, fields)
    valid = TaskQueueMessage(
        message_id=task_message_id("task-after-poison", "versions-v1"),
        task_id="task-after-poison",
        access_scope="tenant-a",
        version_signature="versions-v1",
        published_at=datetime.now(timezone.utc),
    )
    queue.publish(valid)

    delivery = queue.consume("worker-after-poison")

    assert delivery.message == valid
    dead_fields = json.loads(
        next(iter(client.hashes[queue.dead_letter_queue_name].values()))
    )
    assert dead_fields["reason_code"] == reason_code
    assert "payload" not in dead_fields
    assert "malformed" not in str(dead_fields)


def test_redis_wrong_message_identity_is_sanitized_to_dlq():
    client = _FakeRedis()
    queue = RedisTaskQueue(
        client, queue_name="redis-identity-test", visibility_timeout_seconds=30
    )
    poison = TaskQueueMessage(
        message_id="message_forged",
        task_id="task-identity",
        access_scope="tenant-a",
        version_signature="versions-v1",
        published_at=datetime.now(timezone.utc),
    )
    client.xadd(queue.queue_name, queue._stream_fields(poison, 0))

    assert queue.consume("worker") is None
    dead_fields = json.loads(
        next(iter(client.hashes[queue.dead_letter_queue_name].values()))
    )
    assert dead_fields["reason_code"] == "TASK_MESSAGE_IDENTITY_MISMATCH"
    assert dead_fields["delivery_count"] == 1
    assert "payload" not in dead_fields


def test_redis_nogroup_invalidates_cache_and_recovers_without_restart():
    client = _NoGroupOnceRedis()
    queue = RedisTaskQueue(
        client, queue_name="redis-nogroup-test", visibility_timeout_seconds=30
    )
    message = TaskQueueMessage(
        message_id=task_message_id("task-nogroup", "versions-v1"),
        task_id="task-nogroup",
        access_scope="tenant-a",
        version_signature="versions-v1",
        published_at=datetime.now(timezone.utc),
    )
    queue.publish(message)

    delivery = queue.consume("same-process-worker")

    assert delivery.message == message
    assert client.group_creations == 2


@pytest.mark.parametrize("operation", ["acknowledge", "retry", "dead_letter"])
def test_redis_settlement_nogroup_recovers_idempotently(operation):
    client = _NoGroupSettlementRedis()
    queue = RedisTaskQueue(
        client, queue_name=f"settlement-{operation}", visibility_timeout_seconds=30
    )
    message = TaskQueueMessage(
        message_id=task_message_id(f"task-{operation}", "versions-v1"),
        task_id=f"task-{operation}",
        access_scope="tenant-a",
        version_signature="versions-v1",
        published_at=datetime.now(timezone.utc),
    )
    queue.publish(message)
    delivery = queue.consume("worker")

    if operation == "acknowledge":
        queue.acknowledge(delivery)
    elif operation == "retry":
        queue.retry(delivery, delay_seconds=60, reason_code="TRANSIENT_FAILURE")
        assert len(client.sorted_sets[queue.retry_queue_name]) == 1
        queue.retry(delivery, delay_seconds=60, reason_code="TRANSIENT_FAILURE")
        assert len(client.sorted_sets[queue.retry_queue_name]) == 1
    else:
        queue.dead_letter(delivery, reason_code="TASK_ATTEMPTS_EXHAUSTED")
        assert len(client.hashes[queue.dead_letter_queue_name]) == 1
        # Re-entering after an interrupted caller is idempotent at the DLQ boundary.
        queue.dead_letter(delivery, reason_code="TASK_ATTEMPTS_EXHAUSTED")
        assert len(client.hashes[queue.dead_letter_queue_name]) == 1

    assert client.group_creations == 2
    assert not client.streams[queue.queue_name]


def test_redis_deleted_stream_and_group_continue_in_same_process():
    client = _NoGroupOnceRedis()
    client.raise_nogroup = False
    queue = RedisTaskQueue(
        client, queue_name="rebuild-stream", visibility_timeout_seconds=30
    )
    first = TaskQueueMessage(
        message_id=task_message_id("task-first", "versions-v1"),
        task_id="task-first",
        access_scope="tenant-a",
        version_signature="versions-v1",
        published_at=datetime.now(timezone.utc),
    )
    queue.publish(first)
    queue.acknowledge(queue.consume("worker"))

    client.streams.pop(queue.queue_name, None)
    client.seen.clear()
    client.raise_nogroup = True
    second = first.model_copy(
        update={
            "message_id": task_message_id("task-second", "versions-v1"),
            "task_id": "task-second",
        }
    )
    queue.publish(second)

    assert queue.consume("worker").message == second
    assert client.group_creations >= 2


def test_queue_configuration_defaults_to_memory_and_validates_provider():
    selected = build_task_queue({})
    assert selected.provider == "memory"
    assert isinstance(selected.queue, InMemoryTaskQueue)
    assert selected.visibility_timeout_seconds == 60
    assert selected.retry_interval_seconds == 5
    with pytest.raises(ValueError, match="memory or redis"):
        build_task_queue({"MATCHING_QUEUE_PROVIDER": "unknown"})
    with pytest.raises(ValueError, match="MATCHING_REDIS_URL"):
        build_task_queue({"MATCHING_QUEUE_PROVIDER": "redis"})
