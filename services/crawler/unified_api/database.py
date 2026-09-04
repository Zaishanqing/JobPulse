"""MySQL 连接池管理 + 统一 schema (task 02 final).

``ensure_schema()`` 是唯一权威数据库结构入口：
- fresh install: 创建包含全部 raw 字段的当前表
- existing DB: 逐列补齐缺失字段
- 升级失败: 阻止应用启动 (raise, 不吞异常)
"""

from dbutils.pooled_db import PooledDB
import pymysql
from .config import DB_CONFIG

_pool: PooledDB | None = None

# ---------------------------------------------------------------------------
# 连接池
# ---------------------------------------------------------------------------


def get_pool() -> PooledDB:
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=10,
            mincached=2,
            maxcached=5,
            blocking=True,
            host=DB_CONFIG['host'],
            port=DB_CONFIG['port'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            database=DB_CONFIG['database'],
            charset=DB_CONFIG['charset'],
            cursorclass=pymysql.cursors.DictCursor,
        )
    return _pool


def get_conn():
    return get_pool().connection()


# ---------------------------------------------------------------------------
# 当前权威表结构 (与 init_database.py 保持完全一致)
# ---------------------------------------------------------------------------

_BOSSZP_TABLE = """
    CREATE TABLE IF NOT EXISTS bosszp (
        id INT AUTO_INCREMENT PRIMARY KEY,
        job_title VARCHAR(256),
        job_salary VARCHAR(64),
        job_lable VARCHAR(512),
        job_company VARCHAR(256),
        job_url VARCHAR(512),
        job_company_tag VARCHAR(256),
        job_desc TEXT,
        job_acquire VARCHAR(256),
        company_city VARCHAR(64),
        job_skill VARCHAR(512),
        keyword VARCHAR(64),
        user_id INT DEFAULT 1,
        task_id VARCHAR(32),
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        source_record_id VARCHAR(128),
        source_url VARCHAR(1024),
        raw_payload JSON,
        raw_html LONGTEXT,
        raw_text_status VARCHAR(32) DEFAULT '',
        raw_text_error TEXT,
        source_version VARCHAR(64),
        crawl_time DATETIME,
        text_canonicalization_version VARCHAR(32),
        benefits_raw VARCHAR(512),
        skills_raw VARCHAR(512),
        experience_raw VARCHAR(128),
        education_raw VARCHAR(128),
        detail_extraction_method VARCHAR(32),
        UNIQUE KEY uk_job (job_title, job_company, company_city, keyword)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_MULTI_COMPANY_TABLE = """
    CREATE TABLE IF NOT EXISTS multi_company_jobs (
        id INT AUTO_INCREMENT PRIMARY KEY,
        company_name VARCHAR(64) NOT NULL,
        platform VARCHAR(32),
        job_title VARCHAR(256),
        salary_min INT DEFAULT 0,
        salary_max INT DEFAULT 0,
        experience VARCHAR(32),
        education VARCHAR(32),
        jd_text TEXT,
        jd_responsibility TEXT,
        jd_requirement TEXT,
        skill_tags VARCHAR(512),
        location VARCHAR(64),
        source_url VARCHAR(512),
        source_platform VARCHAR(32) DEFAULT 'company',
        task_id VARCHAR(32),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        source_record_id VARCHAR(128),
        raw_payload JSON,
        raw_html LONGTEXT,
        raw_text_status VARCHAR(32) DEFAULT '',
        raw_text_error TEXT,
        source_version VARCHAR(64),
        crawl_time DATETIME,
        text_canonicalization_version VARCHAR(32),
        benefits_raw VARCHAR(512),
        skills_raw VARCHAR(512),
        experience_raw VARCHAR(128),
        education_raw VARCHAR(128),
        INDEX idx_company (company_name),
        INDEX idx_platform (platform),
        INDEX idx_source (source_platform)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CRAWL_TASKS_TABLE = """
    CREATE TABLE IF NOT EXISTS crawl_tasks (
        id VARCHAR(32) PRIMARY KEY,
        user_id INT NOT NULL,
        task_type VARCHAR(32) NOT NULL,
        params JSON,
        status VARCHAR(16) DEFAULT 'pending',
        progress VARCHAR(256),
        result_count INT DEFAULT 0,
        error_message TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        completed_at DATETIME,
        INDEX idx_user (user_id),
        INDEX idx_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_USERS_TABLE = """
    CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(64) NOT NULL UNIQUE,
        password_hash VARCHAR(256) NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CRAWLER_PUBLICATIONS_TABLE = """
    CREATE TABLE IF NOT EXISTS crawler_publications (
        id VARCHAR(36) PRIMARY KEY,
        idempotency_key VARCHAR(128) NOT NULL,
        source_kind VARCHAR(32) NOT NULL,
        source_job_id VARCHAR(64) NOT NULL,
        source_platform VARCHAR(64) NOT NULL,
        source_record_id VARCHAR(256) NOT NULL,
        source_version VARCHAR(64) NOT NULL,
        envelope_payload JSON NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        attempt_count INT NOT NULL DEFAULT 0,
        max_attempts INT NOT NULL,
        last_error_code VARCHAR(96),
        last_error_message VARCHAR(512),
        source_jd_id VARCHAR(36),
        source_jd_version_id VARCHAR(36),
        extraction_task_id VARCHAR(36),
        claimed_by VARCHAR(120),
        lease_expires_at DATETIME,
        heartbeat_at DATETIME,
        next_attempt_at DATETIME,
        finished_at DATETIME,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_crawler_publications_idempotency (idempotency_key),
        INDEX idx_crawler_publications_status (status),
        INDEX idx_crawler_publications_claim (status, next_attempt_at, created_at),
        INDEX idx_crawler_publications_lease (lease_expires_at),
        CONSTRAINT ck_crawler_publications_status
            CHECK (status IN ('pending', 'delivering', 'succeeded', 'failed')),
        CONSTRAINT ck_crawler_publications_attempts
            CHECK (attempt_count >= 0 AND attempt_count <= max_attempts)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CRAWLER_EXPORT_BATCHES_TABLE = """
    CREATE TABLE IF NOT EXISTS crawler_export_batches (
        id VARCHAR(36) PRIMARY KEY,
        bundle_id VARCHAR(128) NOT NULL,
        bundle_schema_version VARCHAR(64) NOT NULL,
        record_schema_version VARCHAR(64) NOT NULL,
        mode VARCHAR(32) NOT NULL,
        parent_bundle_id VARCHAR(128),
        record_count INT NOT NULL DEFAULT 0,
        file_name VARCHAR(255),
        status VARCHAR(32) NOT NULL,
        created_at DATETIME NOT NULL,
        completed_at DATETIME,
        last_error TEXT,
        UNIQUE KEY uk_crawler_export_batches_bundle_id (bundle_id),
        INDEX idx_crawler_export_batches_status (status),
        CONSTRAINT ck_crawler_export_batches_mode
            CHECK (mode IN ('incremental', 'full')),
        CONSTRAINT ck_crawler_export_batches_status
            CHECK (status IN ('building', 'completed', 'failed')),
        CONSTRAINT ck_crawler_export_batches_record_count
            CHECK (record_count >= 0)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CRAWLER_EXPORT_MEMBERS_TABLE = """
    CREATE TABLE IF NOT EXISTS crawler_export_members (
        id VARCHAR(36) PRIMARY KEY,
        batch_id VARCHAR(36) NOT NULL,
        publication_id VARCHAR(36) NOT NULL,
        source_platform VARCHAR(64) NOT NULL,
        source_record_id VARCHAR(255) NOT NULL,
        source_version VARCHAR(64) NOT NULL,
        line_number INT NOT NULL,
        created_at DATETIME NOT NULL,
        UNIQUE KEY uk_crawler_export_member_publication (batch_id, publication_id),
        UNIQUE KEY uk_crawler_export_member_line (batch_id, line_number),
        INDEX idx_crawler_export_members_publication (publication_id),
        CONSTRAINT fk_crawler_export_members_batch
            FOREIGN KEY (batch_id) REFERENCES crawler_export_batches(id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_crawler_export_members_publication
            FOREIGN KEY (publication_id) REFERENCES crawler_publications(id)
            ON DELETE RESTRICT,
        CONSTRAINT ck_crawler_export_members_line CHECK (line_number > 0)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CRAWLER_TASK_PUBLICATIONS_TABLE = """
    CREATE TABLE IF NOT EXISTS crawler_task_publications (
        id VARCHAR(36) PRIMARY KEY,
        task_id VARCHAR(64) NOT NULL,
        publication_id VARCHAR(36) NOT NULL,
        observed_at DATETIME NOT NULL,
        UNIQUE KEY uk_crawler_task_publication (task_id, publication_id),
        INDEX idx_crawler_task_publications_task (task_id),
        INDEX idx_crawler_task_publications_publication (publication_id),
        CONSTRAINT fk_crawler_task_publications_publication
            FOREIGN KEY (publication_id) REFERENCES crawler_publications(id)
            ON DELETE RESTRICT,
        CONSTRAINT ck_crawler_task_publications_task CHECK (task_id <> '')
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# ---------------------------------------------------------------------------
# 升级字段定义 (与 init_database.py 完全一致)
# ---------------------------------------------------------------------------

_NEW_COLUMNS_BOSSZP = {
    "source_record_id": "VARCHAR(128)",
    "source_url": "VARCHAR(1024)",
    "raw_payload": "JSON",
    "raw_html": "LONGTEXT",
    "raw_text_status": "VARCHAR(32) DEFAULT ''",
    "raw_text_error": "TEXT",
    "source_version": "VARCHAR(64)",
    "crawl_time": "DATETIME",
    "text_canonicalization_version": "VARCHAR(32)",
    "benefits_raw": "VARCHAR(512)",
    "skills_raw": "VARCHAR(512)",
    "experience_raw": "VARCHAR(128)",
    "education_raw": "VARCHAR(128)",
    "detail_extraction_method": "VARCHAR(32)",
}

_NEW_COLUMNS_MULTI_COMPANY = {
    "source_record_id": "VARCHAR(128)",
    "raw_payload": "JSON",
    "raw_html": "LONGTEXT",
    "raw_text_status": "VARCHAR(32) DEFAULT ''",
    "raw_text_error": "TEXT",
    "source_version": "VARCHAR(64)",
    "crawl_time": "DATETIME",
    "text_canonicalization_version": "VARCHAR(32)",
    "benefits_raw": "VARCHAR(512)",
    "skills_raw": "VARCHAR(512)",
    "experience_raw": "VARCHAR(128)",
    "education_raw": "VARCHAR(128)",
}

_NEW_COLUMNS_CRAWLER_PUBLICATIONS = {
    "claimed_by": "VARCHAR(120)",
    "lease_expires_at": "DATETIME",
    "heartbeat_at": "DATETIME",
    "next_attempt_at": "DATETIME",
    "finished_at": "DATETIME",
}


def _load_existing_columns(cursor, table: str) -> set[str]:
    """Return column names, compatible with tuple and DictCursor."""
    cursor.execute(f"SHOW COLUMNS FROM `{table}`")
    columns = set()
    for row in cursor.fetchall():
        if isinstance(row, dict):
            columns.add(row["Field"])
        else:
            columns.add(row[0])
    return columns


def _ensure_columns(cursor, table: str, columns: dict[str, str]) -> list[str]:
    existing = _load_existing_columns(cursor, table)
    added = []
    for name, ddl in columns.items():
        if name in existing:
            continue
        cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN `{name}` {ddl}")
        added.append(name)
    return added


def _load_existing_indexes(cursor, table: str) -> set[str]:
    cursor.execute(f"SHOW INDEX FROM `{table}`")
    indexes = set()
    for row in cursor.fetchall():
        indexes.add(row["Key_name"] if isinstance(row, dict) else row[2])
    return indexes


def _create_tables(cursor):
    for ddl in (
        _USERS_TABLE,
        _BOSSZP_TABLE,
        _MULTI_COMPANY_TABLE,
        _CRAWL_TASKS_TABLE,
        _CRAWLER_PUBLICATIONS_TABLE,
        _CRAWLER_EXPORT_BATCHES_TABLE,
        _CRAWLER_EXPORT_MEMBERS_TABLE,
        _CRAWLER_TASK_PUBLICATIONS_TABLE,
    ):
        cursor.execute(ddl)


def _upgrade_existing_tables(cursor):
    """逐列升级，DDL 失败 raise（不吞异常）。"""
    for table, columns in (
        ("bosszp", _NEW_COLUMNS_BOSSZP),
        ("multi_company_jobs", _NEW_COLUMNS_MULTI_COMPANY),
        ("crawler_publications", _NEW_COLUMNS_CRAWLER_PUBLICATIONS),
    ):
        added = _ensure_columns(cursor, table, columns)
        if added:
            print(f"[upgrade] {table}: added {len(added)} column(s): {', '.join(added)}")
        else:
            print(f"[upgrade] {table}: all columns already present")
    publication_indexes = _load_existing_indexes(cursor, "crawler_publications")
    if "idx_crawler_publications_claim" not in publication_indexes:
        cursor.execute(
            "ALTER TABLE crawler_publications ADD INDEX "
            "idx_crawler_publications_claim (status, next_attempt_at, created_at)"
        )
    if "idx_crawler_publications_lease" not in publication_indexes:
        cursor.execute(
            "ALTER TABLE crawler_publications ADD INDEX "
            "idx_crawler_publications_lease (lease_expires_at)"
        )

    # 历史数据标记 — DML 失败也应阻断
    cursor.execute("""
        UPDATE bosszp
        SET raw_text_status = 'unavailable',
            raw_text_error = 'historical record: source data unverifiable'
        WHERE (raw_text_status = '' OR raw_text_status IS NULL)
          AND (raw_text_error = '' OR raw_text_error IS NULL)
    """)
    cursor.execute("""
        UPDATE multi_company_jobs
        SET raw_text_status = 'unavailable',
            raw_text_error = 'historical record: source data unverifiable'
        WHERE (raw_text_status = '' OR raw_text_status IS NULL)
          AND (raw_text_error = '' OR raw_text_error IS NULL)
    """)


def ensure_database_exists() -> None:
    """Create the database if it does not exist (no database param in connection)."""
    conn = pymysql.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        charset=DB_CONFIG["charset"],
        autocommit=True,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DB_CONFIG['database']}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        conn.close()


def reset_pool() -> None:
    """Close and reset the connection pool so it reconnects after DB creation."""
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        finally:
            _pool = None


def ensure_schema():
    """创建数据库 → 创建表 → 升级旧库。任一失败 raise，阻止应用启动。"""
    ensure_database_exists()
    reset_pool()
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        _create_tables(cur)
        conn.commit()
        _upgrade_existing_tables(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# 兼容旧调用
# ---------------------------------------------------------------------------


def init_db():
    """Deprecated alias — delegates to ensure_schema()."""
    ensure_schema()
