# JobPulse 主后端

`apps/api/` 是 FastAPI 主后端和浏览器 BFF。它拥有主数据库、Alembic revision、账号/RBAC、
JD/CV 生命周期、企业招聘、审核治理、任务与跨服务集成。统一前端只调用它的 `/api/v1`；
Knowledge Graph、Emerging Discovery、Trend Intelligence 和 Matching 通过固定客户端接入，
不提供任意 URL 代理。

## 业务上下文

组合根位于 `app/bootstrap/container.py`。`app/contexts/` 当前包含：access、acquisition、
catalog、cv_ingestion、data_validation、discovery、emerging_positions、evaluation、
evidence_independence、evidence_rag、extraction_tasks、governance_feedback、insight_cards、
jd_lifecycle、knowledge_graph、market_intelligence、matching_learning、platform、
review_value_ranking、source_jds、talent_acquisition 和 tasks。

公开路由由 `app/api/v1/router.py` 统一注册，覆盖：

- 登录、账号、企业、招聘岗位与候选人；
- Source JD、JD 抽取/归一化/审核/发布；
- 简历上传、CV 抽取确认和已验证画像；
- 技能、岗位、图谱、演化、趋势和新兴岗位；
- 人岗匹配、Gap、What-if、Learning Path；
- Evidence、RAG、Insight Card、审核任务、反馈与评估治理；
- Evaluation Results、Evidence Decay、采集、任务与系统状态。

## 服务与数据边界

- 主后端只拥有主系统业务表；各独立服务拥有自己的数据库、迁移和事务。
- 跨服务只使用 HTTP、`jobgraph_contracts` 或 Outbox，不导入服务 ORM/Repository。
- JD 抽取必须显式选择 `rule` 或 `llm`，失败不跨 Provider 自动回退。
- CV 文件入口调用配置的 CV Extraction Provider；命名 Demo Snapshot 是独立数据集，不冒充文件抽取。
- 已发布 JD、ValidatedCVSnapshot、GraphVersion 和正式 Evaluation 通过版本/血缘关联，不能原地改写历史事实。
- Emerging 的发现运行、输入/算法快照、Candidate 生命周期与 Lineage 在发现服务内
  持久化，BFF 不在进程内重算。

## 本地运行

从 `apps/api/`：

```powershell
pip install -e ../../packages/contracts
pip install -e .
python -m alembic upgrade head
uvicorn app.main:app --reload
```

开发默认值只适合单进程检查。生产模式要求显式数据库、JWT、内部服务凭据和 Provider 配置。
健康端点为 `/health` 与 `/readiness`，OpenAPI 为 `/docs`。

Compose 见 [快速启动](../../docs/deployment/quickstart.md)。默认拓扑独立运行
Extraction、Validation 和 KG Outbox worker；`cv-extraction` profile 另启动独立 CV worker。

详细文档见 [主后端文档索引](docs/index.md) 和 [接口总览](docs/api-overview.md)。
