from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.json_types import JsonObject


@dataclass(frozen=True)
class SourceJDRecord:
    id: str
    source_platform: str
    source_record_id: str
    latest_version_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SourceJDVersionRecord:
    id: str
    source_jd_id: str
    source_version: str
    schema_version: str
    raw_text: str
    content_hash: str
    raw_payload: JsonObject
    raw_html: str | None
    source_url: str | None
    crawl_time: datetime
    job_title_raw: str | None
    company_name_raw: str | None
    region_raw: str | None
    publish_time_raw: str | None
    text_canonicalization_version: str
    created_at: datetime


class SourceJDRepository(Protocol):
    def get(self, source_jd_id: str) -> SourceJDRecord | None: ...

    def get_by_identity(
        self, source_platform: str, source_record_id: str, *, for_update: bool = False
    ) -> SourceJDRecord | None: ...

    def add_source(self, source_platform: str, source_record_id: str) -> SourceJDRecord: ...

    def get_version(self, version_id: str) -> SourceJDVersionRecord | None: ...

    def get_version_by_source_version(
        self, source_jd_id: str, source_version: str
    ) -> SourceJDVersionRecord | None: ...

    def add_version(self, source_jd_id: str, envelope) -> SourceJDVersionRecord: ...

    def set_latest(self, source_jd_id: str, version_id: str) -> SourceJDRecord: ...

    def list_versions(self, source_jd_id: str) -> tuple[SourceJDVersionRecord, ...]: ...


class SourceJDUnitOfWork(Protocol):
    source_jds: SourceJDRepository

    def __enter__(self) -> "SourceJDUnitOfWork": ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...

    def acquire_import_lock(self, source_platform: str, source_record_id: str) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class SourceJDUoWFactory(Protocol):
    def __call__(self) -> AbstractContextManager[SourceJDUnitOfWork]: ...
