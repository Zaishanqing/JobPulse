from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.acquisition.ports.acquisition import AcquisitionStore
from app.acquisition.ports.connectors import ConnectorResolver


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CrawlService:
    def __init__(
        self,
        store: AcquisitionStore,
        *,
        registry: ConnectorResolver,
    ) -> None:
        self.store = store
        self.registry = registry

    def run_once(self, worker_id: str, *, lease_seconds: float = 60) -> bool:
        self.store.recover_expired_crawl_jobs(now=_utc_now())
        job = self.store.claim_crawl_job(
            worker_id,
            now=_utc_now(),
            lease=timedelta(seconds=lease_seconds),
        )
        if job is None:
            return False
        self.execute_job(str(job["id"]), already_claimed=True)
        return True

    def execute_job(self, job_id: str, *, already_claimed: bool = False) -> dict[str, object]:
        job = self.store.get_crawl_job(job_id)
        if job is None:
            raise LookupError(f"crawl job {job_id} not found")
        source = self.store.get_source(str(job["source_id"]))
        if source is None:
            raise LookupError(f"source {job['source_id']} not found")
        if not already_claimed and not self.store.mark_job_running(job_id):
            raise RuntimeError(f"crawl job {job_id} is not available for execution")
        connector = self.registry.resolve(str(source["source_type"]))
        if connector is None:
            self.store.mark_job_failed(
                job_id,
                f"no connector registered for source_type={source['source_type']}",
                retryable=False,
            )
            return self.store.get_crawl_job(job_id) or {}
        try:
            records = connector.fetch(
                source,
                job["window_start"],
                job["window_end"],
            )
            if not records:
                raise RuntimeError(
                    f"connector returned no records for source_type={source['source_type']}"
                )
            persisted = []
            for record in records:
                content = dict(record.raw_content)
                if not record.external_id or not content:
                    raise ValueError("connector returned a record without external_id or raw_content")
                persisted.append({
                    "external_id": record.external_id,
                    "raw_content": content,
                    "source_version": str(record.metadata.get("source_version") or "1"),
                    "content_type": record.content_type,
                    "captured_at": record.captured_at,
                    "metadata": dict(record.metadata),
                })
            self.store.complete_crawl_job(job_id, str(source["id"]), persisted)
        except Exception as exc:
            self.store.mark_job_failed(job_id, f"{type(exc).__name__}: {exc}", retryable=True)
        return self.store.get_crawl_job(job_id) or {}

    def generate_bundle(self, job_id: str, source_id: str, bundle_type: str, snapshot_ids: list[str]) -> dict[str, object]:
        return self.store.create_bundle_for_job(
            job_id=job_id,
            source_id=source_id,
            snapshot_ids=snapshot_ids,
            bundle_type=bundle_type,
        )
