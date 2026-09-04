# 主后端使用说明

本文描述 `apps/api/` 当前代码入口。完整候选拓扑见
[JobPulse 快速启动](../../../docs/deployment/quickstart.md)。

## 主后端职责

主后端是统一前端的唯一 API，负责账号/RBAC、企业招聘、Source JD、JD/CV 生命周期、
目录、审核治理、Evidence/RAG、Insight、任务以及对 KG、Emerging、Trend、Matching 的 BFF。
业务路由统一位于 `/api/v1`，OpenAPI 位于 `/docs`。

主后端不拥有独立服务的数据表，也不直接导入其内部模型。服务集成通过固定 HTTP 客户端、
`jobgraph_contracts` 和 Outbox 完成。

## 本地启动

从 `apps/api/` 执行：

```powershell
pip install -e ../../packages/contracts
pip install -e .
python -m alembic upgrade head
python scripts/seed_bootstrap_accounts.py
python scripts/sync_skill_taxonomy_catalog.py
python scripts/sync_position_taxonomy_catalog.py
uvicorn app.main:app --reload
```

生产环境必须提供 PostgreSQL `DATABASE_URL`、独立 `JWT_SECRET_KEY`，并为启用的内部服务
提供 URL、Token/签名密钥。`GET /readiness` 会返回依赖与关键配置状态；`GET
/api/v1/system/status` 仅供获授权角色查看更完整的配置投影。

## JD、CV 与 Data Validation

JD 任务显式选择 `extraction_mode=rule|llm`：rule 由主后端确定性 Provider 执行，llm 调用
配置的 JD Extraction 服务；失败不会跨模式回退。Source JD 离线 Bundle 使用：

```powershell
python -m app.offline_import.cli verify <bundle.zip>
python -m app.offline_import.cli import <bundle.zip>
python -m app.offline_import.cli history
```

`DATA_VALIDATION_MODE` 为 `off|observe|enforce`。`off` 不创建 ValidationTask；`observe`
异步记录结论但不阻止首次 Draft import；`enforce` 只接受与当前 policy binding/血缘一致的
成功 Snapshot，缺失或不一致时 fail closed。Catalog 摘要是当前内容的确定性标识，不是可恢复
的历史 Catalog snapshot。

CV 正式业务入口是文件上传、异步抽取、人工确认和 ValidatedCVSnapshot。命名 Demo Snapshot
是独立数据集，不是文件抽取 Provider。文件默认上限 10 MiB；CV 文本提取链支持 PDF、DOCX、
JPEG、PNG 和纯文本。通用文件层虽然识别旧 `.doc` MIME，但 CV extractor 不处理 `.doc`，
不能把“可上传”写成“可解析”。实际能力还取决于 parser/OCR/CV Extraction Provider。

## 独立服务开关

主后端默认配置允许在未连接外部服务时启动，但相应业务会明确 unavailable/disabled：

- `KNOWLEDGE_GRAPH_ENABLED` + KG URL/服务账号；
- `EMERGING_DISCOVERY_ENABLED` + URL/internal token；
- `TREND_INTELLIGENCE_ENABLED` + URL/internal token；
- `MATCHING_SERVICE_ENABLED` + URL/JWT/上游 service token；
- `CV_EXTRACTION_ENABLED` + URL/internal token/taxonomy version；
- JD LLM 由 `JD_EXTRACTION_BASE_URL` 与 internal token 配置；
- Evidence RAG 和语义归一化可调用 Embedding/Qdrant，但默认关闭。

配置项存在或 Provider 容器启动，不代表完整业务链已经编排；应以 readiness 和真实调用结果为准。

## Compose 的实际边界

`infra/compose/docker-compose.candidate.yml` 会启动主 API、四个核心独立服务，以及
`jobgraph-extraction-worker`、`jobgraph-validation-worker` 和 `jobgraph-outbox-worker`。
CV profile 同时启动 CV Provider 与 `jobgraph-cv-extraction-worker`；语义 profile 同时
启动 Embedding、Qdrant、语义 Matching API 和 vector worker。所有主后端 worker
共享主 PostgreSQL 与同一组服务契约配置，但使用独立进程便于扩缩容和故障隔离。
`full` profile 还启动 Crawler MySQL、API 与 scheduler，并把 Bundle 输出挂载给主后端的
离线导入链；它不会绕过 Bundle 校验直接跨库写入。

## 数据库与发布

主数据库结构只由 `apps/api/alembic/` 管理。启动时不使用 `create_all` 代替 Alembic。已发布
JD、ValidatedCVSnapshot 和跨服务结果保留版本/血缘；历史发布事实不可原地覆盖。

真实数据链为：

```text
SourceJD/Version -> ExtractionTask -> Validation/Review -> JD Publication
CV upload -> CV Extraction -> Review/Confirm -> ValidatedCVSnapshot
Published JD -> Knowledge Graph -> GraphVersion/Profile
Validated CV + Position/Enterprise Profile -> Matching -> Gap/What-if/Learning Path
```

具体接口以运行时 OpenAPI 和 [接口总览](api-overview.md) 为准。
