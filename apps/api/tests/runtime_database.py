"""Test-owned database handles for direct persistence assertions."""

from sqlalchemy import text

from app.core.config import settings
from app.core.database import Base, create_database
import app.models  # noqa: F401  (register every model before create_all)

__all__ = ["Base", "SessionLocal", "engine", "reset_database_data"]


_database = create_database(settings.DATABASE_URL)
engine = _database.engine
SessionLocal = _database.session_factory

# conftest gives every pytest run a fresh database file (uuid4 root dir), so the
# schema only needs to be created once per session instead of per test.
Base.metadata.create_all(bind=engine)


def reset_database_data() -> None:
    """Clear all rows without dropping tables.

    Recreating 60 tables per test costs ~1.6s per reset; deleting rows is
    milliseconds and keeps the schema (and its indexes) intact between tests.
    """
    tables = [table.name for table in Base.metadata.sorted_tables]
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        for name in reversed(tables):
            connection.exec_driver_sql(f"DELETE FROM {name}")
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")


_drop_all = Base.metadata.drop_all


def _drop_test_schema(*, bind, tables=None, checkfirst=True) -> None:
    """Disable SQLite FKs only around test teardown of the cyclic full schema."""

    if bind.dialect.name != "sqlite":
        _drop_all(bind=bind, tables=tables, checkfirst=checkfirst)
        return
    with bind.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            _drop_all(
                bind=connection,
                tables=tables,
                checkfirst=checkfirst,
            )
            connection.commit()
        finally:
            if connection.in_transaction():
                connection.rollback()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


Base.metadata.drop_all = _drop_test_schema
