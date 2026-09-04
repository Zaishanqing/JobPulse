# JobPulse Compose 拓扑

本目录定义仓库的容器拓扑与构建边界。

## 当前内容

- `docker-compose.candidate.yml`：主系统、知识图谱、匹配、新兴发现、趋势与前端的生产角色
  一致性链，包含所有默认 worker 与 dispatcher。Embedding、Qdrant、向量索引、JD 抽取、
  CV 抽取与 CV worker 均为显式 profile；`full` profile 在一次 Compose 调用中启用上述
  profile 以及 crawler API、MySQL 和 scheduler；crawler 数据仍以经校验的 Bundle 跨边界交换。
- `production-parity.yaml`：默认与 profile 服务角色的经复核分母。
- `../.env.example`：非敏感本地插值默认值。JobPulse 本地
  `scripts/compose-common.ps1 -Layout JobPulse` 启动器（由 `scripts/compose-up.ps1` 使用）
  会将其复制到被忽略的 `infra/.env`，并在候选运行需要时填入仅本地使用的密钥。直接使用
  `docker compose` 的用户需要自行准备 `infra/.env`。
- `../docker/contexts.candidate.yaml`：逐镜像构建上下文与 Dockerfile 映射；所有构建上下文
  都位于本仓库内，且可访问 `packages/contracts/jobgraph_contracts`。

默认链包含 rule Extraction、Validation 与 KG Outbox worker。JD profile 增加 LLM provider
与 extraction worker；CV profile 同时增加其 provider 与独立主系统 CV worker；语义 profile
增加 Embedding、Qdrant、语义匹配 API 与 vector worker；`full` profile 是所有可选角色的
聚合入口。

## 真实图谱数据初始化

GraphVersion 位于命名卷 `knowledge_graph_postgres_data` 中，不属于 Git 产物。正常
bootstrap 只导入技能与岗位目录。将真实已发布 JD facts 恢复或导入 KG 数据库后，运行一次性
初始化器一次：

```powershell
scripts\compose-init-kg.ps1
```

该初始化器会构建并发布缺失的岗位图谱，同时保留已发布版本。它不属于普通启动流程。
`scripts\compose-up.ps1 -Full` 启动前会检查 KG 是否已有至少一个已发布岗位画像；
缺失时停止并提示先运行该初始化命令。当前检查不会强制指定 `BACKEND_ENGINEER`；
如需把某个岗位作为演示探针，可显式设置 `KG_REQUIRED_POSITION_CODE`（目前仅输出
诊断提示，不作为失败条件）。

直接使用 Docker Compose 时，显式启用 `kg-init` profile，并运行
`knowledge-graph-real-data-init` 或 `knowledge-graph-readiness-check`；不要把初始化服务加入
普通 `up` 命令。

## 契约

- 引用模块源的 Compose 文件必须使用以仓库根目录为根的上下文（或显式声明的外部数据/缓存路径）。
- 构建上下文不得依赖本仓库之外的源树。
- 构建上下文与 Dockerfile 的解析只在 `infra/docker/contexts.candidate.yaml` 与 JobPulse
  本地 Compose 启动器映射中定义一次，不允许通过推断唯一子目录来发现。
- `docker compose --project-directory infra --env-file infra/.env.example -f
  infra/compose/docker-compose.candidate.yml config --quiet` 是本拓扑的配置验证命令。
