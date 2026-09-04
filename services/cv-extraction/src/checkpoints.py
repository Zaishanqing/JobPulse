from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_checkpoint_key(
    *,
    document_id: str,
    raw_text: str,
    runtime_fingerprint: dict[str, str],
) -> str:
    material = {
        "document_id": document_id,
        "raw_text_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        "runtime": runtime_fingerprint,
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_content_checkpoint_key(
    *, raw_text: str, runtime_fingerprint: dict[str, str]
) -> str:
    """Build the cross-document key used only by identity-neutral shards."""

    material = {
        "raw_text_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        "runtime": runtime_fingerprint,
        "scope": "section-content-v1",
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SQLiteExtractionCheckpointStore:
    """Durable stage checkpoints for retrying the same extraction input."""

    def __init__(self, database_path: str | Path) -> None:
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS extraction_checkpoints (
                    checkpoint_key TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (checkpoint_key, stage)
                )
                """
            )

    def load(self, checkpoint_key: str, stage: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM extraction_checkpoints
                WHERE checkpoint_key = ? AND stage = ?
                """,
                (checkpoint_key, stage),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid checkpoint payload for stage {stage!r}")
        return payload

    def save(
        self,
        checkpoint_key: str,
        stage: str,
        payload: dict[str, Any],
    ) -> None:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO extraction_checkpoints (
                    checkpoint_key, stage, payload_json, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(checkpoint_key, stage) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (checkpoint_key, stage, encoded, updated_at),
            )
