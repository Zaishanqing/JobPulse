"""Test Boss persistence results (task 02)."""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from unified_api.services.persistence import PersistenceResult


@pytest.fixture(autouse=True)
def _disable_task_database(monkeypatch):
    monkeypatch.setattr(
        "unified_api.services.boss_service.update_progress",
        lambda *_args, **_kwargs: None,
    )


def _fake_job_data(**overrides):
    data = {
        "encryptJobId": "enc123",
        "securityId": "sec456",
        "jobId": "job789",
        "jobName": "Python Engineer",
        "brandName": "Acme Corp",
        "salaryDesc": "20K-30K",
        "areaDistrict": "朝阳",
        "businessDistrict": "",
        "brandIndustry": "互联网",
        "brandStageName": "D轮及以上",
        "brandScaleName": "500-999人",
        "jobExperience": "3-5年",
        "jobDegree": "本科",
        "skills": ["Python", "Django"],
        "welfareList": ["五险一金", "年终奖"],
    }
    data.update(overrides)
    return data


class FakeCursor:
    def __init__(self):
        self.last_sql = ""
        self.rowcount = 1
    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params
    def close(self): pass


class FakeConn:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.committed = False
        self.rolled_back = False
    def cursor(self):
        return self.cursor_instance
    def commit(self):
        self.committed = True
    def rollback(self):
        self.rolled_back = True
    def close(self): pass


class TestBossPersistence:
    def test_missing_source_record_id(self):
        from unified_api.services.boss_service import _parse_and_save
        result = _parse_and_save(
            {"encryptJobId": "", "securityId": "", "jobId": ""},
            "kw", "bj", FakeConn(),
        )
        assert isinstance(result, PersistenceResult)
        assert result.status == "failed"
        assert result.error_code == "source_record_id_missing"

    def test_invalid_job_title(self):
        from unified_api.services.boss_service import _parse_and_save
        data = _fake_job_data(jobName="未知职位")
        result = _parse_and_save(data, "kw", "bj", FakeConn())
        assert isinstance(result, PersistenceResult)
        assert result.status == "failed"
        assert result.error_code == "invalid_list_record"

    def test_invalid_company_name(self):
        from unified_api.services.boss_service import _parse_and_save
        data = _fake_job_data(brandName="未知公司")
        result = _parse_and_save(data, "kw", "bj", FakeConn())
        assert isinstance(result, PersistenceResult)
        assert result.status == "failed"
        assert result.error_code == "invalid_list_record"

    def test_all_paths_return_persistence_result(self):
        from unified_api.services.boss_service import _parse_and_save
        # Test each code path returns PersistenceResult
        r1 = _parse_and_save({}, "kw", "bj", FakeConn())
        assert isinstance(r1, PersistenceResult)
        r2 = _parse_and_save(_fake_job_data(jobName="", brandName=""), "kw", "bj", FakeConn())
        assert isinstance(r2, PersistenceResult)
        r3 = _parse_and_save(_fake_job_data(), "kw", "bj", FakeConn())
        assert isinstance(r3, PersistenceResult)

    def test_db_execute_failure_rolls_back(self):
        from unified_api.services.boss_service import _parse_and_save
        conn = FakeConn()
        # Make execute raise
        conn.cursor_instance.execute = MagicMock(side_effect=RuntimeError("DB down"))
        data = _fake_job_data()
        result = _parse_and_save(data, "kw", "bj", conn)
        assert result.status == "failed"
        assert result.error_code == "database_write_error"

    def test_json_serialization_failure(self):
        from unified_api.services.boss_service import _parse_and_save
        conn = FakeConn()
        import json as _json
        original = _json.dumps
        _json.dumps = MagicMock(side_effect=TypeError("not serializable"))
        try:
            data = _fake_job_data()
            result = _parse_and_save(data, "kw", "bj", conn)
            assert result.status == "failed"
            assert result.error_code in ("raw_payload_serialization_failed", "database_write_error")
        finally:
            _json.dumps = original

    def test_no_detail_is_saved_as_unavailable_with_crawl_time(self):
        from unified_api.services.boss_service import _parse_and_save
        conn = FakeConn()
        result = _parse_and_save(_fake_job_data(), "kw", "bj", conn, detail_tab=None)
        assert result.status == "saved"
        params = conn.cursor_instance.last_params
        assert params[-3] == "unavailable"
        assert isinstance(params[-5], datetime)

    def test_failed_detail_is_persisted_as_failed(self):
        from unified_api.services.boss_detail import BossJobDetailResult
        from unified_api.services.boss_service import _parse_and_save

        detail = BossJobDetailResult(
            status="failed",
            source_url="https://www.zhipin.com/job_detail/enc123.html",
            error_code="detail_timeout",
            error_message="timeout",
            raw_payload={"list_payload": {}},
        )
        conn = FakeConn()
        with patch(
            "unified_api.services.boss_detail.fetch_boss_job_detail",
            return_value=detail,
        ):
            result = _parse_and_save(
                _fake_job_data(), "kw", "bj", conn, detail_tab=object()
            )
        assert result.status == "saved"
        assert conn.cursor_instance.last_params[-3] == "failed"
        assert conn.cursor_instance.last_params[-2] == "timeout"
