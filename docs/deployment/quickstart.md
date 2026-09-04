# JobPulse 快速启动

本文只描述当前仓库实际存在的入口。

## 前置条件

- Docker Engine / Docker Desktop；
- Docker Compose 2.20.3 或更高版本；
- 从仓库根目录执行以下命令；
- 复制 `infra/.env.example` 后为标记为必填的密码、Token 和 taxonomy version 提供值。

## 配置与启动基础栈

```powershell
Copy-Item .\infra\.env.example .\infra\.env
docker compose --project-directory .\infra --env-file .\infra\.env -f .\infra\compose\docker-compose.candidate.yml config --quiet
docker compose --project-directory .\infra --env-file .\infra\.env -f .\infra\compose\docker-compose.candidate.yml up --build --detach --wait
```

首次或空卷启动时，`main-judge-data-init` 会幂等导入随附示例数据；`main-frontend`
会等待数据导入成功，但冷启动不会自动审核或发布新兴岗位。生产部署可在 `.env` 设置
`LOAD_PACKAGED_JUDGE_DATA=false` 禁用示例数据初始化。

基础栈包含：主 PostgreSQL、Knowledge Graph PostgreSQL/API/worker、Matching
PostgreSQL/Redis/API/worker/dispatcher、共享 Analytics PostgreSQL、Emerging Discovery、
Trend Intelligence API/worker、Embedding、主后端、Extraction/Validation/KG Outbox worker
和统一前端。

使用仓库提供的 `infra/.env.example` 时，对宿主机开放：

- 统一前端：`http://localhost:53000`；
- 主后端：`http://localhost:58000`，OpenAPI 位于 `/docs`；
- 主 PostgreSQL：`55433`；Analytics PostgreSQL：`55434`。

若不设置对应变量，Compose 文件自身的回退值是前端 `3000`、主后端 `8000`、主库
`5433` 和 Analytics `5434`。

Knowledge Graph、Emerging、Trend 和 Matching API 只在 Compose 网络内暴露。

## 可选服务

```powershell
# Qdrant + 语义 Matching API/vector worker（复用基础栈 BGE-M3 服务）
docker compose --project-directory .\infra --env-file .\infra\.env -f .\infra\compose\docker-compose.candidate.yml --profile semantic-demo up --build --detach

# JD LLM Extraction
docker compose --project-directory .\infra --env-file .\infra\.env -f .\infra\compose\docker-compose.candidate.yml --profile model-extraction up --build --detach

# CV LLM Extraction Provider + 独立 CV worker（同时设置 CV_EXTRACTION_ENABLED=true）
docker compose --project-directory .\infra --env-file .\infra\.env -f .\infra\compose\docker-compose.candidate.yml --profile cv-extraction up --build --detach

# 完整统一拓扑：JD/CV、语义和 crawler 一并启动
# 需要 DEEPSEEK_API_KEY、CV_EXTRACTION_ENABLED=true、ACQUISITION_ENABLED=true、模型缓存和站点凭据
docker compose --project-directory .\infra --env-file .\infra\.env -f .\infra\compose\docker-compose.candidate.yml --profile full up --build --detach --wait
```

基础栈常驻 Embedding 并使用持久模型缓存；`semantic-demo` 加入 Qdrant、语义 Matching API
和 vector worker。
JD/CV profile 会把 `DEEPSEEK_API_KEY` 与内部契约配置传入 Provider；默认 Extraction worker
处理 rule/LLM JD 任务，CV profile 另启动独立 CV worker。CV 上传链还要求在 `infra/.env` 中设置
`CV_EXTRACTION_ENABLED=true`。抽取失败不会跨 Provider 自动回退。

## 当前拓扑边界

生产角色分母由 `infra/compose/production-parity.yaml` 固定并受仓库测试约束。基础栈覆盖
默认异步链，模型/CV/语义链由上述 profile 成套启用。`full` 只是统一启用这些角色；
爬虫仍通过离线 Bundle 与主后端交换，JD 离线包也可通过 `python -m app.offline_import.cli`
从 `apps/api/` 导入。

停止服务（保留数据卷）：

```powershell
docker compose --project-directory .\infra --env-file .\infra\.env -f .\infra\compose\docker-compose.candidate.yml down
```

基础栈不要求数据库中已经存在已发布岗位图谱。Windows 可用
`.\scripts\compose-status.ps1` 检查在线服务；导入真实已发布 JD facts 并执行一次
`.\scripts\compose-init-kg.ps1` 后，再用 `.\scripts\compose-status.ps1 -RequirePublishedKg`
校验图谱业务就绪状态。

更多说明见 [部署边界](deployment.md) 和 [Compose 说明](../../infra/compose/README.md)。
