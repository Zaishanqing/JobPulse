# 新兴岗位发现服务

`services/emerging-discovery/` 拥有新兴岗位发现的运行编排、输入/算法快照、Cluster、
Candidate Observation/Transition、Identity、Admission、Lifecycle 与 Lineage。当前正式
算法为 EMERGE v3.2，每个观察窗是独立事务；后继 Run 从服务 PostgreSQL 读取已提交
历史，不由 BFF 在内存中拼接状态。最终判定由 knowledge-graph 内部
`/api/v1/integrations/emergence/v3.2/evaluate` 接口唯一执行。

FastAPI 入口为 `app.main:app`，健康端点为 `/health` 与 `/readiness`。数据库迁移由本目录
`alembic.ini` 和 `alembic/` 管理。正式环境使用 PostgreSQL；当前 Compose 通过独立
`emerging_discovery` database 运行 migrate 与 API。

本地安装先执行 `pip install -e ../../packages/contracts`，再执行 `pip install -e .`。

岗位参考可通过配置的 Knowledge Graph HTTP Provider 读取。主后端负责候选治理投影、人工
定义、发布和晋升，但不重算服务内生命周期。接口与时间语义见
[主后端集成说明](../../apps/api/docs/emerging-discovery.md)。
