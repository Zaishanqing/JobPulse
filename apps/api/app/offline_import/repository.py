from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.offline_import import OfflineImportBatch, OfflineImportItem
from app.offline_import.contracts import (
    ImportBatchRecord,
    ImportSummary,
    VerifiedBundle,
    VerifiedEnvelope,
)


class OfflineImportRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def find_batch(self, bundle_id: str) -> ImportBatchRecord | None:
        with self._session_factory() as session:
            row = (
                session.query(OfflineImportBatch)
                .filter(OfflineImportBatch.bundle_id == bundle_id)
                .one_or_none()
            )
            if row is None:
                return None
            return ImportBatchRecord(row.id, row.bundle_id, row.status, row.bundle_digest)

    def create_batch(self, bundle: VerifiedBundle) -> str:
        manifest = bundle.manifest
        batch_id = str(uuid4())
        with self._session_factory() as session:
            session.add(
                OfflineImportBatch(
                    id=batch_id,
                    bundle_id=manifest.bundle_id,
                    bundle_digest=bundle.bundle_digest,
                    bundle_schema_version=manifest.bundle_schema_version,
                    record_schema_version=manifest.record_schema_version,
                    mode=manifest.mode.value,
                    parent_bundle_id=manifest.parent_bundle_id,
                    file_name=bundle.path.name,
                    record_count=manifest.record_count,
                    status="importing",
                    started_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
        return batch_id

    def prepare_retry(self, batch_id: str) -> None:
        with self._session_factory() as session:
            batch = session.get(OfflineImportBatch, batch_id)
            if batch is None:
                raise LookupError(batch_id)
            batch.status = "importing"
            batch.finished_at = None
            batch.error_message = None
            session.commit()

    def parent_is_completed(self, bundle_id: str) -> bool:
        with self._session_factory() as session:
            return (
                session.query(OfflineImportBatch.id)
                .filter(
                    OfflineImportBatch.bundle_id == bundle_id,
                    OfflineImportBatch.status == "completed",
                )
                .first()
                is not None
            )

    def item_status(self, batch_id: str, line_number: int) -> str | None:
        with self._session_factory() as session:
            row = (
                session.query(OfflineImportItem.status)
                .filter(
                    OfflineImportItem.batch_id == batch_id,
                    OfflineImportItem.line_number == line_number,
                )
                .one_or_none()
            )
            return row[0] if row is not None else None

    def ensure_pending_item(self, batch_id: str, record: VerifiedEnvelope) -> None:
        with self._session_factory() as session:
            existing = (
                session.query(OfflineImportItem.id)
                .filter(
                    OfflineImportItem.batch_id == batch_id,
                    OfflineImportItem.line_number == record.line_number,
                )
                .first()
            )
            if existing is not None:
                return
            envelope = record.envelope
            session.add(
                OfflineImportItem(
                    id=str(uuid4()),
                    batch_id=batch_id,
                    line_number=record.line_number,
                    source_platform=envelope.source_platform,
                    source_record_id=envelope.source_record_id,
                    source_version=envelope.source_version,
                    status="pending",
                )
            )
            session.commit()

    def finish_item(
        self,
        *,
        batch_id: str,
        line_number: int,
        status: str,
        source_jd_id: str | None = None,
        source_jd_version_id: str | None = None,
        extraction_task_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._session_factory() as session:
            item = (
                session.query(OfflineImportItem)
                .filter(
                    OfflineImportItem.batch_id == batch_id,
                    OfflineImportItem.line_number == line_number,
                )
                .one()
            )
            item.status = status
            item.source_jd_id = source_jd_id
            item.source_jd_version_id = source_jd_version_id
            item.extraction_task_id = extraction_task_id
            item.error_code = error_code
            item.error_message = error_message[:4000] if error_message is not None else None
            item.updated_at = datetime.now(timezone.utc)
            session.commit()

    def finalize(self, batch_id: str) -> ImportSummary:
        with self._session_factory() as session:
            batch = session.get(OfflineImportBatch, batch_id)
            if batch is None:
                raise LookupError(batch_id)
            counts = dict(
                session.query(
                    OfflineImportItem.status,
                    func.count(OfflineImportItem.id),
                )
                .filter(OfflineImportItem.batch_id == batch_id)
                .group_by(OfflineImportItem.status)
                .all()
            )
            batch.imported_count = int(counts.get("imported", 0))
            batch.skipped_count = int(counts.get("skipped", 0))
            batch.failed_count = int(counts.get("failed", 0))
            batch.status = "completed_with_errors" if batch.failed_count else "completed"
            batch.finished_at = datetime.now(timezone.utc)
            session.commit()
            return self._summary(batch)

    def fail_batch(self, batch_id: str, error: str) -> None:
        with self._session_factory() as session:
            batch = session.get(OfflineImportBatch, batch_id)
            if batch is None:
                return
            batch.status = "failed"
            batch.error_message = error[:4000]
            batch.finished_at = datetime.now(timezone.utc)
            session.commit()

    def summary(self, bundle_id: str) -> ImportSummary | None:
        with self._session_factory() as session:
            batch = (
                session.query(OfflineImportBatch)
                .filter(OfflineImportBatch.bundle_id == bundle_id)
                .one_or_none()
            )
            return self._summary(batch) if batch is not None else None

    def history(self) -> list[ImportSummary]:
        with self._session_factory() as session:
            return [
                self._summary(batch)
                for batch in session.query(OfflineImportBatch)
                .order_by(OfflineImportBatch.started_at.desc())
                .all()
            ]

    @staticmethod
    def _summary(batch: OfflineImportBatch) -> ImportSummary:
        return ImportSummary(
            batch_id=batch.id,
            bundle_id=batch.bundle_id,
            record_count=batch.record_count,
            imported_count=batch.imported_count,
            skipped_count=batch.skipped_count,
            failed_count=batch.failed_count,
            status=batch.status,
        )
