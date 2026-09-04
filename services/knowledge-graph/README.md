# 知识图谱服务

`services/knowledge-graph/` 是岗位能力图谱事实与发布版本的所有者。它保存已发布 JD 结构化
事实副本、Evidence、目录消费快照、构建任务/运行、关系审核、不可变 GraphVersion、
Position Profile、演化事件和审计记录；不拥有账号、JD 业务生命周期或 CV。

FastAPI 入口为 `app.main:app`，API 位于 `/api/v1`，健康端点为 `/health` 与 `/readiness`。
数据库结构由本目录 `alembic.ini` 和 `alembic/` 管理；异步构建 worker 为
`python scripts/run_build_worker.py`。当前 Compose 会依次运行 migrate、目录/bootstrap、API
和 build worker。

本地安装先执行 `pip install -e ../../packages/contracts`，再执行 `pip install -e .`。

主后端通过服务账号、版本化 HTTP DTO 和显式实体映射调用本服务。集成模式下
`CATALOG_WRITES_ENABLED=false`，技能与岗位目录由主系统同步；服务之间不共享数据库 Session。
详细契约见 [系统集成契约](../../docs/architecture/knowledge-graph-contract.md)。
