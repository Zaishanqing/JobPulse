from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.acquisition.infrastructure.acquisition_models import (
    AcquisitionBundleModel,
    AcquisitionSourceModel,
    RawSnapshotModel,
    RawSnapshotObservationModel,
)
from app.acquisition.ports.trend_input import TrendInputImportResult
from app.domain.analysis_run import NewAnalysisRun
from app.domain.market import SourceRecord
from app.infrastructure.models import (
    AnalysisRunLogModel,
    AnalysisRunModel,
    TrendInputRecordModel,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"bundle record {field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"bundle record {field} is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"bundle record {field} must include timezone")
    return _utc(parsed)


class SqlAlchemyTrendInputAdapter:
    def __init__(self, sessions: sessionmaker[Session], *, max_attempts: int = 3) -> None:
        self.sessions = sessions
        self.max_attempts = max_attempts

    def import_bundle(self, bundle_id: str) -> TrendInputImportResult:
        with self.sessions.begin() as session:
            bundle = session.scalar(
                select(AcquisitionBundleModel)
                .where(AcquisitionBundleModel.id == bundle_id)
                .with_for_update()
            )
            if bundle is None:
                raise LookupError(f"acquisition bundle {bundle_id} not found")
            if bundle.status not in {"ready", "imported"}:
                raise ValueError(f"acquisition bundle {bundle_id} is not ready")
            snapshots = self._validated_snapshots(session, bundle)
            records = [self._source_record(row) for row in snapshots]

            if bundle.analysis_run_id is not None:
                count = session.scalar(
                    select(func.count())
                    .select_from(TrendInputRecordModel)
                    .where(TrendInputRecordModel.bundle_id == bundle.id)
                ) or 0
                if count != len(records):
                    raise RuntimeError(
                        f"bundle {bundle.id} import is incomplete: expected {len(records)}, found {count}"
                    )
                bundle.status = "imported"
                return TrendInputImportResult(
                    bundle_id=bundle.id,
                    analysis_run_id=bundle.analysis_run_id,
                    imported_count=0,
                    duplicate_count=len(records),
                    status="already_imported",
                )

            run = self._create_analysis_run(session, bundle, records)
            for snapshot, record in zip(snapshots, records, strict=True):
                session.add(TrendInputRecordModel(
                    bundle_id=bundle.id,
                    acquisition_snapshot_id=snapshot.id,
                    analysis_run_id=run.id,
                    source=record.source,
                    external_id=record.external_id,
                    source_version=record.source_version,
                    title=record.title,
                    content=record.content,
                    url=record.url,
                    published_at=record.published_at,
                    captured_at=record.captured_at or snapshot.captured_at,
                    record_metadata=dict(record.metadata),
                ))
            session.flush()
            bundle.analysis_run_id = run.id
            bundle.status = "imported"
            return TrendInputImportResult(
                bundle_id=bundle.id,
                analysis_run_id=run.id,
                imported_count=len(records),
                duplicate_count=0,
                status="imported",
            )

    @staticmethod
    def _validated_snapshots(
        session: Session,
        bundle: AcquisitionBundleModel,
    ) -> list[RawSnapshotModel]:
        payload = bundle.payload
        if not isinstance(payload, Mapping):
            raise ValueError("bundle payload must be a JSON object")
        expected = {
            "job_id": bundle.job_id,
            "source_id": bundle.source_id,
            "snapshot_ids": bundle.snapshot_ids,
            "record_count": bundle.record_count,
        }
        for field, value in expected.items():
            if payload.get(field) != value:
                raise ValueError(f"bundle payload {field} does not match persisted contract")
        window = payload.get("acquisition_window")
        if not isinstance(window, Mapping):
            raise ValueError("bundle acquisition_window must be a JSON object")
        if (
            _parse_datetime(window.get("start"), "acquisition_window.start") != _utc(bundle.window_start)
            or _parse_datetime(window.get("end"), "acquisition_window.end") != _utc(bundle.window_end)
        ):
            raise ValueError("bundle acquisition_window does not match persisted contract")
        if not bundle.snapshot_ids or bundle.record_count != len(bundle.snapshot_ids):
            raise ValueError("bundle snapshot_ids and record_count contract is invalid")
        rows = list(session.scalars(
            select(RawSnapshotModel).where(RawSnapshotModel.id.in_(bundle.snapshot_ids))
        ))
        by_id = {row.id: row for row in rows}
        missing = [snapshot_id for snapshot_id in bundle.snapshot_ids if snapshot_id not in by_id]
        if missing:
            raise ValueError(f"bundle snapshots are missing: {', '.join(missing)}")
        ordered = [by_id[snapshot_id] for snapshot_id in bundle.snapshot_ids]
        if any(row.source_id != bundle.source_id for row in ordered):
            raise ValueError("bundle contains a snapshot from another source")
        observed = set(session.scalars(
            select(RawSnapshotObservationModel.snapshot_id).where(
                RawSnapshotObservationModel.job_id == bundle.job_id,
                RawSnapshotObservationModel.snapshot_id.in_(bundle.snapshot_ids),
            )
        ))
        if observed != set(bundle.snapshot_ids):
            raise ValueError("bundle contains a snapshot not observed by its crawl job")
        source_type = session.scalar(
            select(AcquisitionSourceModel.source_type).where(
                AcquisitionSourceModel.id == bundle.source_id
            )
        )
        if source_type is None:
            raise ValueError("bundle acquisition source is missing")
        for row in ordered:
            raw_source = row.raw_content.get("source") if isinstance(row.raw_content, Mapping) else None
            if raw_source != source_type:
                raise ValueError("bundle record source does not match acquisition source type")
        return ordered

    @staticmethod
    def _source_record(snapshot: RawSnapshotModel) -> SourceRecord:
        raw = snapshot.raw_content
        if not isinstance(raw, Mapping):
            raise ValueError(f"snapshot {snapshot.id} raw_content must be a JSON object")
        required = ("source", "source_version", "title", "content", "url", "published_at")
        missing = [field for field in required if not isinstance(raw.get(field), str) or not raw[field].strip()]
        if missing:
            raise ValueError(
                f"snapshot {snapshot.id} is missing Trend fields: {', '.join(missing)}"
            )
        metadata = raw.get("metadata") or {}
        if not isinstance(metadata, Mapping):
            raise ValueError(f"snapshot {snapshot.id} metadata must be a JSON object")
        return SourceRecord(
            source=str(raw["source"]),
            external_id=snapshot.external_id,
            source_version=str(raw["source_version"]),
            title=str(raw["title"]),
            content=str(raw["content"]),
            url=str(raw["url"]),
            published_at=_parse_datetime(raw["published_at"], "published_at"),
            captured_at=_utc(snapshot.captured_at),
            metadata=dict(metadata),
        )

    def _create_analysis_run(
        self,
        session: Session,
        bundle: AcquisitionBundleModel,
        records: list[SourceRecord],
    ) -> AnalysisRunModel:
        sources = tuple(dict.fromkeys(record.source for record in records))
        weights = {
            ("academic" if source in {"arxiv", "cvf", "acl"} else source): 1.0
            for source in sources
        }
        identity = f"acquisition-bundle:{bundle.id}"
        command = NewAnalysisRun(
            contract_version="trend-analysis.v2",
            request_id=identity,
            idempotency_key=identity,
            window_start=_utc(bundle.window_start),
            window_end=_utc(bundle.window_end),
            data_sources=sources,
            weights=weights,
            algorithm_version="acquisition-bundle.v1",
            formula_version="formula.v1",
            run_payload={"acquisition_bundle_id": bundle.id},
        )
        run = AnalysisRunModel(
            contract_version=command.contract_version,
            request_id=command.request_id,
            idempotency_key=command.idempotency_key,
            window_start=command.window_start,
            window_end=command.window_end,
            data_sources=list(command.data_sources),
            weights=dict(command.weights),
            algorithm_version=command.algorithm_version,
            formula_version=command.formula_version,
            run_type=command.run_type,
            run_payload=dict(command.run_payload or {}),
            max_attempts=self.max_attempts,
        )
        session.add(run)
        session.flush()
        session.add(AnalysisRunLogModel(
            run_id=run.id,
            level="info",
            event="created_from_acquisition_bundle",
            message="analysis run accepted from acquisition bundle",
            details={"bundle_id": bundle.id},
        ))
        return run

    def records_for_run(self, run_id: str, source: str) -> list[SourceRecord]:
        with self.sessions() as session:
            rows = list(session.scalars(
                select(TrendInputRecordModel)
                .where(
                    TrendInputRecordModel.analysis_run_id == run_id,
                    TrendInputRecordModel.source == source,
                )
                .order_by(TrendInputRecordModel.created_at, TrendInputRecordModel.id)
            ))
            return [SourceRecord(
                source=row.source,
                external_id=row.external_id,
                source_version=row.source_version,
                title=row.title,
                content=row.content,
                url=row.url,
                published_at=_utc(row.published_at),
                captured_at=_utc(row.captured_at),
                metadata=dict(row.record_metadata),
            ) for row in rows]
