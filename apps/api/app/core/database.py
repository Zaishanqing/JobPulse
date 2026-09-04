from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker


Base = declarative_base()


@dataclass(frozen=True)
class Database:
    engine: Engine
    session_factory: sessionmaker[Session]

    def dispose(self) -> None:
        self.engine.dispose()


def create_database(database_url: str) -> Database:
    engine = create_engine(database_url, pool_pre_ping=True)

    return Database(
        engine=engine,
        session_factory=sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=engine,
        ),
    )
