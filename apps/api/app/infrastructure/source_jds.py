from __future__ import annotations

from types import TracebackType

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.contexts.source_jds import (
    SourceJDImportConflict,
    SourceJDRecord,
    SourceJDVersionRecord,
)
from app.models.source_jd import SourceJD, SourceJDVersion
from app.domain.json_types import freeze_json_object, thaw_json_object
from datetime import timezone
from jobgraph_contracts.source_identity import compute_content_hash


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _source_record(row: SourceJD) -> SourceJDRecord:
    return SourceJDRecord(
        row.id,
        row.source_platform,
        row.source_record_id,
        row.latest_version_id,
        _aware(row.created_at),
        _aware(row.updated_at),
    )


def _version_record(row: SourceJDVersion) -> SourceJDVersionRecord:
    return SourceJDVersionRecord(
        id=row.id,
        source_jd_id=row.source_jd_id,
        source_version=row.source_version,
        schema_version=row.schema_version,
        raw_text=row.raw_text,
        content_hash=row.content_hash,
        raw_payload=freeze_json_object(row.raw_payload, field="raw_payload"),
        raw_html=row.raw_html,
        source_url=row.source_url,
        crawl_time=_aware(row.crawl_time),
        job_title_raw=row.job_title_raw,
        company_name_raw=row.company_name_raw,
        region_raw=row.region_raw,
        publish_time_raw=row.publish_time_raw,
        text_canonicalization_version=row.text_canonicalization_version,
        created_at=_aware(row.created_at),
    )


class SqlAlchemySourceJDRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, source_jd_id: str) -> SourceJDRecord | None:
        row = self._session.get(SourceJD, source_jd_id)
        return _source_record(row) if row is not None else None

    def get_by_identity(
        self, source_platform: str, source_record_id: str, *, for_update: bool = False
    ) -> SourceJDRecord | None:
        query = self._session.query(SourceJD).filter(
            SourceJD.source_platform == source_platform,
            SourceJD.source_record_id == source_record_id,
        )
        if for_update:
            query = query.with_for_update()
        row = query.first()
        return _source_record(row) if row is not None else None

    def add_source(self, source_platform: str, source_record_id: str) -> SourceJDRecord:
        row = SourceJD(
            source_platform=source_platform,
            source_record_id=source_record_id,
        )
        self._session.add(row)
        self._flush_import()
        return _source_record(row)

    def get_version(self, version_id: str) -> SourceJDVersionRecord | None:
        row = self._session.get(SourceJDVersion, version_id)
        return _version_record(row) if row is not None else None

    def get_version_by_source_version(
        self, source_jd_id: str, source_version: str
    ) -> SourceJDVersionRecord | None:
        row = (
            self._session.query(SourceJDVersion)
            .filter(
                SourceJDVersion.source_jd_id == source_jd_id,
                SourceJDVersion.source_version == source_version,
            )
            .first()
        )
        return _version_record(row) if row is not None else None

    def add_version(self, source_jd_id: str, envelope) -> SourceJDVersionRecord:
        row = SourceJDVersion(
            source_jd_id=source_jd_id,
            source_version=envelope.source_version,
            schema_version=envelope.schema_version,
            raw_text=envelope.raw_text,
            content_hash=(
                envelope.content_hash
                if envelope.content_hash is not None
                else compute_content_hash(envelope.raw_text)
            ),
            raw_payload=thaw_json_object(
                freeze_json_object(envelope.raw_payload, field="raw_payload")
            ),
            raw_html=envelope.raw_html,
            source_url=envelope.source_url,
            crawl_time=envelope.crawl_time,
            job_title_raw=envelope.job_title_raw,
            company_name_raw=envelope.company_name_raw,
            region_raw=envelope.region_raw,
            publish_time_raw=envelope.publish_time_raw,
            text_canonicalization_version=envelope.text_canonicalization_version,
        )
        self._session.add(row)
        self._flush_import()
        return _version_record(row)

    def set_latest(self, source_jd_id: str, version_id: str) -> SourceJDRecord:
        row = self._session.get(SourceJD, source_jd_id)
        if row is None:
            raise LookupError(source_jd_id)
        row.latest_version_id = version_id
        self._flush_import()
        return _source_record(row)

    def list_versions(self, source_jd_id: str) -> tuple[SourceJDVersionRecord, ...]:
        rows = (
            self._session.query(SourceJDVersion)
            .filter(SourceJDVersion.source_jd_id == source_jd_id)
            .order_by(SourceJDVersion.created_at.desc(), SourceJDVersion.id.desc())
            .all()
        )
        return tuple(_version_record(row) for row in rows)

    def _flush_import(self) -> None:
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise SourceJDImportConflict("Source JD import conflicted") from exc


class SqlAlchemySourceJDUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemySourceJDUnitOfWork":
        self._session = self._session_factory()
        self.source_jds = SqlAlchemySourceJDRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is None:
            return
        if exc_type is not None:
            self.rollback()
        self._session.close()

    def acquire_import_lock(self, source_platform: str, source_record_id: str) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        dialect = self._session.get_bind().dialect.name
        if dialect == "sqlite":
            self._session.execute(text("BEGIN IMMEDIATE"))
        elif dialect == "postgresql":
            source_key = f"{source_platform}:{source_record_id}"
            self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:source_key, 0))"),
                {"source_key": source_key},
            )

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise SourceJDImportConflict("Source JD import conflicted") from exc

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
