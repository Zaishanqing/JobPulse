"""Environment-driven persistence selection; memory remains the safe default."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from app.infrastructure.memory_repositories import InMemoryPersistence
from app.infrastructure.sqlalchemy_repositories import SQLAlchemyPersistence
from app.ports.repositories import UnitOfWorkFactory


@dataclass(frozen=True)
class PersistenceSelection:
    provider: str
    unit_of_work: UnitOfWorkFactory
    resource: InMemoryPersistence | SQLAlchemyPersistence


def build_persistence(
    environment: Mapping[str, str] | None = None,
) -> PersistenceSelection:
    env = environment if environment is not None else os.environ
    provider = env.get("MATCHING_PERSISTENCE_PROVIDER", "memory").strip().lower()
    if provider == "memory":
        persistence = InMemoryPersistence()
        return PersistenceSelection(provider, persistence.unit_of_work, persistence)
    if provider != "postgres":
        raise ValueError("MATCHING_PERSISTENCE_PROVIDER must be memory or postgres")
    database_url = env.get("MATCHING_DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError("MATCHING_DATABASE_URL is required for postgres persistence")
    is_postgres = database_url.startswith(("postgresql://", "postgresql+psycopg://"))
    sqlite_test_mode = (
        database_url.startswith("sqlite")
        and env.get("MATCHING_PERSISTENCE_SQLITE_TEST_MODE", "false").lower() == "true"
    )
    if not is_postgres and not sqlite_test_mode:
        raise ValueError("postgres persistence requires PostgreSQL or explicit SQLite test mode")
    options: dict[str, object] = {"pool_pre_ping": True}
    if sqlite_test_mode:
        options["connect_args"] = {"check_same_thread": False}
    else:
        connect_timeout = _positive_int(
            env.get("MATCHING_DATABASE_CONNECT_TIMEOUT_SECONDS", "5"),
            "MATCHING_DATABASE_CONNECT_TIMEOUT_SECONDS",
        )
        options.update(
            {
                "connect_args": {"connect_timeout": connect_timeout},
                "pool_timeout": _positive_float(
                    env.get("MATCHING_DATABASE_POOL_TIMEOUT_SECONDS", "5"),
                    "MATCHING_DATABASE_POOL_TIMEOUT_SECONDS",
                ),
                "pool_size": _positive_int(
                    env.get("MATCHING_DATABASE_POOL_SIZE", "5"),
                    "MATCHING_DATABASE_POOL_SIZE",
                ),
                "max_overflow": _non_negative_int(
                    env.get("MATCHING_DATABASE_MAX_OVERFLOW", "5"),
                    "MATCHING_DATABASE_MAX_OVERFLOW",
                ),
            }
        )
    persistence = SQLAlchemyPersistence.from_url(database_url, **options)
    return PersistenceSelection(provider, persistence.unit_of_work, persistence)


def _positive_float(raw: str, name: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_int(raw: str, name: str) -> int:
    value = _non_negative_int(raw, name)
    if value == 0:
        raise ValueError(f"{name} must be positive")
    return value


def _non_negative_int(raw: str, name: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return value
