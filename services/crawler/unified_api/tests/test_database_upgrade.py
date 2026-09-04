"""Test database upgrade cursor compatibility + fail-fast (Block 2)."""
import sys
import pytest
from unittest.mock import MagicMock

# Mock DB dependencies before importing unified_api.database
sys.modules["dbutils"] = MagicMock()
sys.modules["dbutils.pooled_db"] = MagicMock()
sys.modules["pymysql"] = MagicMock()


class FakeTupleCursor:
    def __init__(self, existing=None):
        self._existing = list(existing or [])
        self._executed = []
    def execute(self, sql):
        self._executed.append(sql)
    def fetchall(self):
        if "SHOW COLUMNS" in (self._executed[-1] if self._executed else ""):
            return [(c, "varchar(128)", "YES", "", None, "") for c in self._existing]
        return []
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass


class FakeDictCursor:
    def __init__(self, existing=None):
        self._existing = list(existing or [])
        self._executed = []
    def execute(self, sql):
        self._executed.append(sql)
    def fetchall(self):
        if "SHOW COLUMNS" in (self._executed[-1] if self._executed else ""):
            return [{"Field": c} for c in self._existing]
        return []
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass


class FakeConn:
    def __init__(self, cursor_cls, existing=None):
        self.cursor_instance = cursor_cls(existing)
    def cursor(self):
        return self.cursor_instance
    def commit(self): pass
    def rollback(self): pass
    def close(self): pass


def test_tuple_cursor_reads_columns():
    from unified_api.database import _load_existing_columns
    cur = FakeTupleCursor(existing=["id", "job_title"])
    cols = _load_existing_columns(cur, "test_table")
    assert cols == {"id", "job_title"}


def test_dict_cursor_reads_columns():
    from unified_api.database import _load_existing_columns
    cur = FakeDictCursor(existing=["id", "job_title"])
    cols = _load_existing_columns(cur, "test_table")
    assert cols == {"id", "job_title"}


def test_all_present_no_alter():
    from unified_api.database import _ensure_columns, _NEW_COLUMNS_BOSSZP
    all_cols = set(_NEW_COLUMNS_BOSSZP.keys())
    cur = FakeDictCursor(existing=list(all_cols))
    added = _ensure_columns(cur, "bosszp", _NEW_COLUMNS_BOSSZP)
    assert len(added) == 0


def test_one_missing_adds_one():
    from unified_api.database import _ensure_columns, _NEW_COLUMNS_BOSSZP
    cur = FakeDictCursor(existing=["id"])
    added = _ensure_columns(cur, "bosszp", _NEW_COLUMNS_BOSSZP)
    assert len(added) == len(_NEW_COLUMNS_BOSSZP)
    assert "source_record_id" in added


def test_idempotent():
    from unified_api.database import _ensure_columns, _NEW_COLUMNS_MULTI_COMPANY
    all_cols = set(_NEW_COLUMNS_MULTI_COMPANY.keys())
    cur = FakeTupleCursor(existing=list(all_cols))
    a1 = _ensure_columns(cur, "multi_company_jobs", _NEW_COLUMNS_MULTI_COMPANY)
    assert len(a1) == 0


def test_alter_failure_raises():
    """DDL failure should raise, not be swallowed."""
    from unified_api.database import _ensure_columns, _NEW_COLUMNS_BOSSZP
    cur = FakeDictCursor(existing=["id"])
    # Make execute raise on ALTER
    original = cur.execute
    def fail_on_alter(sql):
        if "ALTER TABLE" in sql:
            raise RuntimeError("DDL failed")
        return original(sql)
    cur.execute = fail_on_alter
    with pytest.raises(RuntimeError, match="DDL failed"):
        _ensure_columns(cur, "bosszp", _NEW_COLUMNS_BOSSZP)


def test_upgrade_dml_failure_raises():
    """Historical UPDATE failures should not be swallowed."""
    from unittest.mock import patch
    with patch("unified_api.database.get_conn") as mock_conn:
        cur = FakeDictCursor(existing=list({"source_record_id", "raw_text_status"}))
        conn = MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        from unified_api.database import _upgrade_existing_tables
        # The _ensure_columns returns 0 (all present), then UPDATE runs
        # Make UPDATE raise
        cur._executed = []
        def fail_update(sql):
            if "UPDATE" in sql:
                raise RuntimeError("UPDATE failed")
        cur.execute = fail_update
        with pytest.raises(RuntimeError, match="UPDATE failed"):
            _upgrade_existing_tables(cur)
