from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


class PostgresTrendSyncLeadership:
    """Ensure exactly one synchronizer runs across all API worker processes."""

    _LOCK_KEY = 0x4A505453

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._connection: Connection | None = None

    def acquire(self) -> bool:
        if self._connection is not None:
            return True
        connection = self._engine.connect()
        acquired = bool(connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": self._LOCK_KEY},
        ))
        if not acquired:
            connection.close()
            return False
        self._connection = connection
        return True

    def release(self) -> None:
        if self._connection is None:
            return
        try:
            self._connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": self._LOCK_KEY},
            )
        finally:
            self._connection.close()
            self._connection = None
