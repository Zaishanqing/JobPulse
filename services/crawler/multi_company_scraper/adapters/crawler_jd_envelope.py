"""将采集层内部 JobData 转换为仓库级共享契约 CrawlerJDEnvelopeV1。

所有爬虫必须通过此适配器输出 Envelope，不得在各 scraper 中分别拼装。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.source_identity import parse_crawl_time

from multi_company_scraper.models.job_data import JobData

# ---------------------------------------------------------------------------
# crawl_time helpers
# ---------------------------------------------------------------------------


def _parse_crawl_time(job: JobData) -> datetime:
    """Parse crawl_time → timezone-aware UTC datetime.

    Delegates to shared ``jobgraph_contracts.parse_crawl_time``.
    Never falls back to ``datetime.now()``.
    """
    return parse_crawl_time(job.crawl_time)


# ---------------------------------------------------------------------------
# Envelope construction
# ---------------------------------------------------------------------------


def job_data_to_envelope(job: JobData) -> CrawlerJDEnvelopeV1:
    """Convert a crawler-internal :class:`JobData` to a shared Envelope.

    Raises:
        ValueError: When ``jd_text`` is empty, ``raw_text_status`` is not
            ``completed``, the computed hash does not match, or any required
            identity field is missing / invalid.
    """
    if job.raw_text_status != "completed":
        raise ValueError(
            f"job_data_to_envelope: raw_text_status={job.raw_text_status!r} "
            f"for {job.source_platform!r}:{job.job_id!r}"
        )
    if not job.jd_text or not job.jd_text.strip():
        raise ValueError(
            f"job_data_to_envelope: jd_text is empty for "
            f"{job.source_platform!r}:{job.job_id!r}"
        )
    if not job.job_id or not job.job_id.strip():
        raise ValueError(
            "job_data_to_envelope: source_record_id (job_id) is missing"
        )

    return CrawlerJDEnvelopeV1(
        source_record_id=job.job_id.strip(),
        source_platform=job.source_platform,
        source_url=job.source_url or None,
        job_title_raw=job.job_title or None,
        company_name_raw=job.company_name or None,
        region_raw=(job.city + (" " + job.district if job.district else "")).strip() or None,
        publish_time_raw=job.publish_date or None,
        crawl_time=_parse_crawl_time(job),
        raw_text=job.jd_text,
        raw_payload=job.raw_payload,
        raw_html=job.raw_html or None,
        text_canonicalization_version=job.text_canonicalization_version or "v1",
        source_version=job.source_version,
    )


# ---------------------------------------------------------------------------
# Batch export with explicit per-item results
# ---------------------------------------------------------------------------


class EnvelopeExportItemResult:
    """Single item result in a batch envelope export."""

    __slots__ = (
        "source_platform", "source_record_id", "status",
        "envelope", "error_code", "error_message",
    )

    def __init__(
        self,
        *,
        source_platform: str,
        source_record_id: str,
        status: str,  # "success" | "failed"
        envelope: CrawlerJDEnvelopeV1 | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.source_platform = source_platform
        self.source_record_id = source_record_id
        self.status = status
        self.envelope = envelope
        self.error_code = error_code
        self.error_message = error_message

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "source_platform": self.source_platform,
            "source_record_id": self.source_record_id,
            "status": self.status,
        }
        if self.envelope is not None:
            d["envelope"] = self.envelope.model_dump(mode="json")
        if self.error_code is not None:
            d["error_code"] = self.error_code
        if self.error_message is not None:
            d["error_message"] = self.error_message
        return d


def batch_to_envelopes(
    jobs: list[JobData],
) -> tuple[list[CrawlerJDEnvelopeV1], list[EnvelopeExportItemResult]]:
    """Convert a batch of :class:`JobData` → (successes, failures).

    Every input item appears in exactly one of the two returned lists.
    Failures carry a stable ``error_code`` for API consumers.
    """
    successes: list[CrawlerJDEnvelopeV1] = []
    failures: list[EnvelopeExportItemResult] = []

    for job in jobs:
        platform = job.source_platform or ""
        rid = job.job_id or ""
        try:
            if job.raw_text_status != "completed":
                raise ValueError("raw_text_unavailable")
            successes.append(job_data_to_envelope(job))
        except ValueError as exc:
            msg = str(exc)
            if "raw_text_status" in msg or "raw_text_unavailable" in msg:
                code = "raw_text_unavailable"
            elif "jd_text is empty" in msg:
                code = "raw_text_empty"
            elif "source_record_id" in msg or "job_id" in msg:
                code = "source_record_id_missing"
            elif "crawl_time" in msg:
                code = "crawl_time_invalid"
            else:
                code = "contract_validation_failed"
            failures.append(EnvelopeExportItemResult(
                source_platform=platform,
                source_record_id=rid,
                status="failed",
                error_code=code,
                error_message=msg,
            ))

    return successes, failures
