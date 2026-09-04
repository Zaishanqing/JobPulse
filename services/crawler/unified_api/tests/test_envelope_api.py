"""Test Envelope API endpoints (task 02)."""
import json
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest


class FakeRow(dict):
    def __init__(self, **kw):
        super().__init__(**kw)
    def __getattr__(self, k):
        return self.get(k)


def _make_completed_boss_row():
    return FakeRow(
        id=1,
        job_company="Acme",
        job_title="Python Engineer",
        source_record_id="enc123",
        source_url="https://www.zhipin.com/job_detail/enc123.html",
        company_city="北京",
        keyword="Python",
        job_desc="Python backend developer " * 10,
        benefits_raw="五险一金",
        experience_raw="3-5年",
        education_raw="本科",
        skills_raw="Python",
        raw_text_status="completed",
        raw_text_error="",
        text_canonicalization_version="v1",
        crawl_time=datetime(2026, 7, 20, 14, 30, 0),
        raw_payload=json.dumps({"test": 1}),
        raw_html="<html></html>",
    )


def _make_unavailable_boss_row():
    return FakeRow(
        id=2, job_company="Acme", job_title="Dev",
        source_record_id="enc456",
        company_city="北京", keyword="Java",
        job_desc="",
        raw_text_status="unavailable",
        raw_text_error="full JD detail not fetched",
        crawl_time=None,
    )


def _make_failed_boss_row():
    return FakeRow(
        id=3, job_company="Acme", job_title="DevOps",
        source_record_id="enc789",
        company_city="北京", keyword="DevOps",
        job_desc="",
        raw_text_status="failed",
        raw_text_error="detail_fetch_error",
        crawl_time=None,
    )


class TestBossRawAPI:
    def test_completed_returns_200(self):
        from unified_api.routers.boss_router import get_boss_job_raw
        row = _make_completed_boss_row()
        with patch("unified_api.routers.boss_router.get_boss_job_by_id",
                    return_value=row):
            from fastapi import HTTPException
            try:
                result = get_boss_job_raw(1, user={"id": 1})
                assert result is not None
            except HTTPException as e:
                pytest.fail(f"Expected 200, got {e.status_code}: {e.detail}")

    def test_unavailable_returns_422(self):
        from unified_api.routers.boss_router import get_boss_job_raw
        from fastapi import HTTPException
        with patch("unified_api.routers.boss_router.get_boss_job_by_id",
                    return_value=_make_unavailable_boss_row()):
            with pytest.raises(HTTPException) as exc:
                get_boss_job_raw(2, user={"id": 1})
            assert exc.value.status_code == 422

    def test_failed_returns_422(self):
        from unified_api.routers.boss_router import get_boss_job_raw
        from fastapi import HTTPException
        with patch("unified_api.routers.boss_router.get_boss_job_by_id",
                    return_value=_make_failed_boss_row()):
            with pytest.raises(HTTPException) as exc:
                get_boss_job_raw(3, user={"id": 1})
            assert exc.value.status_code == 422

    def test_not_found_returns_404(self):
        from unified_api.routers.boss_router import get_boss_job_raw
        from fastapi import HTTPException
        with patch("unified_api.routers.boss_router.get_boss_job_by_id",
                    return_value=None):
            with pytest.raises(HTTPException) as exc:
                get_boss_job_raw(999, user={"id": 1})
            assert exc.value.status_code == 404


class TestEnvelopeExportRequest:
    def test_limit_zero_rejected(self):
        from unified_api.schemas.job import EnvelopeExportRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            EnvelopeExportRequest(limit=0)

    def test_limit_over_100_rejected(self):
        from unified_api.schemas.job import EnvelopeExportRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            EnvelopeExportRequest(limit=101)

    def test_limit_100_ok(self):
        from unified_api.schemas.job import EnvelopeExportRequest
        req = EnvelopeExportRequest(limit=100)
        assert req.limit == 100


class TestBatchExportCounts:
    def test_processed_equals_exported_plus_failed(self):
        # Verify the export functions return correct structure
        from unified_api.services.boss_service import export_boss_envelopes
        # Mock the DB to return 3 rows: 1 completed, 1 unavailable, 1 failed
        rows = [_make_completed_boss_row(), _make_unavailable_boss_row(), _make_failed_boss_row()]
        with patch("unified_api.services.boss_service.get_conn") as mock_conn:
            mock_cur = MagicMock()
            mock_cur.fetchall.return_value = rows
            mock_conn.return_value.cursor.return_value = mock_cur

            result = export_boss_envelopes(limit=100)
            assert result["exported_count"] + result["failed_count"] == result["processed_count"]
            assert result["requested_count"] == len(rows)
            # All rows appear in items
            assert len(result["items"]) == result["exported_count"] + result["failed_count"]
