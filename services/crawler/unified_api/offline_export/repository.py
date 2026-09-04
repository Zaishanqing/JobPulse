from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.offline_bundle import (
    BUNDLE_SCHEMA_VERSION,
    RECORD_SCHEMA_VERSION,
    BundleMode,
)

from unified_api.offline_export.contracts import ExportBatchRecord


class ExportRepository(Protocol):
    def create_batch(
        self,
        *,
        bundle_id: str,
        mode: BundleMode,
        parent_bundle_id: str | None,
    ) -> str: ...

    def list_records(
        self, *, mode: BundleMode, limit: int | None, task_id: str | None = None
    ) -> list[ExportBatchRecord]: ...

    def latest_completed_bundle_id(self) -> str | None: ...

    def is_completed_bundle(self, bundle_id: str) -> bool: ...

    def complete_batch(
        self,
        *,
        batch_id: str,
        records: list[ExportBatchRecord],
        file_name: str,
    ) -> None: ...

    def fail_batch(self, batch_id: str, error: str) -> None: ...


def _connection():
    from unified_api.database import get_conn

    return get_conn()


class MySQLExportRepository:
    """Persistence adapter limited to crawler-local MySQL."""

    def create_batch(
        self,
        *,
        bundle_id: str,
        mode: BundleMode,
        parent_bundle_id: str | None,
    ) -> str:
        batch_id = str(uuid4())
        connection = _connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO crawler_export_batches (
                        id, bundle_id, bundle_schema_version,
                        record_schema_version, mode, parent_bundle_id,
                        status, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'building', %s)
                    """,
                    (
                        batch_id,
                        bundle_id,
                        BUNDLE_SCHEMA_VERSION,
                        RECORD_SCHEMA_VERSION,
                        mode.value,
                        parent_bundle_id,
                        datetime.now(timezone.utc).replace(tzinfo=None),
                    ),
                )
            connection.commit()
            return batch_id
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_records(
        self, *, mode: BundleMode, limit: int | None, task_id: str | None = None
    ) -> list[ExportBatchRecord]:
        conditions: list[str] = []
        parameters: list[object] = []
        if mode is BundleMode.INCREMENTAL:
            conditions.append(
                """
                NOT EXISTS (
                    SELECT 1
                    FROM crawler_export_members member
                    JOIN crawler_export_batches batch ON batch.id = member.batch_id
                    WHERE member.publication_id = publication.id
                      AND batch.status = 'completed'
                )
                """
            )
        if task_id is not None:
            conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM crawler_task_publications task_publication
                    WHERE task_publication.publication_id = publication.id
                      AND task_publication.task_id = %s
                )
                """
            )
            parameters.append(task_id)
        where_sql = ""
        if conditions:
            where_sql = "WHERE " + " AND ".join(conditions)
        query = f"""
            SELECT publication.id AS publication_id,
                   publication.envelope_payload
            FROM crawler_publications publication
            {where_sql}
            ORDER BY publication.source_platform,
                     publication.source_record_id,
                     publication.source_version,
                     publication.id
        """
        if limit is not None:
            query += " LIMIT %s"
            parameters.append(limit)
        connection = _connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(query, tuple(parameters))
                rows = cursor.fetchall()
        finally:
            connection.close()
        records = []
        for row in rows:
            payload = row["envelope_payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            records.append(
                ExportBatchRecord(
                    publication_id=row["publication_id"],
                    envelope=CrawlerJDEnvelopeV1.model_validate(payload),
                )
            )
        return records

    def latest_completed_bundle_id(self) -> str | None:
        connection = _connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT bundle_id
                    FROM crawler_export_batches
                    WHERE status='completed'
                    ORDER BY completed_at DESC, created_at DESC, bundle_id DESC
                    LIMIT 1
                    """
                )
                row = cursor.fetchone()
            return row["bundle_id"] if row is not None else None
        finally:
            connection.close()

    def is_completed_bundle(self, bundle_id: str) -> bool:
        connection = _connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT 1
                    FROM crawler_export_batches
                    WHERE bundle_id=%s AND status='completed'
                    LIMIT 1
                    """,
                    (bundle_id,),
                )
                return cursor.fetchone() is not None
        finally:
            connection.close()

    def complete_batch(
        self,
        *,
        batch_id: str,
        records: list[ExportBatchRecord],
        file_name: str,
    ) -> None:
        connection = _connection()
        try:
            with connection.cursor() as cursor:
                for line_number, record in enumerate(records, start=1):
                    envelope = record.envelope
                    cursor.execute(
                        """
                        INSERT INTO crawler_export_members (
                            id, batch_id, publication_id, source_platform,
                            source_record_id, source_version, line_number, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            str(uuid4()),
                            batch_id,
                            record.publication_id,
                            envelope.source_platform,
                            envelope.source_record_id,
                            envelope.source_version,
                            line_number,
                            datetime.now(timezone.utc).replace(tzinfo=None),
                        ),
                    )
                cursor.execute(
                    """
                    UPDATE crawler_export_batches
                    SET record_count=%s, file_name=%s, status='completed',
                        completed_at=%s, last_error=NULL
                    WHERE id=%s AND status='building'
                    """,
                    (
                        len(records),
                        file_name,
                        datetime.now(timezone.utc).replace(tzinfo=None),
                        batch_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Export batch is no longer building")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fail_batch(self, batch_id: str, error: str) -> None:
        connection = _connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE crawler_export_batches
                    SET status='failed', last_error=%s
                    WHERE id=%s AND status='building'
                    """,
                    (error[:4000], batch_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
