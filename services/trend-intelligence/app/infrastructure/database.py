from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


@dataclass(frozen=True)
class Database:
    engine: Engine
    sessions: sessionmaker[Session]


def create_database(url: str) -> Database:
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("Trend Intelligence requires PostgreSQL with the psycopg driver")
    engine = create_engine(url, pool_pre_ping=True)
    return Database(engine, sessionmaker(engine, expire_on_commit=False, autoflush=False))
