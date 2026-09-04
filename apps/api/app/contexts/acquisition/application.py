"""Acquisition application services.

This layer orchestrates the crawler gateway, the offline bundle store and the
existing offline bundle importer.  It never talks to the crawler database and
never imports the crawler Python package.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from app.contexts.acquisition.domain import (
    AcquisitionJobCreate,
    AcquisitionJobRecord,
    AcquisitionRetryRejected,
    can_retry,
    require_transition,
)
from app.contexts.acquisition.ports import (
    AcquisitionBackgroundRunner,
    AcquisitionImporterPort,
    AcquisitionUnitOfWork,
    BossLoginStatus,
    BundleStorePort,
    CrawlerGateway,
    CrawlerSourceStatus,
    CrawlerTaskStatus,
    LiepinLoginStatus,
)
from app.domain.accounts import AccountActor
from app.domain.permissions import (
    ACQUISITION_JOB_MANAGE,
    ACQUISITION_READ,
    require_permission,
)
from app.offline_import.contracts import (
    BundleImportConflict,
    BundleVerificationError,
)
from app.offline_import.verifier import verify_bundle


class AcquisitionJobNotFound(LookupError):
    pass


class AcquisitionConflict(RuntimeError):
    pass


class AcquisitionError(RuntimeError):
    status = "crawl_failed"
    code = "ACQUISITION_INTERNAL"
    safe_message = "Acquisition failed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.safe_message)


class AcquisitionSourceUnavailable(AcquisitionError):
    status = "crawl_failed"
    code = "ACQUISITION_SOURCE_UNAVAILABLE"
    safe_message = "Acquisition source is unavailable."


class AcquisitionLoginRequired(AcquisitionError):
    status = "crawl_failed"
    code = "ACQUISITION_LOGIN_REQUIRED"
    safe_message = "Acquisition source login is required on the crawler service."


class AcquisitionCrawlFailed(AcquisitionError):
    status = "crawl_failed"
    code = "ACQUISITION_CRAWL_FAILED"
    safe_message = "Crawler task failed."


class AcquisitionExportFailed(AcquisitionError):
    status = "export_failed"
    code = "ACQUISITION_EXPORT_FAILED"
    safe_message = "Offline bundle export failed."


class AcquisitionBundleInvalid(AcquisitionError):
    status = "verify_failed"
    code = "ACQUISITION_BUNDLE_INVALID"
    safe_message = "Offline bundle verification failed."


class AcquisitionImportFailed(AcquisitionError):
    status = "import_failed"
    code = "ACQUISITION_IMPORT_FAILED"
    safe_message = "Offline bundle import failed."


class AcquisitionTimeout(AcquisitionError):
    status = "crawl_failed"
    code = "ACQUISITION_TIMEOUT"
    safe_message = "Acquisition timed out."


@dataclass(frozen=True)
class AcquisitionJobPage:
    items: tuple[AcquisitionJobRecord, ...]
    total: int
    page: int
    page_size: int


class AcquisitionUseCases:
    def __init__(
        self,
        uow_factory: Callable[[], AcquisitionUnitOfWork],
        crawler_gateway: CrawlerGateway,
        bundle_store: BundleStorePort,
        importer: AcquisitionImporterPort,
        background_runner: AcquisitionBackgroundRunner | None = None,
        *,
        poll_interval_seconds: float = 0.5,
        timeout_seconds: float = 300.0,
        stale_after_seconds: float = 3600.0,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._uow_factory = uow_factory
        self._crawler_gateway = crawler_gateway
        self._bundle_store = bundle_store
        self._importer = importer
        self._background_runner = background_runner or _ThreadBackgroundRunner()
        self._poll_interval_seconds = poll_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._stale_after_seconds = stale_after_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleeper = sleeper

    # ------------------------------------------------------------------
    # Read/write use cases
    # ------------------------------------------------------------------

    def create(
        self,
        actor: AccountActor,
        *,
        source: str,
        keyword: str,
        city: str,
        pages: int,
    ) -> AcquisitionJobRecord:
        require_permission(actor.role, ACQUISITION_JOB_MANAGE)
        source_value = source.strip()
        if not source_value:
            raise ValueError("source is required")
        if source_value != "feishu" and (
            not keyword.strip() or not city.strip()
        ):
            raise ValueError("source, keyword and city are required")
        if pages < 1 or pages > 100:
            raise ValueError("pages must be between 1 and 100")
        request = AcquisitionJobCreate(
            requested_by=actor.account_id,
            source=source_value,
            keyword=keyword.strip() or "all",
            city=city.strip() or "全国",
            pages=pages,
        )
        now = self._clock()
        record = _new_record(request, now)
        with self._uow_factory() as uow:
            uow.acquisition.add(record)
            uow.commit()
        self._background_runner.submit(lambda: self.run(record.id))
        return record

    def get(self, actor: AccountActor, job_id: str) -> AcquisitionJobRecord:
        require_permission(actor.role, ACQUISITION_READ)
        self.recover_stale()
        with self._uow_factory() as uow:
            record = uow.acquisition.get(job_id)
        if record is None:
            raise AcquisitionJobNotFound("Acquisition job not found")
        return record

    def list(
        self,
        actor: AccountActor,
        *,
        status: str | None = None,
        source: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> AcquisitionJobPage:
        require_permission(actor.role, ACQUISITION_READ)
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValueError("Invalid pagination")
        self.recover_stale()
        with self._uow_factory() as uow:
            items, total = uow.acquisition.list(
                status=status,
                source=source,
                offset=(page - 1) * page_size,
                limit=page_size,
            )
        return AcquisitionJobPage(tuple(items), total, page, page_size)

    def retry(self, actor: AccountActor, job_id: str) -> AcquisitionJobRecord:
        require_permission(actor.role, ACQUISITION_JOB_MANAGE)
        self.recover_stale()
        with self._uow_factory() as uow:
            current = uow.acquisition.get(job_id)
            if current is None:
                raise AcquisitionJobNotFound("Acquisition job not found")
            if not can_retry(current.status):
                raise AcquisitionRetryRejected(
                    "Only terminal failed acquisition jobs can be retried"
                )
            request = AcquisitionJobCreate(
                requested_by=actor.account_id,
                source=current.source,
                keyword=current.keyword,
                city=current.city,
                pages=current.pages,
                retry_of_id=current.id,
                attempt=current.attempt + 1,
            )
            now = self._clock()
            record = _new_record(request, now)
            uow.acquisition.add(record)
            uow.commit()
        self._background_runner.submit(lambda: self.run(record.id))
        return record

    def recover_stale(self) -> int:
        with self._uow_factory() as uow:
            count = uow.acquisition.recover_stale(
                self._clock(), self._stale_after_seconds
            )
            uow.commit()
        return count

    def resume_pending(self) -> int:
        with self._uow_factory() as uow:
            pending, _ = uow.acquisition.list(
                status="pending",
                offset=0,
                limit=10000,
            )
        for record in pending:
            self._background_runner.submit(
                lambda job_id=record.id: self.run(job_id)
            )
        return len(pending)

    def list_sources(self, actor: AccountActor) -> list[CrawlerSourceStatus]:
        require_permission(actor.role, ACQUISITION_READ)
        try:
            return self._crawler_gateway.list_sources()
        except Exception:
            return [
                CrawlerSourceStatus(
                    source="boss",
                    available=False,
                    ready=False,
                    reason="Crawler service unavailable",
                ),
                CrawlerSourceStatus(
                    source="liepin",
                    available=False,
                    ready=False,
                    reason="Crawler service unavailable",
                ),
                CrawlerSourceStatus(
                    source="feishu",
                    available=False,
                    ready=False,
                    reason="Crawler service unavailable",
                ),
        ]

    def save_boss_cookies(self, actor: AccountActor, cookies: list[dict]) -> dict:
        require_permission(actor.role, ACQUISITION_JOB_MANAGE)
        return self._crawler_gateway.save_boss_cookies(cookies)

    def save_liepin_cookies(self, actor: AccountActor, cookies: list[dict]) -> dict:
        require_permission(actor.role, ACQUISITION_JOB_MANAGE)
        return self._crawler_gateway.save_liepin_cookies(cookies)

    def get_boss_login_status(self, actor: AccountActor) -> BossLoginStatus:
        require_permission(actor.role, ACQUISITION_READ)
        return self._crawler_gateway.get_boss_login_status()

    def get_liepin_login_status(self, actor: AccountActor) -> LiepinLoginStatus:
        require_permission(actor.role, ACQUISITION_READ)
        return self._crawler_gateway.get_liepin_login_status()

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def run(self, job_id: str) -> None:
        now = self._clock()
        with self._uow_factory() as uow:
            job = uow.acquisition.claim_pending(job_id, now)
            if job is None:
                return
            uow.commit()
        try:
            self._run_crawl_and_import(job)
        except AcquisitionError as exc:
            job = self._load(job_id) or job
            self._fail(job, exc.status, exc.code, str(exc) or exc.safe_message)
        except BundleVerificationError as exc:
            job = self._load(job_id) or job
            if job.status == "verifying":
                self._fail(
                    job,
                    "verify_failed",
                    "ACQUISITION_BUNDLE_INVALID",
                    str(exc),
                )
            else:
                self._fail(
                    job,
                    "import_failed",
                    "ACQUISITION_IMPORT_FAILED",
                    str(exc),
                )
        except BundleImportConflict as exc:
            job = self._load(job_id) or job
            self._fail(job, "import_failed", "ACQUISITION_IMPORT_FAILED", str(exc))
        except Exception:
            job = self._load(job_id) or job
            status = self._stage_failure(job.status)
            self._fail(
                job,
                status,
                "ACQUISITION_INTERNAL",
                "Acquisition orchestration failed unexpectedly.",
            )

    def _load(self, job_id: str) -> AcquisitionJobRecord | None:
        with self._uow_factory() as uow:
            return uow.acquisition.get(job_id)

    def _save(self, record: AcquisitionJobRecord) -> None:
        with self._uow_factory() as uow:
            uow.acquisition.save(record)
            uow.commit()

    def _run_crawl_and_import(self, job: AcquisitionJobRecord) -> None:
        try:
            task_ref = self._crawler_gateway.start_crawl(
                source=job.source,
                keyword=job.keyword,
                city=job.city,
                pages=job.pages,
            )
        except AcquisitionSourceUnavailable:
            raise
        except AcquisitionLoginRequired:
            raise
        except Exception as exc:
            raise AcquisitionCrawlFailed(str(exc)) from exc

        job = job.with_fields(
            crawler_task_id=task_ref.task_id,
            updated_at=self._clock(),
        )
        self._save(job)

        task = self._wait_for_crawler(task_ref.task_id)
        if task.status in {"failed", "error"}:
            raise AcquisitionCrawlFailed(task.error_message or "Crawler task failed.")

        job = job.with_fields(
            discovered_count=task.result_count,
            progress=0.4,
            updated_at=self._clock(),
        )
        self._save(job)
        job = self._transition(job, "exporting", progress=0.5)
        self._save(job)

        try:
            bundle = self._crawler_gateway.export_bundle(
                task_id=task_ref.task_id, source=job.source
            )
        except Exception as exc:
            raise AcquisitionExportFailed(str(exc)) from exc

        job = job.with_fields(
            bundle_id=bundle.bundle_id,
            bundle_file_name=bundle.file_name,
            bundle_hash=bundle.hash,
            exported_count=bundle.record_count,
            progress=0.7,
            updated_at=self._clock(),
        )
        self._save(job)
        job = self._transition(job, "verifying", progress=0.8)
        self._save(job)

        path = self._bundle_store.resolve(bundle)
        verified = verify_bundle(path)
        job = job.with_fields(
            bundle_hash=verified.manifest.compressed_sha256
            or verified.manifest.uncompressed_sha256,
            exported_count=verified.manifest.record_count,
            updated_at=self._clock(),
        )
        self._save(job)
        job = self._transition(job, "importing", progress=0.9)
        self._save(job)

        summary = self._importer.import_bundle(path)

        now = self._clock()
        job = self._transition(
            job,
            "completed",
            progress=1.0,
            imported_count=summary.imported_count,
            no_op_count=summary.skipped_count,
            failed_count=summary.failed_count,
            exported_count=summary.record_count,
            import_batch_id=summary.batch_id,
            finished_at=now,
            error_code=None,
            error_message=None,
            updated_at=now,
        )
        self._save(job)

    def _wait_for_crawler(self, task_id: str) -> CrawlerTaskStatus:
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            task = self._crawler_gateway.get_task(task_id)
            if task.status in {"completed", "succeeded"}:
                return task
            if task.status in {"failed", "error"}:
                return task
            if time.monotonic() >= deadline:
                raise AcquisitionTimeout()
            self._sleeper(self._poll_interval_seconds)

    def _transition(
        self,
        job: AcquisitionJobRecord,
        target: str,
        **changes: object,
    ) -> AcquisitionJobRecord:
        require_transition(job.status, target)
        now = self._clock()
        values: dict[str, object] = {
            "status": target,
            "updated_at": now,
        }
        values.update(changes)
        if target in {"completed", "crawl_failed", "export_failed", "verify_failed", "import_failed", "cancelled"}:
            values.setdefault("finished_at", now)
        return job.with_fields(**values)

    def _fail(
        self,
        job: AcquisitionJobRecord,
        target_status: str,
        code: str,
        message: str,
    ) -> None:
        try:
            require_transition(job.status, target_status)
        except Exception:
            # If the job already reached a terminal state, keep it terminal and
            # only enrich the error fields rather than moving backwards.
            target_status = job.status
        now = self._clock()
        failed = job.with_fields(
            status=target_status,
            error_code=code,
            error_message=(message or "")[:4000],
            finished_at=now,
            updated_at=now,
        )
        self._save(failed)

    @staticmethod
    def _stage_failure(status: str) -> str:
        return {
            "pending": "crawl_failed",
            "crawling": "crawl_failed",
            "exporting": "export_failed",
            "verifying": "verify_failed",
            "importing": "import_failed",
        }.get(status, "crawl_failed")


class _ThreadBackgroundRunner:
    def submit(self, fn) -> None:
        thread = threading.Thread(target=fn, daemon=False)
        thread.start()


def _new_record(
    request: AcquisitionJobCreate,
    now: datetime,
) -> AcquisitionJobRecord:
    return AcquisitionJobRecord(
        id=str(uuid4()),
        requested_by=request.requested_by,
        source=request.source,
        keyword=request.keyword,
        city=request.city,
        pages=request.pages,
        status="pending",
        progress=0.0,
        crawler_task_id=None,
        bundle_id=None,
        bundle_file_name=None,
        bundle_hash=None,
        discovered_count=0,
        exported_count=0,
        imported_count=0,
        no_op_count=0,
        failed_count=0,
        import_batch_id=None,
        error_code=None,
        error_message=None,
        retry_of_id=request.retry_of_id,
        attempt=request.attempt,
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


__all__ = [
    "ACQUISITION_JOB_MANAGE",
    "ACQUISITION_READ",
    "AcquisitionBundleInvalid",
    "AcquisitionConflict",
    "AcquisitionCrawlFailed",
    "AcquisitionError",
    "AcquisitionExportFailed",
    "AcquisitionImportFailed",
    "AcquisitionJobNotFound",
    "AcquisitionJobPage",
    "AcquisitionLoginRequired",
    "AcquisitionSourceUnavailable",
    "AcquisitionTimeout",
    "AcquisitionUseCases",
]
