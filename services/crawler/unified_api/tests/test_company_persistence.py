"""Test Company persistence (task 02)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from unified_api.services.persistence import PersistenceResult
from unified_api.services.company_service import classify_persistence_error

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _disable_task_database(monkeypatch):
    monkeypatch.setattr(
        "unified_api.services.company_service.update_progress",
        lambda *_args, **_kwargs: None,
    )
    # These tests isolate the legacy row-persistence behavior. Offline staging
    # has its own contract tests with a cursor that supports reads.
    monkeypatch.setattr(
        "unified_api.services.company_service.ensure_export_candidate_in_transaction",
        lambda *_args, **_kwargs: "publication-test",
    )


class FakeCursor:
    def __init__(self):
        self.last_sql = ""
        self.last_params = None
        self.rowcount = 1
        self._stored = None
    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params
        if "INSERT INTO crawler_publications" in sql:
            self._stored = {
                "id": params[0],
                "source_kind": params[2],
                "envelope_payload": params[7],
            }
    def fetchone(self):
        return self._stored
    def close(self): pass

class FakeConn:
    def __init__(self, fail_on=None):
        self.cursor_instance = FakeCursor()
        self._fail_on = fail_on or set()
        self._call = 0
    def cursor(self): return self.cursor_instance
    def commit(self): self._call += 1
    def rollback(self): self.rolled_back = True
    def close(self): self.closed = True


class FakeJob:
    def __init__(self, **kw):
        self.company_name = kw.get("company_name", "TestCo")
        self.job_title = kw.get("job_title", "Engineer")
        self.source_platform = kw.get("source_platform", "test")
        self.job_id = kw.get("job_id", "rec-001")
        self.salary_min = 0
        self.salary_max = 0
        self.experience_raw = ""
        self.education_raw = ""
        self.jd_text = "test jd"
        self.jd_responsibility = ""
        self.jd_requirement = ""
        self.skill_tags = ""
        self.city = ""
        self.district = ""
        self.source_url = ""
        self.publish_date = ""
        self.raw_payload = kw.get("raw_payload", {})
        self.raw_html = ""
        self.crawl_time = kw.get("crawl_time", "2026-07-20T14:30:00+00:00")
        self.text_canonicalization_version = "v1"
        self.source_version = "1"
        self.raw_text_status = "completed"
        self.raw_text_error = ""
        self.benefits_raw = ""
        self.skills_raw = ""
        self.experience = ""
        self.education = ""


class FakeCollector:
    def __init__(self, jobs):
        self._jobs = jobs


class TestCompanyPersistence:
    def test_empty_source_record_id_rejected(self):
        from unified_api.services.company_service import _save_jobs_to_db
        job = FakeJob(job_id="")
        collector = FakeCollector([job])
        with patch("unified_api.services.company_service.get_conn") as get_conn:
            results = _save_jobs_to_db(collector, "task-1")
        assert len(results) == 1
        assert results[0].status == "failed"
        assert results[0].error_code == "source_record_id_missing"
        get_conn.assert_not_called()

    def test_two_success_one_failure(self):
        from unified_api.services.company_service import _save_jobs_to_db
        j1 = FakeJob(job_id="r1")
        j2 = FakeJob(job_id="r2")
        j3 = FakeJob(job_id="r3", crawl_time="bad-time")
        collector = FakeCollector([j1, j2, j3])
        with patch("unified_api.services.company_service.get_conn", return_value=FakeConn()):
            results = _save_jobs_to_db(collector, "task-1")
        assert len(results) == 3
        saved = [r for r in results if r.status == "saved"]
        failed = [r for r in results if r.status == "failed"]
        assert len(saved) == 2
        assert len(failed) == 1
        assert failed[0].error_code == "crawl_time_invalid"

    def test_crawl_time_naive_fails(self):
        from unified_api.services.company_service import _save_jobs_to_db
        job = FakeJob(job_id="r1", crawl_time="2026-07-20T14:30:00")
        collector = FakeCollector([job])
        with patch("unified_api.services.company_service.get_conn", return_value=FakeConn()):
            results = _save_jobs_to_db(collector, "task-1")
        assert results[0].status == "failed"

    def test_json_serialization_failure(self):
        from unified_api.services.company_service import _save_jobs_to_db
        import json as _json
        original = _json.dumps
        _json.dumps = MagicMock(side_effect=TypeError("bad"))
        try:
            job = FakeJob(job_id="r1", raw_payload={"value": "forces serialization"})
            collector = FakeCollector([job])
            with patch("unified_api.services.company_service.get_conn", return_value=FakeConn()):
                results = _save_jobs_to_db(collector, "task-1")
            assert results[0].status == "failed"
        finally:
            _json.dumps = original

    def test_execute_failure_rollback(self):
        from unified_api.services.company_service import _save_jobs_to_db
        conn = FakeConn()
        conn.cursor_instance.execute = MagicMock(side_effect=RuntimeError("DB error"))
        job = FakeJob(job_id="r1")
        collector = FakeCollector([job])
        with patch("unified_api.services.company_service.get_conn", return_value=conn):
            results = _save_jobs_to_db(collector, "task-1")
        assert results[0].status == "failed"

    def test_scraper_import_failures_visible(self):
        from unified_api.services.company_service import _setup_company_dispatcher
        with patch("importlib.import_module", side_effect=ImportError("no playwright")):
            with pytest.raises(RuntimeError, match="no company scrapers"):
                _setup_company_dispatcher()

    def test_no_except_pass_in_core_path(self):
        import inspect
        from unified_api.services import company_service
        src = inspect.getsource(company_service._setup_company_dispatcher)
        assert "except Exception:" not in src or "pass" not in src.split("except Exception:")[1][:50]
