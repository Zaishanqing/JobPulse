"""Test crawl_time MySQL round-trip → Envelope export (task 02)."""
import json
from datetime import datetime, timezone

import pytest

UTC = timezone.utc


class TestBossCompletedEnvelope:
    def test_boss_completed_row_exports_envelope(self):
        """MySQL naive UTC datetime → restore tz → envelope export succeeds."""
        from multi_company_scraper.models.job_data import JobData
        from multi_company_scraper.adapters.crawler_jd_envelope import job_data_to_envelope
        from unified_api.services.boss_service import _row_to_job_data

        jd_text = "Python backend developer responsible for API design and database modeling " * 3

        # Simulate a Boss completed row with MySQL naive UTC datetime
        row = {
            "job_company": "Acme",
            "job_title": "Python Engineer",
            "source_record_id": "enc123",
            "source_url": "https://www.zhipin.com/job_detail/enc123.html",
            "company_city": "北京",
            "keyword": "Python",
            "job_desc": jd_text,
            "benefits_raw": "五险一金",
            "experience_raw": "3-5年",
            "education_raw": "本科",
            "skills_raw": "Python,Django",
            "raw_text_status": "completed",
            "raw_text_error": "",
            "text_canonicalization_version": "v1",
            "crawl_time": datetime(2026, 7, 20, 14, 30, 0),  # naive = UTC in MySQL
        }

        job = _row_to_job_data(row, "boss_zhipin")
        assert job.raw_text_status == "completed"
        assert job.jd_text == jd_text
        # crawl_time should be restored as ISO with timezone
        assert "+00:00" in job.crawl_time or "Z" in job.crawl_time or job.crawl_time.endswith("+00:00")

        envelope = job_data_to_envelope(job)
        assert envelope.schema_version == "crawler-jd-v1"
        assert envelope.raw_text == jd_text


class TestCompanyCompletedEnvelope:
    def test_company_completed_row_exports_envelope(self):
        from multi_company_scraper.adapters.crawler_jd_envelope import job_data_to_envelope
        from unified_api.services.company_service import _row_to_job_data

        jd_text = "Go backend engineer " * 5
        row = {
            "company_name": "TestCo",
            "job_title": "Go Engineer",
            "source_platform": "moka",
            "source_record_id": "rec-moka-1",
            "location": "上海",
            "source_url": "https://example.com/job/1",
            "jd_text": jd_text,
            "crawl_time": datetime(2026, 7, 20, 14, 30, 0),
            "text_canonicalization_version": "v1",
            "raw_text_status": "completed",
            "raw_text_error": "",
            "benefits_raw": "",
            "experience_raw": "",
            "education_raw": "",
            "skills_raw": "",
            "raw_payload": None,
            "raw_html": "",
        }
        job = _row_to_job_data(row)
        envelope = job_data_to_envelope(job)
        assert envelope.schema_version == "crawler-jd-v1"


class TestCrawlTimeMissing:
    def test_missing_crawl_time_fails_export(self):
        from multi_company_scraper.adapters.crawler_jd_envelope import job_data_to_envelope
        from multi_company_scraper.models.job_data import JobData

        jd = "test " * 5
        job = JobData(
            company_name="c", job_title="t", source_platform="p",
            job_id="r1", jd_text=jd,
            raw_text_status="completed", crawl_time="",
        )
        with pytest.raises(ValueError, match="crawl_time"):
            job_data_to_envelope(job)


class TestNoCreatedAtFallback:
    def test_row_to_job_data_does_not_use_created_at(self):
        import inspect
        from unified_api.services.company_service import _row_to_job_data
        src = inspect.getsource(_row_to_job_data)
        assert "created_at" not in src

    def test_no_now_in_row_reader(self):
        import inspect
        from unified_api.services.company_service import _row_to_job_data
        src = inspect.getsource(_row_to_job_data)
        assert "datetime.now" not in src
