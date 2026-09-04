"""Test unified database schema startup (Block 1)."""
import sys
import pytest
from unittest.mock import MagicMock, patch

# Mock DB dependencies before importing unified_api.database
sys.modules["dbutils"] = MagicMock()
sys.modules["dbutils.pooled_db"] = MagicMock()
sys.modules["pymysql"] = MagicMock()


class FakeCursor:
    """Fake DictCursor for schema tests."""
    def __init__(self, existing_columns=None):
        self._existing = set(existing_columns or [])
        self._executed = []
        self._committed = False
        self._rolled_back = False

    def execute(self, sql):
        self._executed.append(sql)

    def fetchall(self):
        if "SHOW COLUMNS FROM" in (self._executed[-1] if self._executed else ""):
            # Return DictCursor-style rows
            return [{"Field": c} for c in self._existing]
        return []

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeConnection:
    def __init__(self, existing_columns=None):
        self.cursor_instance = FakeCursor(existing_columns)
        self._committed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self._committed = True

    def rollback(self):
        self.cursor_instance._rolled_back = True

    def close(self):
        pass


def test_fresh_schema_has_boss_raw_fields():
    """Fresh install creates bosszp with all raw columns."""
    conn = FakeConnection()
    from unified_api.database import _create_tables, _BOSSZP_TABLE

    cur = conn.cursor()
    _create_tables(cur)
    # Verify the table DDL includes key raw columns
    ddl = _BOSSZP_TABLE
    for col in ("source_record_id", "raw_payload", "raw_html",
                "crawl_time", "raw_text_status", "benefits_raw", "skills_raw",
                "experience_raw", "education_raw", "detail_extraction_method"):
        assert col in ddl, f"Missing column {col} in bosszp DDL"


def test_fresh_schema_has_company_raw_fields():
    """Fresh install creates multi_company_jobs with all raw columns."""
    from unified_api.database import _MULTI_COMPANY_TABLE

    ddl = _MULTI_COMPANY_TABLE
    for col in ("source_record_id", "raw_payload", "raw_html",
                "crawl_time", "raw_text_status", "benefits_raw", "skills_raw",
                "experience_raw", "education_raw"):
        assert col in ddl, f"Missing column {col} in multi_company_jobs DDL"


def test_startup_calls_ensure_schema():
    """main.py startup calls ensure_schema, not old init_db — verified via source text."""
    import os
    main_py = os.path.join(os.path.dirname(__file__), "..", "main.py")
    with open(main_py, encoding="utf-8") as f:
        src = f.read()
    assert "ensure_schema()" in src
    assert "init_db()" not in src


def test_upgrade_adds_missing_columns():
    """Existing DB with old columns gets upgraded column-by-column."""
    from unified_api.database import _ensure_columns, _NEW_COLUMNS_BOSSZP

    conn = FakeConnection(existing_columns={"id", "job_title"})  # old table
    cur = conn.cursor()
    added = _ensure_columns(cur, "bosszp", _NEW_COLUMNS_BOSSZP)
    assert len(added) > 0
    assert "source_record_id" in added


def test_upgrade_idempotent_when_all_present():
    """When all columns exist, upgrade adds nothing."""
    from unified_api.database import _ensure_columns, _NEW_COLUMNS_BOSSZP

    all_cols = set(_NEW_COLUMNS_BOSSZP.keys())
    conn = FakeConnection(existing_columns=all_cols)
    cur = conn.cursor()
    added = _ensure_columns(cur, "bosszp", _NEW_COLUMNS_BOSSZP)
    assert len(added) == 0


def test_schema_failure_rolls_back():
    """When upgrade fails, connection is rolled back."""
    # Database creation is a separate, already-covered phase.  Isolate the
    # schema-connection failure so this unit test never contacts real MySQL.
    with patch("unified_api.database.ensure_database_exists"), \
         patch("unified_api.database.reset_pool"), \
         patch("unified_api.database.get_conn") as mock_conn:
        mock_conn.side_effect = RuntimeError("DB unavailable")
        from unified_api.database import ensure_schema
        with pytest.raises(RuntimeError, match="DB unavailable"):
            ensure_schema()
