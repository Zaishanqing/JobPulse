from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings

class Base(DeclarativeBase):
    pass

def make_engine(url: str):
    target = url
    options = {
        "connect_args": {"check_same_thread": False} if target.startswith("sqlite") else {},
        "pool_pre_ping": True,
    }
    if target.startswith("postgresql"):
        options["isolation_level"] = "READ COMMITTED"
    result = create_engine(target, **options)
    if target.startswith("sqlite"):
        @event.listens_for(result, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return result

@dataclass(frozen=True)
class Database:
    engine: Engine
    session_factory: sessionmaker[Session]


def create_database(settings: Settings) -> Database:
    engine = make_engine(settings.database_url)
    return Database(
        engine=engine,
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
    )


def get_db(request: Request):
    database: Database = request.app.state.database
    db = database.session_factory()
    try:
        yield db
    finally:
        db.close()
