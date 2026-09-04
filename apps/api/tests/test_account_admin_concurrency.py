"""SQLite concurrency tests for the last-active-admin invariant.

Each scenario uses a pytest-owned temporary directory, two independent
``NullPool`` engines, and two DBAPI connections to one file-backed SQLite
database. A test-only UoW barrier makes both workers reach the real lock gate
before either can issue ``BEGIN IMMEDIATE``.
"""

from __future__ import annotations

import threading
from pathlib import Path

from sqlalchemy import URL, create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.contexts.access import (
    AccountActiveChangeCommand,
    AccountRoleChangeCommand,
    InvalidAccountChange,
    ManageAccount,
)
from app.domain.accounts import AccountActor
from app.infrastructure.accounts import SqlAlchemyAccountUnitOfWork
from app.models.user import Base, User
from app.services.auth_service import hash_password


class BarrierAccountUnitOfWork(SqlAlchemyAccountUnitOfWork):
    """Synchronise workers immediately before the production lock call."""

    def __init__(self, session_factory, *, lock_barrier: threading.Barrier):
        super().__init__(session_factory)
        self._lock_barrier = lock_barrier

    def acquire_account_administration_lock(self) -> None:
        self._lock_barrier.wait(timeout=15)
        super().acquire_account_administration_lock()


def _build_engine(database_url: URL):
    return create_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 5},
        poolclass=NullPool,
    )


def _seed_users(session, *users: tuple[str, str, bool]) -> None:
    for user_id, role, is_active in users:
        session.add(
            User(
                id=user_id,
                username=user_id,
                role=role,
                is_active=is_active,
                hashed_password=hash_password("password123"),
            )
        )
    session.commit()


def _active_admin_count(session) -> int:
    return (
        session.query(User)
        .filter(User.role == "admin", User.is_active.is_(True))
        .count()
    )


def _run_concurrent_admin_mutation(tmp_path: Path, *, scenario: str) -> None:
    db_path = tmp_path / f"account_admin_{scenario}.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    assert db_path.parent.exists()
    assert db_path.parent.is_dir()
    database_url = URL.create("sqlite+pysqlite", database=str(db_path.resolve()))

    engine_a = None
    engine_b = None
    verify_engine = None
    thread_a = None
    thread_b = None
    results: dict[str, tuple[str, str]] = {}
    connection_ids: dict[str, int] = {}

    try:
        engine_a = _build_engine(database_url)
        engine_b = _build_engine(database_url)

        @event.listens_for(engine_a, "connect")
        def _record_connection_a(dbapi_connection, _connection_record) -> None:
            connection_ids["A"] = id(dbapi_connection)

        @event.listens_for(engine_b, "connect")
        def _record_connection_b(dbapi_connection, _connection_record) -> None:
            connection_ids["B"] = id(dbapi_connection)

        Base.metadata.create_all(engine_a)
        session_a = sessionmaker(bind=engine_a)
        session_b = sessionmaker(bind=engine_b)
        with session_a() as seed_session:
            _seed_users(
                seed_session,
                ("admin_a", "admin", True),
                ("admin_b", "admin", True),
            )

        lock_barrier = threading.Barrier(2, timeout=15)

        def _mutate(name: str, actor_id: str, target_id: str, session_factory) -> None:
            manager = ManageAccount(
                lambda: BarrierAccountUnitOfWork(
                    session_factory,
                    lock_barrier=lock_barrier,
                )
            )
            actor = AccountActor(actor_id, "admin")
            try:
                if scenario == "demotion":
                    manager.change_role(
                        actor,
                        AccountRoleChangeCommand(target_id, "personal_user"),
                    )
                else:
                    manager.change_active(actor, AccountActiveChangeCommand(target_id, False))
            except InvalidAccountChange as exc:
                results[name] = ("rejected", str(exc))
            except Exception as exc:
                results[name] = ("error", f"{type(exc).__name__}: {exc}")
            else:
                results[name] = ("success", "ok")

        thread_a = threading.Thread(
            target=_mutate,
            args=("A", "admin_a", "admin_b", session_a),
        )
        thread_b = threading.Thread(
            target=_mutate,
            args=("B", "admin_b", "admin_a", session_b),
        )
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=30)
        thread_b.join(timeout=30)
        assert not thread_a.is_alive(), "Thread A hung"
        assert not thread_b.is_alive(), "Thread B hung"

        assert connection_ids.get("A") is not None
        assert connection_ids.get("B") is not None
        assert connection_ids["A"] != connection_ids["B"]
        assert set(results) == {"A", "B"}, results
        successes = [result for result in results.values() if result[0] == "success"]
        rejections = [result for result in results.values() if result[0] == "rejected"]
        assert len(successes) == 1, results
        assert len(rejections) == 1, results
        assert "last active administrator" in rejections[0][1].lower()

        verify_engine = _build_engine(database_url)
        verify_session = sessionmaker(bind=verify_engine)
        with verify_session() as session:
            assert _active_admin_count(session) == 1
    finally:
        for thread in (thread_a, thread_b):
            if thread is not None:
                thread.join(timeout=30)
                assert not thread.is_alive(), "Worker thread did not stop"
        for engine in (verify_engine, engine_a, engine_b):
            if engine is not None:
                engine.dispose()

    probe_engine = _build_engine(database_url)
    try:
        with probe_engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        probe_engine.dispose()


def test_concurrent_mutual_demotion(tmp_path: Path) -> None:
    _run_concurrent_admin_mutation(tmp_path, scenario="demotion")


def test_concurrent_mutual_disable(tmp_path: Path) -> None:
    _run_concurrent_admin_mutation(tmp_path, scenario="disable")
