from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session, sessionmaker

from app.acquisition.infrastructure.acquisition_models import (
    AcquisitionBundleModel,
    AcquisitionOutboxModel,
    AcquisitionSourceModel,
    CrawlJobModel,
    RawSnapshotObservationModel,
    RawSnapshotModel,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SqlAlchemyAcquisitionStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    # -- Source CRUD --

    def create_source(self, value: dict[str, object]) -> dict[str, object]:
        with self.sessions.begin() as session:
            row = AcquisitionSourceModel(
                name=str(value["name"]),
                source_type=str(value["source_type"]),
                endpoint_config=dict(value.get("endpoint_config") or {}),
                auth_config=dict(value.get("auth_config") or {}),
                rate_limit_rps=float(value.get("rate_limit_rps", 1.0)),
                compliance_policy=dict(value.get("compliance_policy") or {}),
            )
            session.add(row)
            session.flush()
            return self._source_dict(row)

    def get_source(self, source_id: str) -> dict[str, object] | None:
        with self.sessions() as session:
            row = session.get(AcquisitionSourceModel, source_id)
            return self._source_dict(row) if row else None

    def list_sources(self, source_type: str | None = None, status: str | None = None) -> list[dict[str, object]]:
        with self.sessions() as session:
            query = select(AcquisitionSourceModel).order_by(AcquisitionSourceModel.created_at)
            if source_type:
                query = query.where(AcquisitionSourceModel.source_type == source_type)
            if status:
                query = query.where(AcquisitionSourceModel.status == status)
            return [self._source_dict(row) for row in session.scalars(query)]

    def update_source(self, source_id: str, value: dict[str, object]) -> dict[str, object] | None:
        with self.sessions.begin() as session:
            row = session.get(AcquisitionSourceModel, source_id)
            if row is None:
                return None
            for field in ("name", "source_type", "endpoint_config", "auth_config", "rate_limit_rps", "compliance_policy"):
                if field in value:
                    setattr(row, field, value[field])
            row.updated_at = _utc_now()
            session.flush()
            return self._source_dict(row)

    def delete_source(self, source_id: str) -> dict[str, object] | None:
        with self.sessions.begin() as session:
            row = session.get(AcquisitionSourceModel, source_id)
            if row is None:
                return None
            row.status = "deprecated"
            row.updated_at = _utc_now()
            session.flush()
            return self._source_dict(row)

    # -- CrawlJob --

    def create_crawl_job(self, value: dict[str, object]) -> dict[str, object]:
        with self.sessions.begin() as session:
            row = CrawlJobModel(
                source_id=str(value["source_id"]),
                window_start=datetime.fromisoformat(str(value["window_start"]).replace("Z", "+00:00")),
                window_end=datetime.fromisoformat(str(value["window_end"]).replace("Z", "+00:00")),
                max_retries=int(value.get("max_retries", 3)),
                rate_limit_rps=float(value["rate_limit_rps"]) if value.get("rate_limit_rps") else None,
            )
            session.add(row)
            session.flush()
            return self._job_dict(row)

    def get_crawl_job(self, job_id: str) -> dict[str, object] | None:
        with self.sessions() as session:
            row = session.get(CrawlJobModel, job_id)
            return self._job_dict(row) if row else None

    def list_crawl_jobs(self, source_id: str | None = None, status: str | None = None) -> list[dict[str, object]]:
        with self.sessions() as session:
            query = select(CrawlJobModel).order_by(CrawlJobModel.created_at.desc())
            if source_id:
                query = query.where(CrawlJobModel.source_id == source_id)
            if status:
                query = query.where(CrawlJobModel.status == status)
            return [self._job_dict(row) for row in session.scalars(query)]

    def mark_job_running(self, job_id: str) -> bool:
        with self.sessions.begin() as session:
            result = session.execute(
                update(CrawlJobModel)
                .where(CrawlJobModel.id == job_id, CrawlJobModel.status == "pending")
                .values(status="running", started_at=_utc_now())
            )
            return result.rowcount == 1

    def claim_crawl_job(
        self, worker_id: str, *, now: datetime, lease: timedelta
    ) -> dict[str, object] | None:
        """Atomically claim one available acquisition job on PostgreSQL."""
        with self.sessions.begin() as session:
            row = session.scalar(
                select(CrawlJobModel)
                .where(CrawlJobModel.status == "pending", CrawlJobModel.available_at <= now)
                .order_by(CrawlJobModel.created_at, CrawlJobModel.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.status = "running"
            row.started_at = row.started_at or now
            row.lease_owner = worker_id
            row.lease_expires_at = now + lease
            session.flush()
            return self._job_dict(row)

    def recover_expired_crawl_jobs(self, *, now: datetime) -> int:
        with self.sessions.begin() as session:
            rows = list(session.scalars(
                select(CrawlJobModel)
                .where(
                    CrawlJobModel.status == "running",
                    CrawlJobModel.lease_expires_at < now,
                )
                .with_for_update(skip_locked=True)
            ))
            for row in rows:
                row.status = "failed" if row.retry_count >= row.max_retries else "pending"
                row.available_at = now
                row.lease_owner = None
                row.lease_expires_at = None
                if row.status == "failed":
                    row.error_message = "acquisition worker lease expired after maximum retries"
                    row.completed_at = now
                else:
                    row.retry_count += 1
            return len(rows)

    def mark_job_failed(self, job_id: str, error: str, *, retryable: bool = True) -> bool:
        with self.sessions.begin() as session:
            row = session.get(CrawlJobModel, job_id)
            if row is None or row.status != "running":
                return False
            row.error_message = error
            if retryable and row.retry_count < row.max_retries:
                row.status = "pending"
                row.retry_count += 1
                row.available_at = _utc_now()
            else:
                row.status = "failed"
                row.completed_at = _utc_now()
            row.lease_owner = None
            row.lease_expires_at = None
            return True

    def retry_crawl_job(self, job_id: str) -> dict[str, object]:
        with self.sessions.begin() as session:
            row = session.get(CrawlJobModel, job_id)
            if row is None:
                raise LookupError(f"crawl job {job_id} not found")
            if row.status not in ("failed",):
                raise RuntimeError(f"crawl job {job_id} is not failed (current: {row.status})")
            row.status = "pending"
            row.error_message = None
            row.retry_count = 0
            row.available_at = _utc_now()
            row.lease_owner = None
            row.lease_expires_at = None
            session.flush()
            return self._job_dict(row)

    def cancel_crawl_job(self, job_id: str) -> dict[str, object]:
        with self.sessions.begin() as session:
            row = session.get(CrawlJobModel, job_id)
            if row is None:
                raise LookupError(f"crawl job {job_id} not found")
            if row.status not in ("pending", "running"):
                raise RuntimeError(
                    f"crawl job {job_id} cannot be cancelled (current: {row.status})"
                )
            row.status = "cancelled"
            row.completed_at = _utc_now()
            row.error_message = "cancelled by user"
            row.lease_owner = None
            row.lease_expires_at = None
            session.flush()
            return self._job_dict(row)

    # -- Snapshots --

    def save_snapshot(self, job_id: str, source_id: str, external_id: str, raw_content: dict[str, object], source_version: str, content_type: str, metadata: dict[str, object] | None = None) -> dict[str, object]:
        with self.sessions.begin() as session:
            job = session.scalar(
                select(CrawlJobModel).where(
                    CrawlJobModel.id == job_id,
                    CrawlJobModel.source_id == source_id,
                )
            )
            if job is None:
                raise ValueError(f"job {job_id} does not belong to source {source_id}")
            row, _created = self._upsert_snapshot(
                session,
                job_id=job_id,
                source_id=source_id,
                record={
                    "external_id": external_id,
                    "raw_content": raw_content,
                    "source_version": source_version,
                    "content_type": content_type,
                    "metadata": dict(metadata or {}),
                },
            )
            self._observe_snapshot(session, job_id, row.id)
            return self._snapshot_dict(row)

    def complete_crawl_job(
        self, job_id: str, source_id: str, records: list[dict[str, object]]
    ) -> dict[str, object]:
        """Atomically persist lineage, complete the job, and publish its ready bundle."""
        with self.sessions.begin() as session:
            job = session.scalar(select(CrawlJobModel).where(
                CrawlJobModel.id == job_id,
                CrawlJobModel.source_id == source_id,
                CrawlJobModel.status == "running",
            ).with_for_update())
            if job is None:
                raise RuntimeError(f"crawl job {job_id} is not running")
            stored: list[RawSnapshotModel] = []
            unique_snapshots: list[RawSnapshotModel] = []
            seen_snapshot_ids: set[str] = set()
            new_snapshot_count = 0
            for record in records:
                row, created = self._upsert_snapshot(
                    session,
                    job_id=job_id,
                    source_id=source_id,
                    record=record,
                )
                new_snapshot_count += int(created)
                stored.append(row)
                self._observe_snapshot(session, job_id, row.id)
                if row.id not in seen_snapshot_ids:
                    seen_snapshot_ids.add(row.id)
                    unique_snapshots.append(row)

            bundle = self._create_bundle(
                session,
                job=job,
                snapshots=unique_snapshots,
                bundle_type="raw_snapshot",
            )
            job.status = "succeeded"
            job.completed_at = _utc_now()
            job.error_message = None
            job.fetched_count = len(records)
            job.new_snapshot_count = new_snapshot_count
            job.duplicate_count = len(records) - new_snapshot_count
            job.lease_owner = None
            job.lease_expires_at = None
            session.flush()
            return {
                "snapshots": [self._snapshot_dict(row) for row in stored],
                "bundle": self._bundle_dict(bundle),
            }

    @staticmethod
    def _upsert_snapshot(
        session: Session,
        *,
        job_id: str,
        source_id: str,
        record: dict[str, object],
    ) -> tuple[RawSnapshotModel, bool]:
        snapshot_id = str(uuid4())
        inserted_id = session.scalar(
            postgresql_insert(RawSnapshotModel)
            .values(
                id=snapshot_id,
                job_id=job_id,
                source_id=source_id,
                external_id=str(record["external_id"]),
                raw_content=dict(record["raw_content"]),
                source_version=str(record["source_version"]),
                content_type=str(record["content_type"]),
                captured_at=record.get("captured_at") or _utc_now(),
                snapshot_metadata=dict(record.get("metadata") or {}),
            )
            .on_conflict_do_nothing(constraint="uq_raw_snapshot_identity")
            .returning(RawSnapshotModel.id)
        )
        created = inserted_id is not None
        if inserted_id is None:
            inserted_id = session.scalar(select(RawSnapshotModel.id).where(
                RawSnapshotModel.source_id == source_id,
                RawSnapshotModel.external_id == str(record["external_id"]),
                RawSnapshotModel.source_version == str(record["source_version"]),
            ))
        if inserted_id is None:
            raise RuntimeError("snapshot upsert did not return the stored snapshot")
        row = session.get(RawSnapshotModel, inserted_id)
        if row is None:
            raise RuntimeError(f"snapshot {inserted_id} disappeared during acquisition")
        return row, created

    @staticmethod
    def _observe_snapshot(session: Session, job_id: str, snapshot_id: str) -> None:
        session.execute(
            postgresql_insert(RawSnapshotObservationModel)
            .values(job_id=job_id, snapshot_id=snapshot_id, observed_at=_utc_now())
            .on_conflict_do_nothing(index_elements=["job_id", "snapshot_id"])
        )

    def list_snapshots(self, source_id: str, offset: int = 0, limit: int = 50) -> list[dict[str, object]]:
        with self.sessions() as session:
            query = (
                select(RawSnapshotModel)
                .where(RawSnapshotModel.source_id == source_id)
                .order_by(RawSnapshotModel.captured_at.desc())
                .offset(offset).limit(limit)
            )
            return [self._snapshot_dict(row) for row in session.scalars(query)]

    def list_snapshot_observations(self, job_id: str) -> list[dict[str, object]]:
        with self.sessions() as session:
            rows = session.scalars(
                select(RawSnapshotObservationModel)
                .where(RawSnapshotObservationModel.job_id == job_id)
                .order_by(RawSnapshotObservationModel.observed_at)
            )
            return [self._observation_dict(row) for row in rows]

    # -- Bundles --

    def create_bundle_for_job(
        self,
        job_id: str,
        source_id: str,
        snapshot_ids: list[str],
        bundle_type: str,
    ) -> dict[str, object]:
        if not snapshot_ids:
            raise ValueError("snapshot_ids must not be empty")
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("snapshot_ids must not contain duplicates")
        with self.sessions.begin() as session:
            job = session.scalar(
                select(CrawlJobModel)
                .where(CrawlJobModel.id == job_id)
                .with_for_update()
            )
            if job is None:
                raise LookupError(f"crawl job {job_id} not found")
            if job.source_id != source_id:
                raise ValueError(f"crawl job {job_id} does not belong to source {source_id}")
            if job.status != "succeeded":
                raise RuntimeError(f"crawl job {job_id} is not succeeded")
            rows = list(session.scalars(
                select(RawSnapshotModel).where(RawSnapshotModel.id.in_(snapshot_ids))
            ))
            by_id = {row.id: row for row in rows}
            missing = [snapshot_id for snapshot_id in snapshot_ids if snapshot_id not in by_id]
            if missing:
                raise LookupError(f"snapshots not found: {', '.join(missing)}")
            cross_source = [row.id for row in rows if row.source_id != source_id]
            if cross_source:
                raise ValueError(
                    f"snapshots do not belong to source {source_id}: {', '.join(cross_source)}"
                )
            observed = set(session.scalars(
                select(RawSnapshotObservationModel.snapshot_id).where(
                    RawSnapshotObservationModel.job_id == job_id,
                    RawSnapshotObservationModel.snapshot_id.in_(snapshot_ids),
                )
            ))
            unobserved = [snapshot_id for snapshot_id in snapshot_ids if snapshot_id not in observed]
            if unobserved:
                raise ValueError(
                    f"snapshots were not observed by job {job_id}: {', '.join(unobserved)}"
                )
            ordered = [by_id[snapshot_id] for snapshot_id in snapshot_ids]
            existing = session.scalar(
                select(AcquisitionBundleModel).where(AcquisitionBundleModel.job_id == job_id)
            )
            if existing is not None:
                if (
                    existing.source_id == source_id
                    and existing.snapshot_ids == snapshot_ids
                    and existing.bundle_type == bundle_type
                ):
                    return self._bundle_dict(existing)
                raise RuntimeError(f"crawl job {job_id} already has a different bundle")
            return self._bundle_dict(self._create_bundle(
                session,
                job=job,
                snapshots=ordered,
                bundle_type=bundle_type,
            ))

    def _create_bundle(
        self,
        session: Session,
        *,
        job: CrawlJobModel,
        snapshots: list[RawSnapshotModel],
        bundle_type: str,
    ) -> AcquisitionBundleModel:
        snapshot_ids = [row.id for row in snapshots]
        payload: dict[str, object] = {
            "bundle_type": bundle_type,
            "job_id": job.id,
            "source_id": job.source_id,
            "snapshot_ids": snapshot_ids,
            "record_count": len(snapshot_ids),
            "acquisition_window": {
                "start": job.window_start.isoformat(),
                "end": job.window_end.isoformat(),
            },
            "records": [
                {
                    "snapshot_id": row.id,
                    "external_id": row.external_id,
                    "content": row.raw_content,
                    "source_version": row.source_version,
                    "captured_at": row.captured_at.isoformat(),
                }
                for row in snapshots
            ],
        }
        bundle = AcquisitionBundleModel(
            job_id=job.id,
            source_id=job.source_id,
            bundle_type=bundle_type,
            snapshot_ids=snapshot_ids,
            payload=payload,
            record_count=len(snapshot_ids),
            window_start=job.window_start,
            window_end=job.window_end,
            status="ready",
        )
        session.add(bundle)
        session.flush()
        session.add(AcquisitionOutboxModel(
            aggregate_type="Bundle",
            aggregate_id=bundle.id,
            event_type="bundle_ready",
            payload={
                "bundle_id": bundle.id,
                "job_id": job.id,
                "source_id": job.source_id,
                "snapshot_ids": snapshot_ids,
            },
        ))
        return bundle

    def get_bundle(self, bundle_id: str) -> dict[str, object] | None:
        with self.sessions() as session:
            row = session.get(AcquisitionBundleModel, bundle_id)
            return self._bundle_dict(row) if row else None

    def get_bundle_for_job(self, job_id: str) -> dict[str, object] | None:
        with self.sessions() as session:
            row = session.scalar(
                select(AcquisitionBundleModel).where(AcquisitionBundleModel.job_id == job_id)
            )
            return self._bundle_dict(row) if row else None

    # -- Outbox --

    def enqueue_outbox(self, aggregate_type: str, aggregate_id: str, event_type: str, payload: dict[str, object]) -> dict[str, object]:
        with self.sessions.begin() as session:
            row = AcquisitionOutboxModel(
                aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                event_type=event_type, payload=payload,
            )
            session.add(row)
            session.flush()
            return self._outbox_dict(row)

    def poll_outbox(self, status: str = "pending", limit: int = 20) -> list[dict[str, object]]:
        with self.sessions() as session:
            query = (
                select(AcquisitionOutboxModel)
                .where(AcquisitionOutboxModel.status == status)
                .order_by(AcquisitionOutboxModel.created_at)
                .limit(limit)
            )
            return [self._outbox_dict(row) for row in session.scalars(query)]

    def claim_outbox(
        self, worker_id: str, *, now: datetime, lease: timedelta, limit: int = 20
    ) -> list[dict[str, object]]:
        with self.sessions.begin() as session:
            rows = list(session.scalars(
                select(AcquisitionOutboxModel)
                .where(AcquisitionOutboxModel.status == "pending")
                .order_by(AcquisitionOutboxModel.created_at, AcquisitionOutboxModel.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ))
            for row in rows:
                row.status = "processing"
                row.lease_owner = worker_id
                row.lease_expires_at = now + lease
            session.flush()
            return [self._outbox_dict(row) for row in rows]

    def recover_expired_outbox(self, *, now: datetime) -> int:
        with self.sessions.begin() as session:
            rows = list(session.scalars(
                select(AcquisitionOutboxModel)
                .where(
                    AcquisitionOutboxModel.status == "processing",
                    AcquisitionOutboxModel.lease_expires_at < now,
                )
                .with_for_update(skip_locked=True)
            ))
            for row in rows:
                row.status = "pending" if row.retry_count < 3 else "failed"
                row.retry_count += 1
                row.lease_owner = None
                row.lease_expires_at = None
                row.error_message = "outbox processing lease expired"
            return len(rows)

    def mark_outbox_processed(self, outbox_id: str, worker_id: str) -> bool:
        with self.sessions.begin() as session:
            result = session.execute(
                update(AcquisitionOutboxModel)
                .where(
                    AcquisitionOutboxModel.id == outbox_id,
                    AcquisitionOutboxModel.status == "processing",
                    AcquisitionOutboxModel.lease_owner == worker_id,
                )
                .values(
                    status="processed", processed_at=_utc_now(), error_message=None,
                    lease_owner=None, lease_expires_at=None,
                )
            )
            return result.rowcount == 1

    def mark_outbox_failed(self, outbox_id: str, worker_id: str, error: str) -> bool:
        with self.sessions.begin() as session:
            row = session.scalar(select(AcquisitionOutboxModel).where(
                AcquisitionOutboxModel.id == outbox_id,
                AcquisitionOutboxModel.status == "processing",
                AcquisitionOutboxModel.lease_owner == worker_id,
            ).with_for_update())
            if row is None:
                return False
            row.retry_count += 1
            row.status = "failed" if row.retry_count >= 3 else "pending"
            row.error_message = error[:4000]
            row.lease_owner = None
            row.lease_expires_at = None
            return True

    # -- Dict converters --

    @staticmethod
    def _source_dict(row: AcquisitionSourceModel) -> dict[str, object]:
        return {
            "id": row.id, "name": row.name, "source_type": row.source_type,
            "endpoint_config": row.endpoint_config, "auth_config": row.auth_config,
            "rate_limit_rps": row.rate_limit_rps, "compliance_policy": row.compliance_policy,
            "status": row.status, "created_at": row.created_at, "updated_at": row.updated_at,
        }

    @staticmethod
    def _job_dict(row: CrawlJobModel) -> dict[str, object]:
        return {
            "id": row.id, "source_id": row.source_id, "status": row.status,
            "window_start": row.window_start,
            "window_end": row.window_end, "retry_count": row.retry_count,
            "max_retries": row.max_retries, "rate_limit_rps": row.rate_limit_rps,
            "available_at": row.available_at, "lease_owner": row.lease_owner,
            "lease_expires_at": row.lease_expires_at,
            "error_message": row.error_message, "created_at": row.created_at,
            "fetched_count": row.fetched_count,
            "new_snapshot_count": row.new_snapshot_count,
            "duplicate_count": row.duplicate_count,
            "started_at": row.started_at, "completed_at": row.completed_at,
        }

    @staticmethod
    def _snapshot_dict(row: RawSnapshotModel) -> dict[str, object]:
        return {
            "id": row.id, "job_id": row.job_id, "source_id": row.source_id,
            "external_id": row.external_id, "raw_content": row.raw_content,
            "source_version": row.source_version, "content_type": row.content_type,
            "captured_at": row.captured_at, "metadata": row.snapshot_metadata,
        }

    @staticmethod
    def _observation_dict(row: RawSnapshotObservationModel) -> dict[str, object]:
        return {
            "job_id": row.job_id,
            "snapshot_id": row.snapshot_id,
            "observed_at": row.observed_at,
        }

    @staticmethod
    def _bundle_dict(row: AcquisitionBundleModel) -> dict[str, object]:
        return {
            "id": row.id, "job_id": row.job_id, "source_id": row.source_id,
            "bundle_type": row.bundle_type, "snapshot_ids": row.snapshot_ids,
            "payload": row.payload,
            "record_count": row.record_count,
            "window_start": row.window_start, "window_end": row.window_end,
            "analysis_run_id": row.analysis_run_id,
            "status": row.status, "created_at": row.created_at,
        }

    @staticmethod
    def _outbox_dict(row: AcquisitionOutboxModel) -> dict[str, object]:
        return {
            "id": row.id, "aggregate_type": row.aggregate_type,
            "aggregate_id": row.aggregate_id, "event_type": row.event_type,
            "payload": row.payload, "status": row.status,
            "retry_count": row.retry_count, "error_message": row.error_message,
            "lease_owner": row.lease_owner, "lease_expires_at": row.lease_expires_at,
            "created_at": row.created_at, "processed_at": row.processed_at,
        }
