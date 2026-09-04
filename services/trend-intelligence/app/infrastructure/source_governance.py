from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.market import SourceRecord
from app.infrastructure.models import (
    ReplayCacheModel,
    SourceCircuitStateModel,
    SourceFetchAttemptModel,
    SourceSnapshotModel,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SqlAlchemySourceGovernanceStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def circuit_allows(self, source: str, now: datetime) -> bool:
        with self.sessions.begin() as session:
            row = session.get(SourceCircuitStateModel, source)
            if row is None or row.state == "closed":
                return True
            opened_until = row.opened_until
            if opened_until and opened_until.tzinfo is None:
                opened_until = opened_until.replace(tzinfo=timezone.utc)
            if opened_until and opened_until <= now:
                row.state = "half_open"
                return True
            return False

    @staticmethod
    def _identity(record: SourceRecord) -> tuple[str, str, str]:
        return record.source, record.external_id, record.source_version

    def record_attempt(self, *, run_id: str, source: str, status: str, duration_ms: float,
                       records: Sequence[SourceRecord], error_type: str | None,
                       window_end: datetime, failure_threshold: int,
                       open_seconds: int) -> None:
        identities = [self._identity(item) for item in records]
        duplicate_count = len(identities) - len(set(identities))
        required = sum(
            bool(value)
            for item in records
            for value in (item.external_id, item.title, item.content, item.url, item.published_at)
        )
        completeness = required / (len(records) * 5) if records else 0.0
        latest = max((item.published_at for item in records), default=None)
        freshness = max((window_end - latest).total_seconds(), 0) if latest else None
        with self.sessions.begin() as session:
            session.add(SourceFetchAttemptModel(
                analysis_run_id=run_id, source=source, status=status,
                duration_ms=round(duration_ms, 3), records_count=len(records),
                duplicate_count=duplicate_count, field_completeness=round(completeness, 6),
                freshness_seconds=freshness, error_type=error_type,
            ))
            circuit = session.get(SourceCircuitStateModel, source)
            if circuit is None:
                circuit = SourceCircuitStateModel(source=source, state="closed", consecutive_failures=0)
                session.add(circuit)
            if status in {"succeeded", "replayed"}:
                circuit.state = "closed"
                circuit.consecutive_failures = 0
                circuit.opened_until = None
                circuit.last_error_type = None
            elif status != "circuit_open":
                circuit.consecutive_failures = (circuit.consecutive_failures or 0) + 1
                circuit.last_error_type = error_type
                if circuit.consecutive_failures >= failure_threshold:
                    circuit.state = "open"
                    circuit.opened_until = utc_now() + timedelta(seconds=open_seconds)
            circuit.updated_at = utc_now()

    @staticmethod
    def _payload(record: SourceRecord) -> dict[str, object]:
        return {"source": record.source, "external_id": record.external_id,
                "source_version": record.source_version, "title": record.title,
                "content": record.content, "url": record.url,
                "published_at": record.published_at.isoformat(), "metadata": dict(record.metadata)}

    def cache_records(self, run_id: str, source: str, records: Sequence[SourceRecord], request_id: str) -> str:
        payload = [self._payload(item) for item in records]
        with self.sessions.begin() as session:
            row = session.scalar(select(ReplayCacheModel).where(
                ReplayCacheModel.analysis_run_id == run_id, ReplayCacheModel.source == source
            ))
            if row is None:
                row = ReplayCacheModel(
                    analysis_run_id=run_id, request_id=request_id, source=source,
                    run_id=run_id, records_payload=payload,
                )
                session.add(row)
                session.flush()
            return row.id

    def replay_records(self, run_id: str, source: str) -> list[SourceRecord] | None:
        with self.sessions() as session:
            row = session.scalar(select(ReplayCacheModel).where(
                ReplayCacheModel.analysis_run_id == run_id, ReplayCacheModel.source == source
            ))
            if row is None:
                return None
            return [SourceRecord(
                source=item["source"], external_id=item["external_id"],
                source_version=item["source_version"], title=item["title"],
                content=item["content"], url=item["url"],
                published_at=datetime.fromisoformat(item["published_at"]),
                metadata=item.get("metadata") or {},
            ) for item in row.records_payload]

    def source_health(self, source: str | None = None) -> list[dict[str, object]]:
        with self.sessions() as session:
            query = select(SourceFetchAttemptModel)
            if source:
                query = query.where(SourceFetchAttemptModel.source == source)
            attempts = list(session.scalars(query.order_by(SourceFetchAttemptModel.created_at)))
            snapshots = list(session.scalars(select(SourceSnapshotModel)))
            circuits = {row.source: row for row in session.scalars(select(SourceCircuitStateModel))}
        grouped: dict[str, list[SourceFetchAttemptModel]] = defaultdict(list)
        for item in attempts:
            grouped[item.source].append(item)
        results = []
        for item_source in sorted(set(grouped) | set(circuits)):
            rows = grouped[item_source]
            total = len(rows)
            circuit = circuits.get(item_source)
            results.append({
                "source": item_source,
                "attempts": total,
                "success_rate": round(sum(row.status in {"succeeded", "replayed"} for row in rows) / total, 6) if total else 0.0,
                "empty_result_rate": round(sum(row.status == "empty" for row in rows) / total, 6) if total else 0.0,
                "duplicate_rate": round(sum(row.duplicate_count for row in rows) / max(sum(row.records_count for row in rows), 1), 6),
                "average_duration_ms": round(sum(row.duration_ms for row in rows) / total, 3) if total else 0.0,
                "error_types": sorted({row.error_type for row in rows if row.error_type}),
                "freshness_seconds": min((row.freshness_seconds for row in rows if row.freshness_seconds is not None), default=None),
                "field_completeness": round(sum(row.field_completeness for row in rows) / total, 6) if total else 0.0,
                "circuit_state": circuit.state if circuit else "closed",
                "circuit_opened_until": circuit.opened_until if circuit else None,
            })
        return results
