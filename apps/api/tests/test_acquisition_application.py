from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.infrastructure.acquisition import SqlAlchemyAcquisitionRepository
from app.models.acquisition_job import AcquisitionJob as AcquisitionJobRow

from app.contexts.acquisition.application import (
    AcquisitionLoginRequired,
    AcquisitionSourceUnavailable,
    AcquisitionUseCases,
)
from app.contexts.acquisition.domain import AcquisitionJobRecord
from app.contexts.acquisition.ports import (
    BossLoginStatus,
    BundleRef,
    CrawlerSourceStatus,
    CrawlerTaskRef,
    CrawlerTaskStatus,
    LiepinLoginStatus,
)
from app.domain.accounts import AccountActor
from app.offline_import.contracts import (
    BundleImportConflict,
    BundleVerificationError,
    ImportSummary,
)
from tests.offline_bundle_test_support import envelope, make_bundle


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class FakeRepository:
    def __init__(self) -> None:
        self._records: dict[str, AcquisitionJobRecord] = {}
        self._claim_lock = threading.Lock()

    def add(self, record: AcquisitionJobRecord) -> None:
        self._records[record.id] = record

    def get(self, job_id: str) -> AcquisitionJobRecord | None:
        return self._records.get(job_id)

    def claim_pending(self, job_id: str, now: datetime) -> AcquisitionJobRecord | None:
        with self._claim_lock:
            record = self._records.get(job_id)
            if record is None or record.status != "pending":
                return None
            claimed = record.with_fields(
                status="crawling",
                started_at=now,
                progress=0.1,
                updated_at=now,
            )
            self._records[job_id] = claimed
            return claimed

    def list(self, *, status=None, source=None, offset=0, limit=20):
        values = [item for item in self._records.values()]
        if status is not None:
            values = [item for item in values if item.status == status]
        if source is not None:
            values = [item for item in values if item.source == source]
        values.sort(key=lambda item: str(item.created_at or ""), reverse=True)
        return values[offset : offset + limit], len(values)

    def save(self, record: AcquisitionJobRecord) -> None:
        self._records[record.id] = record

    def recover_stale(self, now, stale_after_seconds):
        return 0


class ContendedRepository(FakeRepository):
    def __init__(self, barrier: threading.Barrier) -> None:
        super().__init__()
        self._barrier = barrier

    def claim_pending(self, job_id: str, now: datetime) -> AcquisitionJobRecord | None:
        self._barrier.wait(timeout=5)
        return super().claim_pending(job_id, now)


class FakeCrawlerGateway:
    def __init__(
        self,
        *,
        task_status: CrawlerTaskStatus | None = None,
        start_error: Exception | None = None,
        list_error: Exception | None = None,
        export_error: Exception | None = None,
        bundle: BundleRef | None = None,
        sources: list[CrawlerSourceStatus] | None = None,
    ) -> None:
        self.task_status = task_status or CrawlerTaskStatus(
            "task-1", "completed", result_count=3
        )
        self.start_error = start_error
        self.list_error = list_error
        self.export_error = export_error
        self.bundle = bundle or BundleRef(
            "bundle-1", "bundle.zip", 3, "hash"
        )
        self.sources = sources or []
        self.started: list[dict] = []

    def list_sources(self) -> list[CrawlerSourceStatus]:
        if self.list_error is not None:
            raise self.list_error
        return self.sources

    def get_boss_login_status(self) -> BossLoginStatus:
        return BossLoginStatus(
            logged_in=True,
            cookie_count=3,
            running=False,
            status="succeeded",
            login_id="login-1",
        )

    def get_liepin_login_status(self) -> LiepinLoginStatus:
        return LiepinLoginStatus(
            logged_in=False,
            cookie_count=0,
            running=False,
            status="idle",
        )

    def start_crawl(self, *, source, keyword, city, pages):
        if self.start_error is not None:
            raise self.start_error
        self.started.append(
            {"source": source, "keyword": keyword, "city": city, "pages": pages}
        )
        return CrawlerTaskRef("task-1")

    def get_task(self, task_id: str) -> CrawlerTaskStatus:
        return self.task_status

    def export_bundle(self, *, task_id: str, source: str) -> BundleRef:
        if self.export_error is not None:
            raise self.export_error
        return self.bundle


class FakeBundleStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def resolve(self, bundle: BundleRef) -> Path:
        return self._path


class FakeImporter:
    def __init__(self, summary: ImportSummary | None = None, error: Exception | None = None) -> None:
        self.summary = summary or ImportSummary(
            batch_id="batch-1",
            bundle_id="bundle-1",
            record_count=3,
            imported_count=2,
            skipped_count=1,
            failed_count=0,
            status="completed",
        )
        self.error = error
        self.calls = 0

    def import_bundle(self, path, *, allow_gap=False, retry=False) -> ImportSummary:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.summary


class FakeUnitOfWork:
    def __init__(self, repo: FakeRepository) -> None:
        self.acquisition = repo
        self._committed = False

    def __enter__(self) -> "FakeUnitOfWork":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is not None:
            self.rollback()

    def commit(self) -> None:
        self._committed = True

    def rollback(self) -> None:
        pass


class ImmediateRunner:
    def __init__(self) -> None:
        self.jobs: list[str] = []

    def submit(self, fn) -> None:
        fn()


def _use_cases(tmp_path, *, gateway=None, importer=None, bundle_path=None):
    repo = FakeRepository()
    runner = ImmediateRunner()
    if bundle_path is None:
        bundle_path = make_bundle(
            tmp_path / "bundle.zip",
            bundle_id="bundle-1",
            envelopes=[envelope("one", "text")],
        )
    if gateway is None:
        gateway = FakeCrawlerGateway()
    if importer is None:
        importer = FakeImporter()
    use_cases = AcquisitionUseCases(
        lambda: FakeUnitOfWork(repo),
        gateway,
        FakeBundleStore(bundle_path),
        importer,
        runner,
        poll_interval_seconds=0.01,
        timeout_seconds=1,
        stale_after_seconds=3600,
        clock=lambda: utc("2026-08-17T00:00:00+00:00"),
        sleeper=lambda _: None,
    )
    return use_cases, repo


def test_boss_login_status(tmp_path):
    use_cases, _ = _use_cases(tmp_path)
    status = use_cases.get_boss_login_status(AccountActor("user-1", "admin"))
    assert status.logged_in is True
    assert status.cookie_count == 3


def test_feishu_create_does_not_require_keyword_or_city(tmp_path):
    use_cases, repo = _use_cases(tmp_path)
    record = use_cases.create(
        AccountActor("user-1", "admin"),
        source="feishu",
        keyword="",
        city="",
        pages=1,
    )
    current = repo.get(record.id)
    assert current.source == "feishu"
    assert current.keyword == "all"
    assert current.city == "全国"


def test_non_feishu_create_requires_keyword_and_city(tmp_path):
    use_cases, _ = _use_cases(tmp_path)
    with pytest.raises(ValueError):
        use_cases.create(
            AccountActor("user-1", "admin"),
            source="boss",
            keyword="",
            city="北京",
            pages=1,
        )


def test_list_sources_fallback_includes_feishu(tmp_path):
    gateway = FakeCrawlerGateway(
        list_error=AcquisitionSourceUnavailable("crawler down")
    )
    use_cases, _ = _use_cases(tmp_path, gateway=gateway)
    sources = use_cases.list_sources(AccountActor("user-1", "admin"))
    assert {source.source for source in sources} == {"boss", "liepin", "feishu"}
    assert all(source.available is False for source in sources)


def test_happy_path(tmp_path):
    use_cases, repo = _use_cases(tmp_path)
    record = use_cases.create(
        AccountActor("user-1", "admin"), source="boss", keyword="Java", city="北京", pages=2
    )
    current = repo.get(record.id)
    assert current is not None
    assert current.status == "completed"
    assert current.discovered_count == 3
    assert current.exported_count == 3
    assert current.imported_count == 2
    assert current.no_op_count == 1
    assert current.failed_count == 0
    assert current.bundle_id == "bundle-1"
    assert current.crawler_task_id == "task-1"
    assert current.bundle_hash is not None
    assert current.import_batch_id == "batch-1"


def test_source_unavailable(tmp_path):
    gateway = FakeCrawlerGateway(
        start_error=AcquisitionSourceUnavailable("source down")
    )
    use_cases, repo = _use_cases(tmp_path, gateway=gateway)
    record = use_cases.create(
        AccountActor("user-1", "admin"), source="boss", keyword="Java", city="北京", pages=1
    )
    current = repo.get(record.id)
    assert current.status == "crawl_failed"
    assert current.error_code == "ACQUISITION_SOURCE_UNAVAILABLE"


def test_login_required(tmp_path):
    gateway = FakeCrawlerGateway(start_error=AcquisitionLoginRequired("login"))
    use_cases, repo = _use_cases(tmp_path, gateway=gateway)
    record = use_cases.create(
        AccountActor("user-1", "admin"), source="boss", keyword="Java", city="北京", pages=1
    )
    current = repo.get(record.id)
    assert current.status == "crawl_failed"
    assert current.error_code == "ACQUISITION_LOGIN_REQUIRED"


def test_crawl_failure(tmp_path):
    gateway = FakeCrawlerGateway(
        task_status=CrawlerTaskStatus("task-1", "failed", error_message="boom")
    )
    use_cases, repo = _use_cases(tmp_path, gateway=gateway)
    record = use_cases.create(
        AccountActor("user-1", "admin"), source="boss", keyword="Java", city="北京", pages=1
    )
    current = repo.get(record.id)
    assert current.status == "crawl_failed"
    assert current.error_code == "ACQUISITION_CRAWL_FAILED"
    assert current.error_message == "boom"


def test_export_failure(tmp_path):
    gateway = FakeCrawlerGateway(export_error=RuntimeError("export boom"))
    use_cases, repo = _use_cases(tmp_path, gateway=gateway)
    record = use_cases.create(
        AccountActor("user-1", "admin"), source="boss", keyword="Java", city="北京", pages=1
    )
    current = repo.get(record.id)
    assert current.status == "export_failed"
    assert current.error_code == "ACQUISITION_EXPORT_FAILED"


def test_verify_failure(tmp_path):
    invalid_bundle = tmp_path / "bad.zip"
    invalid_bundle.write_bytes(b"not-a-real-zip")
    use_cases, repo = _use_cases(tmp_path, bundle_path=invalid_bundle)
    record = use_cases.create(
        AccountActor("user-1", "admin"), source="boss", keyword="Java", city="北京", pages=1
    )
    current = repo.get(record.id)
    assert current.status == "verify_failed"
    assert current.error_code == "ACQUISITION_BUNDLE_INVALID"


def test_importer_bundle_verification_error_maps_to_import_failed(tmp_path):
    importer = FakeImporter(error=BundleVerificationError("bad hash during importer"))
    use_cases, repo = _use_cases(tmp_path, importer=importer)
    record = use_cases.create(
        AccountActor("user-1", "admin"), source="boss", keyword="Java", city="北京", pages=1
    )
    current = repo.get(record.id)
    assert current.status == "import_failed"
    assert current.error_code == "ACQUISITION_IMPORT_FAILED"


def test_import_conflict_maps_to_import_failed(tmp_path):
    importer = FakeImporter(error=BundleImportConflict("explicit retry required"))
    use_cases, repo = _use_cases(tmp_path, importer=importer)
    record = use_cases.create(
        AccountActor("user-1", "admin"), source="boss", keyword="Java", city="北京", pages=1
    )
    current = repo.get(record.id)
    assert current.status == "import_failed"
    assert current.error_code == "ACQUISITION_IMPORT_FAILED"


def test_import_failure(tmp_path):
    importer = FakeImporter(error=RuntimeError("import boom"))
    use_cases, repo = _use_cases(tmp_path, importer=importer)
    record = use_cases.create(
        AccountActor("user-1", "admin"), source="boss", keyword="Java", city="北京", pages=1
    )
    current = repo.get(record.id)
    assert current.status == "import_failed"
    assert current.error_code == "ACQUISITION_INTERNAL"


def test_duplicate_no_op_import(tmp_path):
    importer = FakeImporter(
        ImportSummary(
            batch_id="batch-1",
            bundle_id="bundle-1",
            record_count=2,
            imported_count=0,
            skipped_count=2,
            failed_count=0,
            status="completed",
            no_op=True,
        )
    )
    use_cases, repo = _use_cases(tmp_path, importer=importer)
    record = use_cases.create(
        AccountActor("user-1", "admin"), source="boss", keyword="Java", city="北京", pages=1
    )
    current = repo.get(record.id)
    assert current.status == "completed"
    assert current.imported_count == 0
    assert current.no_op_count == 2
    assert current.failed_count == 0


def test_retry_creates_new_lineage(tmp_path):
    gateway = FakeCrawlerGateway(
        task_status=CrawlerTaskStatus("task-1", "failed", error_message="boom")
    )
    use_cases, repo = _use_cases(tmp_path, gateway=gateway)
    first = use_cases.create(
        AccountActor("user-1", "admin"), source="boss", keyword="Java", city="北京", pages=1
    )
    first_record = repo.get(first.id)
    assert first_record.status == "crawl_failed"

    gateway.task_status = CrawlerTaskStatus("task-1", "completed", result_count=1)
    second = use_cases.retry(AccountActor("user-1", "admin"), first.id)
    second_record = repo.get(second.id)
    assert second_record is not None
    assert second_record.retry_of_id == first.id
    assert second_record.attempt == 2
    assert second_record.status == "completed"
    assert repo.get(first.id).status == "crawl_failed"


def _pending_record() -> AcquisitionJobRecord:
    now = utc("2026-08-17T00:00:00+00:00")
    return AcquisitionJobRecord(
        id="job-claim-1",
        requested_by="user-1",
        source="boss",
        keyword="Java",
        city="北京",
        pages=1,
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
        retry_of_id=None,
        attempt=1,
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def test_two_workers_only_one_claims_pending(tmp_path):
    barrier = threading.Barrier(2)
    repo = ContendedRepository(barrier)
    repo.add(_pending_record())
    gateway = FakeCrawlerGateway()
    use_cases = AcquisitionUseCases(
        lambda: FakeUnitOfWork(repo),
        gateway,
        FakeBundleStore(make_bundle(
            tmp_path / "bundle.zip",
            bundle_id="bundle-1",
            envelopes=[envelope("one", "text")],
        )),
        FakeImporter(),
        ImmediateRunner(),
        poll_interval_seconds=0.01,
        timeout_seconds=1,
        stale_after_seconds=3600,
        clock=lambda: utc("2026-08-17T00:00:00+00:00"),
        sleeper=lambda _: None,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(use_cases.run, "job-claim-1") for _ in range(2)]
        for future in futures:
            future.result(timeout=5)

    assert len(gateway.started) == 1
    assert repo.get("job-claim-1").status == "completed"


def test_sqlalchemy_claim_pending_uses_conditional_update(tmp_path):
    import app.models  # noqa: F401

    engine = create_engine(f"sqlite:///{(tmp_path / 'acq-cas.db').as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    record = _pending_record()
    with Session() as session:
        SqlAlchemyAcquisitionRepository(session).add(record)
        session.commit()

    with Session() as session:
        first = SqlAlchemyAcquisitionRepository(session).claim_pending(
            record.id,
            utc("2026-08-17T00:01:00+00:00"),
        )
        session.commit()
    with Session() as session:
        second = SqlAlchemyAcquisitionRepository(session).claim_pending(
            record.id,
            utc("2026-08-17T00:02:00+00:00"),
        )
        session.commit()

    assert first is not None
    assert second is None
    with Session() as session:
        row = session.get(AcquisitionJobRow, record.id)
        assert row is not None
        assert row.status == "crawling"
        assert row.started_at is not None
