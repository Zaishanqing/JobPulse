"""Ports for the acquisition bounded context.

The application layer depends on these protocols, not on HTTP clients,
filesystem paths, or the crawler package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from app.contexts.acquisition.domain import AcquisitionJobRecord
from app.offline_import.contracts import ImportSummary


@dataclass(frozen=True)
class CrawlerSourceStatus:
    source: str
    available: bool
    ready: bool
    login_required: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class CrawlerTaskRef:
    task_id: str


@dataclass(frozen=True)
class CrawlerTaskStatus:
    task_id: str
    status: str
    result_count: int = 0
    progress: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class BundleRef:
    bundle_id: str
    file_name: str
    record_count: int
    hash: str | None = None


@dataclass(frozen=True)
class CrawlerLoginStatus:
    logged_in: bool
    cookie_count: int
    running: bool
    status: str
    login_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    message: str | None = None
    updated_at: str | None = None
BossLoginStatus = CrawlerLoginStatus
LiepinLoginStatus = CrawlerLoginStatus


class CrawlerGateway(Protocol):
    def list_sources(self) -> list[CrawlerSourceStatus]: ...

    def save_boss_cookies(self, cookies: list[dict]) -> dict: ...

    def save_liepin_cookies(self, cookies: list[dict]) -> dict: ...

    def get_boss_login_status(self) -> BossLoginStatus: ...

    def get_liepin_login_status(self) -> LiepinLoginStatus: ...

    def start_crawl(
        self,
        *,
        source: str,
        keyword: str,
        city: str,
        pages: int,
    ) -> CrawlerTaskRef: ...

    def get_task(self, task_id: str) -> CrawlerTaskStatus: ...

    def export_bundle(self, *, task_id: str, source: str) -> BundleRef: ...


class BundleStorePort(Protocol):
    def resolve(self, bundle: BundleRef) -> Path: ...


class AcquisitionRepository(Protocol):
    def add(self, record: AcquisitionJobRecord) -> None: ...

    def get(self, job_id: str) -> AcquisitionJobRecord | None: ...

    def claim_pending(self, job_id: str, now: datetime) -> AcquisitionJobRecord | None: ...

    def list(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[AcquisitionJobRecord], int]: ...

    def save(self, record: AcquisitionJobRecord) -> None: ...

    def recover_stale(self, now: datetime, stale_after_seconds: float) -> int: ...


class AcquisitionUnitOfWork(Protocol):
    acquisition: AcquisitionRepository

    def __enter__(self) -> "AcquisitionUnitOfWork": ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class AcquisitionImporterPort(Protocol):
    def import_bundle(self, path: Path, *, allow_gap: bool = False, retry: bool = False) -> ImportSummary: ...


class AcquisitionBackgroundRunner(Protocol):
    def submit(self, fn) -> None: ...


__all__ = [
    "AcquisitionBackgroundRunner",
    "AcquisitionImporterPort",
    "AcquisitionRepository",
    "AcquisitionUnitOfWork",
    "BossLoginStatus",
    "CrawlerLoginStatus",
    "BundleRef",
    "BundleStorePort",
    "CrawlerGateway",
    "CrawlerSourceStatus",
    "CrawlerTaskRef",
    "CrawlerTaskStatus",
    "ImportSummary",
    "LiepinLoginStatus",
]
