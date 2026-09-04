from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.bootstrap.settings import Settings


class Base(DeclarativeBase):
    pass


@dataclass(frozen=True)
class Database:
    engine: Engine
    session_factory: sessionmaker[Session]


def create_database(config: Settings) -> Database:
    engine = create_engine(config.DATABASE_URL, pool_pre_ping=True)
    return Database(
        engine=engine,
        session_factory=sessionmaker(
            bind=engine,
            autoflush=False,
            expire_on_commit=False,
        ),
    )
