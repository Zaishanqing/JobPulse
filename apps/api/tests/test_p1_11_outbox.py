from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
import ast
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import tomllib

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.config import Settings
from app.domain.json_types import freeze_json_object
from app.infrastructure.outbox import (
    SqlAlchemyOutboxDispatcher,
    SqlAlchemyOutboxRepository,
)
from app.infrastructure.knowledge_graph import (
    KnowledgeGraphPublishedJDSyncHandler,
    build_knowledge_graph_outbox_handlers,
    published_jd_fact_identity,
)
from app.integration_events import (
    DispatchResult,
    IdempotencyKey,
    IntegrationEvent,
    OutboxMessageDraft,
    OutboxStatus,
)
from app.models.outbox_message import OutboxMessage
from app.models.jd import JobDescription
from app.models.jd_parse_result import JDParseResult
from app.workers import outbox as outbox_worker


def _factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'outbox.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _draft(key: str = "fact:1:hash") -> OutboxMessageDraft:
    now = datetime.now(timezone.utc)
    return OutboxMessageDraft(
        IntegrationEvent(
            "event-1", "test.event", "aggregate-1", freeze_json_object({"value": 1}), now, "trace-1"
        ),
        IdempotencyKey(key),
    )


def _seed_published_jd(factory):
    with factory() as session:
        parsed = JDParseResult(
            id="FACT1",
            jd_id="JD1",
            extraction_result={"title": "Engineer"},
            normalized_result={"skills": ["Python"]},
            workflow_status="published",
        )
        session.add_all(
            [
                JobDescription(
                    id="JD1", source_type="manual", title="Engineer", raw_text="text"
                ),
                parsed,
            ]
        )
        session.commit()
        session.refresh(parsed)
        return published_jd_fact_identity(parsed)


def _kg_draft(identity, *, key: str | None = None, **overrides) -> OutboxMessageDraft:
    payload = {
        "document_id": "JD1",
        "actor_id": "admin-1",
        "actor_role": "admin",
        "source_fact_id": identity.source_fact_id,
        "source_fact_version": identity.source_fact_version,
    }
    payload.update(overrides)
    return OutboxMessageDraft(
        IntegrationEvent(
            "kg-event-1",
            "knowledge_graph.published_jd.sync",
            "JD1",
            freeze_json_object(payload),
            datetime.now(timezone.utc),
        ),
        IdempotencyKey(key or identity.idempotency_key),
    )


class Handler:
    def __init__(self, result: DispatchResult) -> None:
        self.result = result
        self.keys: list[str] = []

    def handle(self, event, idempotency_key):
        self.keys.append(idempotency_key.value)
        return self.result


def test_business_rollback_also_rolls_back_outbox(tmp_path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        SqlAlchemyOutboxRepository(session).add(_draft())
        session.rollback()
    with factory() as session:
        assert session.query(OutboxMessage).count() == 0


def test_pending_message_survives_restart_and_is_delivered_with_same_key(tmp_path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        SqlAlchemyOutboxRepository(session).add(_draft())
        session.commit()
    handler = Handler(DispatchResult(True))
    result = SqlAlchemyOutboxDispatcher(factory, {"test.event": handler}).dispatch_one(
        "worker-1", datetime.now(timezone.utc)
    )
    assert result == DispatchResult(True)
    assert handler.keys == ["fact:1:hash"]
    with factory() as session:
        row = session.query(OutboxMessage).one()
        assert row.status == OutboxStatus.DELIVERED.value
        assert row.attempts == 1


def test_two_workers_cannot_claim_the_same_message(tmp_path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        SqlAlchemyOutboxRepository(session).add(_draft())
        session.commit()
    barrier = Barrier(2)
    now = datetime.now(timezone.utc)

    def claim(worker: str):
        with factory() as session:
            barrier.wait()
            message = SqlAlchemyOutboxRepository(session).claim(worker, now)
            session.commit()
            return message

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, ("one", "two")))
    assert sum(message is not None for message in claimed) == 1


def test_retry_and_dead_letter_are_persisted(tmp_path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        message = SqlAlchemyOutboxRepository(session, max_attempts=2).add(_draft())
        session.commit()
    now = datetime.now(timezone.utc)
    with factory() as session:
        repository = SqlAlchemyOutboxRepository(session, max_attempts=2)
        claimed = repository.claim("worker", now)
        repository.complete(claimed.message_id, "worker", DispatchResult(False, True, "temporary"))
        session.commit()
    with factory() as session:
        row = session.get(OutboxMessage, message.message_id)
        assert row.status == OutboxStatus.RETRYABLE.value
        row.next_attempt_at = now - timedelta(seconds=1)
        session.commit()
    with factory() as session:
        repository = SqlAlchemyOutboxRepository(session, max_attempts=2)
        claimed = repository.claim("worker", now)
        repository.complete(claimed.message_id, "worker", DispatchResult(False, True, "again"))
        session.commit()
    with factory() as session:
        assert (
            session.get(OutboxMessage, message.message_id).status == OutboxStatus.DEAD_LETTER.value
        )


def test_complete_returns_false_for_an_expired_lease(tmp_path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        message = SqlAlchemyOutboxRepository(session, lease_seconds=60).add(_draft())
        session.commit()
    now = datetime.now(timezone.utc)
    with factory() as session:
        row = session.get(OutboxMessage, message.message_id)
        row.status = OutboxStatus.CLAIMED.value
        row.lease_owner = "worker-a"
        row.lease_until = now - timedelta(seconds=1)
        session.commit()
    with factory() as session:
        completed = SqlAlchemyOutboxRepository(session).complete(
            message.message_id, "worker-a", DispatchResult(True)
        )
        assert completed is False


def test_dispatcher_renews_lease_during_long_handler(tmp_path, monkeypatch) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        message = SqlAlchemyOutboxRepository(session).add(_draft())
        session.commit()

    started = Event()
    release = Event()
    renewals: list[str] = []
    original_renew = SqlAlchemyOutboxRepository.renew_lease

    def renew(self, message_id, worker_id, now):
        renewals.append(message_id)
        return original_renew(self, message_id, worker_id, now)

    monkeypatch.setattr(SqlAlchemyOutboxRepository, "renew_lease", renew)

    class SlowHandler:
        def handle(self, _event, _key):
            started.set()
            release.wait(2)
            return DispatchResult(True)

    dispatcher = SqlAlchemyOutboxDispatcher(
        factory, {"test.event": SlowHandler()}, lease_seconds=1
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dispatcher.dispatch_one, "worker-a", datetime.now(timezone.utc))
        assert started.wait(1)
        assert _wait_for_renewal(renewals)
        release.set()
        assert future.result() == DispatchResult(True)

    with factory() as session:
        assert session.get(OutboxMessage, message.message_id).status == OutboxStatus.DELIVERED.value


def _wait_for_renewal(renewals: list[str]) -> bool:
    deadline = datetime.now(timezone.utc) + timedelta(seconds=1)
    while not renewals and datetime.now(timezone.utc) < deadline:
        Event().wait(0.02)
    return bool(renewals)


def test_dispatcher_returns_lost_lease_instead_of_raising(tmp_path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        message = SqlAlchemyOutboxRepository(session).add(_draft())
        session.commit()

    class StealingHandler:
        def handle(self, _event, _key):
            with factory() as session:
                row = session.get(OutboxMessage, message.message_id)
                row.lease_owner = "worker-b"
                row.lease_until = datetime.now(timezone.utc) + timedelta(seconds=60)
                session.commit()
            return DispatchResult(True)

    result = SqlAlchemyOutboxDispatcher(
        factory, {"test.event": StealingHandler()}
    ).dispatch_one("worker-a", datetime.now(timezone.utc))

    assert result == DispatchResult(False, True, "LOST_LEASE")


def test_permanent_failure_enters_dead_letter_without_retry(tmp_path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        message = SqlAlchemyOutboxRepository(session).add(_draft())
        session.commit()
    now = datetime.now(timezone.utc)
    with factory() as session:
        repository = SqlAlchemyOutboxRepository(session)
        claimed = repository.claim("worker", now)
        repository.complete(
            claimed.message_id,
            "worker",
            DispatchResult(False, False, "permanent contract error"),
        )
        session.commit()
    with factory() as session:
        row = session.get(OutboxMessage, message.message_id)
        assert row.status == OutboxStatus.DEAD_LETTER.value
        assert row.attempts == 1
        assert row.last_error == "permanent contract error"


def test_registry_exposes_the_real_kg_published_jd_handler(tmp_path) -> None:
    factory = _factory(tmp_path)
    registry = build_knowledge_graph_outbox_handlers(factory, object(), enabled=True)

    handler = registry["knowledge_graph.published_jd.sync"]

    assert isinstance(handler, KnowledgeGraphPublishedJDSyncHandler)


def test_unregistered_event_enters_dead_letter_with_explicit_error(tmp_path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        SqlAlchemyOutboxRepository(session).add(_draft())
        session.commit()

    result = SqlAlchemyOutboxDispatcher(factory, {}).dispatch_one(
        "worker-1", datetime.now(timezone.utc)
    )

    assert result is not None
    assert result.delivered is False
    assert result.retryable is False
    assert "No outbox handler registered" in (result.error or "")
    with factory() as session:
        row = session.query(OutboxMessage).one()
        assert row.status == OutboxStatus.DEAD_LETTER.value
        assert row.lease_owner is None


def test_pending_message_is_dispatched_by_the_real_kg_handler(tmp_path, monkeypatch) -> None:
    factory = _factory(tmp_path)
    identity = _seed_published_jd(factory)
    calls: list[tuple[str, str]] = []

    def sync_jd(adapter, document_id, actor):
        calls.append((document_id, actor.account_id))

    monkeypatch.setattr("app.infrastructure.knowledge_graph.KnowledgeGraphAdapter.sync_jd", sync_jd)
    draft = _kg_draft(identity)
    with factory() as session:
        SqlAlchemyOutboxRepository(session).add(draft)
        session.commit()
    now = datetime.now(timezone.utc)

    dispatcher = SqlAlchemyOutboxDispatcher(
        factory, build_knowledge_graph_outbox_handlers(factory, object(), enabled=True)
    )
    assert dispatcher.dispatch_one("worker-1", now) == DispatchResult(True)
    assert calls == [("JD1", "admin-1")]
    with factory() as session:
        assert session.query(OutboxMessage).one().status == OutboxStatus.DELIVERED.value


@pytest.mark.parametrize(
    ("payload_override", "key_override"),
    [
        ({"source_fact_version": "stale-version"}, None),
        ({"source_fact_id": "stale-fact"}, None),
        ({}, "stale-idempotency-key"),
    ],
)
def test_stale_kg_identity_is_dead_lettered_without_remote_call(
    tmp_path, monkeypatch, payload_override, key_override
) -> None:
    factory = _factory(tmp_path)
    identity = _seed_published_jd(factory)
    calls: list[str] = []
    monkeypatch.setattr(
        "app.infrastructure.knowledge_graph.KnowledgeGraphAdapter.sync_jd",
        lambda *_args: calls.append("remote"),
    )
    with factory() as session:
        SqlAlchemyOutboxRepository(session).add(
            _kg_draft(identity, key=key_override, **payload_override)
        )
        session.commit()

    result = SqlAlchemyOutboxDispatcher(
        factory, build_knowledge_graph_outbox_handlers(factory, object(), enabled=True)
    ).dispatch_one("worker-1", datetime.now(timezone.utc))

    assert result == DispatchResult(False, False, "published_jd_sync_event_stale")
    assert calls == []
    with factory() as session:
        message = session.query(OutboxMessage).one()
        assert message.status == OutboxStatus.DEAD_LETTER.value
        assert message.last_error == "published_jd_sync_event_stale"


def test_h1_event_is_stale_after_fact_changes_to_h2(tmp_path, monkeypatch) -> None:
    factory = _factory(tmp_path)
    h1 = _seed_published_jd(factory)
    with factory() as session:
        SqlAlchemyOutboxRepository(session).add(_kg_draft(h1))
        parsed = session.query(JDParseResult).filter_by(jd_id="JD1").one()
        parsed.normalized_result = {"skills": ["Python", "SQL"]}
        parsed.updated_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        session.commit()
    calls: list[str] = []
    monkeypatch.setattr(
        "app.infrastructure.knowledge_graph.KnowledgeGraphAdapter.sync_jd",
        lambda *_args: calls.append("remote"),
    )

    result = SqlAlchemyOutboxDispatcher(
        factory, build_knowledge_graph_outbox_handlers(factory, object(), enabled=True)
    ).dispatch_one("worker-1", datetime.now(timezone.utc))

    assert result == DispatchResult(False, False, "published_jd_sync_event_stale")
    assert calls == []


def test_outbox_worker_console_script_and_settings_validation() -> None:
    project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text("utf-8"))
    assert project["project"]["scripts"]["jobgraph-outbox-worker"] == "app.workers.outbox:main"
    with pytest.raises(ValidationError):
        Settings(OUTBOX_IDLE_SLEEP_SECONDS=0)
    with pytest.raises(ValidationError):
        Settings(OUTBOX_DISPATCH_ONCE="not-a-boolean")
    with pytest.raises(ValidationError, match="KG outbox lease"):
        Settings(
            KG_OUTBOX_POLL_INTERVAL_SECONDS=10,
            KG_OUTBOX_LEASE_SECONDS=5,
        )


class _WorkerDispatcher:
    handlers = {"test.event": object()}

    def __init__(self, result=None) -> None:
        self.result = result
        self.calls: list[str] = []

    def dispatch_one(self, worker_id, _now):
        self.calls.append(worker_id)
        return self.result


def _worker_runtime(dispatcher):
    database = SimpleNamespace(
        engine=SimpleNamespace(url=make_url("postgresql://worker:secret@db.example/jobgraph"))
    )
    return SimpleNamespace(database=database, dispatcher=dispatcher)


def _disable_real_signal_handlers(monkeypatch) -> None:
    monkeypatch.setattr(outbox_worker.signal, "getsignal", lambda _signal: None)
    monkeypatch.setattr(outbox_worker.signal, "signal", lambda *_args: None)


def test_outbox_worker_dispatch_once_logs_safe_runtime_metadata(monkeypatch, caplog) -> None:
    dispatcher = _WorkerDispatcher()
    _disable_real_signal_handlers(monkeypatch)

    with caplog.at_level("INFO"):
        outbox_worker.run_worker(
            _worker_runtime(dispatcher), worker_id="worker-test", dispatch_once=True
        )

    assert dispatcher.calls == ["worker-test"]
    record = next(item for item in caplog.records if item.message == "outbox_worker_started")
    assert record.worker_id == "worker-test"
    assert record.registered_event_types == ["test.event"]
    assert "secret" not in record.database_target


def test_outbox_worker_uses_bounded_concurrency(monkeypatch) -> None:
    dispatcher = _WorkerDispatcher()
    _disable_real_signal_handlers(monkeypatch)

    outbox_worker.run_worker(
        _worker_runtime(dispatcher),
        worker_id="bounded-worker",
        concurrency=3,
        dispatch_once=True,
    )

    assert sorted(dispatcher.calls) == [
        "bounded-worker:0",
        "bounded-worker:1",
        "bounded-worker:2",
    ]


def test_idle_worker_sleeps_and_stops_without_busy_loop(monkeypatch) -> None:
    dispatcher = _WorkerDispatcher()
    stopped = Event()
    sleeps: list[float] = []
    _disable_real_signal_handlers(monkeypatch)

    def stop_after_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        stopped.set()

    monkeypatch.setattr(outbox_worker, "sleep", stop_after_sleep)
    outbox_worker.run_worker(
        _worker_runtime(dispatcher),
        stop=stopped,
        worker_id="idle-worker",
        idle_sleep_seconds=0.5,
    )

    assert dispatcher.calls == ["idle-worker"]
    assert sleeps == [0.5]


def test_signal_requests_graceful_worker_shutdown(monkeypatch) -> None:
    captured = {}
    sleeps: list[float] = []

    def register(signum, handler):
        captured[signum] = handler

    class SignallingDispatcher(_WorkerDispatcher):
        def dispatch_one(self, worker_id, now):
            result = super().dispatch_one(worker_id, now)
            captured[outbox_worker.signal.SIGINT](outbox_worker.signal.SIGINT, None)
            return result

    monkeypatch.setattr(outbox_worker.signal, "getsignal", lambda _signal: None)
    monkeypatch.setattr(outbox_worker.signal, "signal", register)
    monkeypatch.setattr(outbox_worker, "sleep", lambda seconds: sleeps.append(seconds))
    dispatcher = SignallingDispatcher()

    outbox_worker.run_worker(
        _worker_runtime(dispatcher), worker_id="signal-worker", idle_sleep_seconds=0.2
    )

    assert dispatcher.calls == ["signal-worker"]
    assert sleeps == []


def test_unexpected_handler_exception_is_retryable_and_sanitized(tmp_path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        SqlAlchemyOutboxRepository(session).add(_draft())
        session.commit()

    class ExplodingHandler:
        def handle(self, _event, _key):
            raise RuntimeError("database-password=do-not-log")

    result = SqlAlchemyOutboxDispatcher(
        factory, {"test.event": ExplodingHandler()}
    ).dispatch_one("worker", datetime.now(timezone.utc))

    assert result == DispatchResult(False, True, "handler_exception:RuntimeError")
    with factory() as session:
        row = session.query(OutboxMessage).one()
        assert row.status == OutboxStatus.RETRYABLE.value
        assert "do-not-log" not in (row.last_error or "")


def test_worker_main_always_closes_runtime(monkeypatch) -> None:
    closed: list[bool] = []
    runtime = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(outbox_worker, "build_worker_runtime", lambda: runtime)
    monkeypatch.setattr(outbox_worker, "run_worker", lambda *_args, **_kwargs: None)

    outbox_worker.main()

    assert closed == [True]


def test_kg_persistence_and_remote_adapters_have_separate_resources() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "infrastructure"
    repository_tree = ast.parse((root / "knowledge_graph_repositories.py").read_text("utf-8"))
    remote_tree = ast.parse((root / "knowledge_graph_remote.py").read_text("utf-8"))
    adapter_tree = ast.parse((root / "knowledge_graph_adapter.py").read_text("utf-8"))

    repository_imports = {
        node.module
        for node in ast.walk(repository_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    remote_imports = {
        node.module
        for node in ast.walk(remote_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(name.startswith("app.integrations") for name in repository_imports)
    assert not any(name.startswith(("sqlalchemy", "app.models")) for name in remote_imports)

    # Transaction completion belongs to the coordinating UoW/factory.  The
    # adapters and repositories may flush, but cannot independently commit.
    for tree in (repository_tree, remote_tree, adapter_tree):
        transaction_calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"commit", "rollback"}
        }
        assert transaction_calls == set()
