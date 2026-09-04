"""数据库初始化入口 — 委托 unified_api.database.ensure_schema()。

所有 CREATE TABLE / ALTER TABLE 逻辑统一在 unified_api/database.py 维护。
"""

from unified_api.database import ensure_schema

if __name__ == "__main__":
    ensure_schema()
    print("Database schema is ready.")
