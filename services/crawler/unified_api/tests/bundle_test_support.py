from __future__ import annotations

from datetime import datetime, timezone

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.offline_bundle import BundleMode

from unified_api.offline_export.contracts import ExportBatchRecord


def envelope(number: int, text: str | None = None) -> CrawlerJDEnvelopeV1:
    raw_text = text or f"job description {number}"
    return CrawlerJDEnvelopeV1(
        source_record_id=f"record-{number}",
        source_platform="test",
        source_url=f"https://example.test/jobs/{number}",
        job_title_raw=f"Job {number}",
        company_name_raw="NFBS",
        region_raw="Local",
        crawl_time=datetime(2026, 7, 26, 8, number, tzinfo=timezone.utc),
        raw_text=raw_text,
        raw_payload={"number": number},
        text_canonicalization_version="v1",
    )


def records(*numbers: int) -> list[ExportBatchRecord]:
    return [
        ExportBatchRecord(f"publication-{number}", envelope(number))
        for number in numbers
    ]


class FakeExportRepository:
    def __init__(self, values: list[ExportBatchRecord]) -> None:
        self.records = values
        self.batches: dict[str, dict[str, object]] = {}
        self.completed_publications: set[str] = set()
        self.fail_completion = False
        self.task_publications: dict[str, set[str]] = {}

    def associate(self, task_id: str, publication_ids: list[str]) -> None:
        self.task_publications.setdefault(task_id, set()).update(publication_ids)

    def create_batch(
        self,
        *,
        bundle_id: str,
        mode: BundleMode,
        parent_bundle_id: str | None,
    ) -> str:
        if any(item["bundle_id"] == bundle_id for item in self.batches.values()):
            raise RuntimeError("duplicate bundle ID")
        batch_id = f"batch-{len(self.batches) + 1}"
        self.batches[batch_id] = {
            "bundle_id": bundle_id,
            "mode": mode,
            "parent_bundle_id": parent_bundle_id,
            "status": "building",
        }
        return batch_id

    def list_records(
        self, *, mode: BundleMode, limit: int | None, task_id: str | None = None
    ) -> list[ExportBatchRecord]:
        values = self.records
        if task_id is not None:
            allowed = self.task_publications.get(task_id, set())
            values = [
                item for item in values if item.publication_id in allowed
            ]
        if mode is BundleMode.INCREMENTAL:
            values = [
                item
                for item in values
                if item.publication_id not in self.completed_publications
            ]
        return list(values[:limit] if limit is not None else values)

    def latest_completed_bundle_id(self) -> str | None:
        completed = [
            item
            for item in self.batches.values()
            if item["status"] == "completed"
        ]
        return str(completed[-1]["bundle_id"]) if completed else None

    def is_completed_bundle(self, bundle_id: str) -> bool:
        return any(
            item["bundle_id"] == bundle_id and item["status"] == "completed"
            for item in self.batches.values()
        )

    def complete_batch(
        self,
        *,
        batch_id: str,
        records: list[ExportBatchRecord],
        file_name: str,
    ) -> None:
        if self.fail_completion:
            raise RuntimeError("completion failed")
        batch = self.batches[batch_id]
        batch.update(
            {
                "status": "completed",
                "file_name": file_name,
                "record_count": len(records),
            }
        )
        self.completed_publications.update(
            item.publication_id for item in records
        )

    def fail_batch(self, batch_id: str, error: str) -> None:
        self.batches[batch_id].update({"status": "failed", "error": error})
