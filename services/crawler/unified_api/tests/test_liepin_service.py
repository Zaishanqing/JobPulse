"""Test Liepin service persistence (task 02)."""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from unified_api.services.persistence import PersistenceResult

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _disable_task_database(monkeypatch):
    monkeypatch.setattr(
        "unified_api.services.liepin_service.update_progress",
        lambda *_args, **_kwargs: None,
    )
    # These tests isolate the legacy row-persistence behavior. Offline staging
    # has its own contract tests with a cursor that supports reads.
    monkeypatch.setattr(
        "unified_api.services.liepin_service.ensure_export_candidate_in_transaction",
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
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.committed = False; self.rolled_back = False; self.closed = False
    def cursor(self): return self.cursor_instance
    def commit(self): self.committed = True
    def rollback(self): self.rolled_back = True
    def close(self): self.closed = True

class FakeJob:
    def __init__(self, **kw):
        self.company_name = kw.get("company_name", "猎聘")
        self.job_title = kw.get("job_title", "Engineer")
        self.source_platform = "liepin"
        self.job_id = kw.get("job_id", "liepin-rec-001")
        self.salary_min = 0; self.salary_max = 0
        self.experience_raw = ""; self.education_raw = ""
        self.jd_text = "test jd"
        self.jd_responsibility = ""; self.jd_requirement = ""
        self.skill_tags = ""; self.city = ""; self.district = ""
        self.source_url = ""; self.publish_date = ""
        self.raw_payload = kw.get("raw_payload", {}); self.raw_html = ""
        self.crawl_time = kw.get("crawl_time", "2026-07-20T14:30:00+00:00")
        self.text_canonicalization_version = "v1"
        self.source_version = "1"
        self.raw_text_status = "completed"; self.raw_text_error = ""
        self.benefits_raw = ""; self.skills_raw = ""
        self.experience = ""; self.education = ""


class TestLiepinPersistence:
    def test_empty_source_record_id_rejected(self):
        from unified_api.services.liepin_service import _save_liepin_jobs
        job = FakeJob(job_id="")
        with patch("unified_api.services.liepin_service.get_conn") as get_conn:
            results = _save_liepin_jobs([job], "task-1")
        assert len(results) == 1
        assert results[0].status == "failed"
        assert results[0].error_code == "source_record_id_missing"
        get_conn.assert_not_called()

    def test_normal_save(self):
        from unified_api.services.liepin_service import _save_liepin_jobs
        job = FakeJob(job_id="r1")
        with patch("unified_api.services.liepin_service.get_conn", return_value=FakeConn()):
            results = _save_liepin_jobs([job], "task-1")
        assert results[0].status == "saved"

    def test_connection_and_cursor_are_closed(self):
        from unified_api.services.liepin_service import _save_liepin_jobs
        conn = FakeConn()
        with patch("unified_api.services.liepin_service.get_conn", return_value=conn):
            results = _save_liepin_jobs([FakeJob(job_id="r1")], "task-1")
        assert results[0].status == "saved"
        assert conn.closed is True

    def test_crawl_time_naive_fails(self):
        from unified_api.services.liepin_service import _save_liepin_jobs
        job = FakeJob(job_id="r1", crawl_time="2026-07-20T14:30:00")
        results = _save_liepin_jobs([job], "task-1")
        assert results[0].status == "failed"
        assert results[0].error_code == "crawl_time_invalid"

    def test_json_serialization_fails(self):
        from unified_api.services.liepin_service import _save_liepin_jobs
        job = FakeJob(job_id="r1", raw_payload={"bad": object()})
        with patch("unified_api.services.liepin_service.get_conn", return_value=FakeConn()):
            results = _save_liepin_jobs([job], "task-1")
        assert results[0].status == "failed"
        assert results[0].error_code == "raw_payload_serialization_failed"

    def test_execute_failure_rollback(self):
        from unified_api.services.liepin_service import _save_liepin_jobs
        conn = FakeConn()
        conn.cursor_instance.execute = MagicMock(side_effect=RuntimeError("DB error"))
        job = FakeJob(job_id="r1")
        with patch("unified_api.services.liepin_service.get_conn", return_value=conn):
            results = _save_liepin_jobs([job], "task-1")
        assert results[0].status == "failed"
        assert results[0].error_code == "database_write_error"

    def test_all_results_are_persistence_result(self):
        from unified_api.services.liepin_service import _save_liepin_jobs
        j1 = FakeJob(job_id="r1")
        j2 = FakeJob(job_id="")
        with patch("unified_api.services.liepin_service.get_conn", return_value=FakeConn()):
            results = _save_liepin_jobs([j1, j2], "task-1")
        for r in results:
            assert isinstance(r, PersistenceResult)

    def test_config_loads_independent_of_cwd(self):
        from unified_api.services.liepin_service import _LIEPIN_CONFIG_PATH
        assert _LIEPIN_CONFIG_PATH.exists()
        assert _LIEPIN_CONFIG_PATH.name == "liepin_search_params.yaml"

    def test_no_root_no_sys_path(self):
        import inspect
        from unified_api.services import liepin_service
        src = inspect.getsource(liepin_service)
        for line in src.split('\n'):
            s = line.strip()
            if s.startswith('#') or s.startswith('"""') or s.startswith("'''"):
                continue
            if 'os.path.join(ROOT' in s or s.startswith('ROOT ='):
                pytest.fail(f"legacy ROOT variable: {s}")
            if 'sys.path' in s:
                pytest.fail(f"sys.path: {s}")
