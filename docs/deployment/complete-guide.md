# JobPulse 完整部署手册

> 适用范围：当前仓库的 Candidate Compose 拓扑。本文所有命令均从仓库根目录执行，
> 并以 `infra/compose/docker-compose.candidate.yml` 为唯一 Compose 入口。

## 1. 部署模式

| 模式 | 适用场景 | 入口 | 主要前置条件 |
| --- | --- | --- | --- |
| 最小基础栈 | 首次验证、无外部 LLM 凭据环境 | 直接运行基础 Compose | BGE-M3；关闭 CV、采集和 Responsibility CE |
| 标准 Windows | 日常集成、正式发现能力 | `scripts/compose-up.ps1` | BGE-M3 缓存或允许首次联网下载 |
| Evidence | 证据问答 | `scripts/compose-up.ps1 -Evidence` | `DEEPSEEK_API_KEY`、BGE-M3 |
| Full | JD/CV、语义、采集全部启用 | `scripts/compose-up.ps1 -Full` | DeepSeek、CV、采集、模型、站点凭据、已发布 KG |
| 离线 | 无网络演示或交付 | `/offline` 与离线导入脚本 | 预先导出的镜像包和模型包 |

`full` 只是启用所有可选容器角色，不会绕过模型、真实数据、审核、发布或站点授权要求。

## 2. 系统要求

- Docker Engine 24 或更高版本；
- Docker Compose 2.20.3 或更高版本；
- Windows 推荐 Docker Desktop 与 PowerShell 5.1/7；
- Linux/macOS 使用 Docker Engine 与 Compose v2；
- 首次使用语义能力需要约 2.3 GB 的 BGE-M3 权重，并为镜像、数据库和构建缓存预留额外空间；
- GPU 不是基础栈的必需条件，CPU 可以运行基础模式和默认规则匹配。

检查环境：

```powershell
docker version
docker compose version
git status --short --branch
```

## 3. 目录和配置

关键文件：

```text
infra/.env.example                         环境变量模板
infra/.env                                 本机配置，Git 忽略
infra/compose/docker-compose.candidate.yml 唯一 Compose 入口
infra/compose/production-parity.yaml       服务角色分母
scripts/compose-*.ps1                      Windows 运维脚本
config/semantic-demo-contract.env          语义模型固定版本契约
```

首次配置：

```powershell
Copy-Item .\infra\.env.example .\infra\.env
```

`infra/.env` 只能保存于目标机器，不得提交。模板中的密码和 Token 是本地候选默认值，
正式部署必须替换为独立强随机值。

### 3.1 所有部署都要检查的变量

| 变量 | 说明 | 正式环境要求 |
| --- | --- | --- |
| `MAIN_POSTGRES_PASSWORD` | 主数据库密码 | 替换 |
| `MAIN_BACKEND_JWT_SECRET_KEY` | 主后端 JWT 密钥 | 替换 |
| `KNOWLEDGE_GRAPH_POSTGRES_PASSWORD` | KG 数据库密码 | 替换 |
| `KNOWLEDGE_GRAPH_JWT_SECRET_KEY` | KG JWT 密钥 | 替换 |
| `KNOWLEDGE_GRAPH_SERVICE_PASSWORD` | 主后端访问 KG 的凭据 | 替换 |
| `MATCHING_POSTGRES_PASSWORD` | Matching 数据库密码 | 替换 |
| `MATCHING_SERVICE_SIGNING_KEY` | Matching 签名密钥 | 替换 |
| `MATCHING_UPSTREAM_SERVICE_TOKEN` | Matching 上游服务 Token | 替换 |
| `EMERGING_DISCOVERY_INTERNAL_TOKEN` | Emerging 内部 Token | 替换 |
| `TREND_INTELLIGENCE_INTERNAL_TOKEN` | Trend 内部 Token | 替换 |
| `ALLOW_DEMO_ADMIN_REGISTRATION` | 演示管理员自助注册 | 正式环境设为 `false` |
| `LOAD_PACKAGED_JUDGE_DATA` | 冷启动导入仓库内示例数据 | 生产环境设为 `false` |
| `JOBPULSE_IMAGE_TAG` | 第一方镜像标签 | 正式发布使用完整 Git SHA |

### 3.2 模型和可选能力变量

| 能力 | 必需配置 |
| --- | --- |
| JD/CV LLM 抽取 | 非空 `DEEPSEEK_API_KEY`；CV 另需 `CV_EXTRACTION_ENABLED=true` |
| 采集 | `ACQUISITION_ENABLED=true`、内部 Token、站点凭据与合法授权 |
| Evidence RAG | 非空 `DEEPSEEK_API_KEY`、BGE-M3 模型缓存 |
| Responsibility CE | 固定 revision 模型目录、`manifest.json`、CE 构建开关和语义服务 |
| Embedding | BGE-M3；基础栈常驻 |
| 语义匹配 | Qdrant、`semantic-demo` Profile；复用基础 Embedding 服务 |

当前 `.env.example` 默认开启 CV、采集和 Responsibility CE，但
`DEEPSEEK_API_KEY` 为空。首次部署必须选择下列方案之一：

- 配齐模型和凭据；或
- 按“最小基础栈”关闭这些能力。

## 4. 最小基础栈

适用于没有 DeepSeek 或 Responsibility CE 制品的环境；基础栈仍需 BGE-M3。在
`infra/.env` 中设置：

```dotenv
DEEPSEEK_API_KEY=
CV_EXTRACTION_ENABLED=false
ACQUISITION_ENABLED=false
MATCHING_RESPONSIBILITY_CE_MODE=disabled
MATCHING_INSTALL_RESPONSIBILITY_CE=false
ALLOW_DEMO_ADMIN_REGISTRATION=false
LOAD_PACKAGED_JUDGE_DATA=false
```

校验并启动：

```powershell
docker compose `
  --project-directory .\infra `
  --env-file .\infra\.env `
  -f .\infra\compose\docker-compose.candidate.yml `
  config --quiet

docker compose `
  --project-directory .\infra `
  --env-file .\infra\.env `
  -f .\infra\compose\docker-compose.candidate.yml `
  up --build --detach --wait
```

不要省略 `--project-directory .\infra`。否则 Compose 会把构建上下文解析到错误的
`infra/apps`、`infra/services` 路径。

## 5. Windows 标准启动

脚本会创建或补全被忽略的 `infra/.env`、生成本地密钥、校验 Docker、构建当前源码、
检查 BGE-M3 缓存并启动选中的 Profile。

```powershell
# 标准启动
.\scripts\compose-up.ps1

# 强制构建所选镜像
.\scripts\compose-up.ps1 -Build

# Evidence RAG
.\scripts\compose-up.ps1 -Evidence

# 完整拓扑
.\scripts\compose-up.ps1 -Full

# 离线启动；缺镜像或模型时直接失败，不联网下载
.\scripts\compose-up.ps1 -Offline
```

双击入口：

```text
start-jobpulse.cmd
start-jobpulse.cmd /full
start-jobpulse.cmd /offline
start-jobpulse.cmd /offline /full
```

当前 JobPulse 标准脚本会检查正式发现所需的 BGE-M3 缓存；缓存不存在且允许联网时，
会在启动前下载固定 revision。直接基础 Compose 不执行这一预取逻辑。

## 6. Linux/macOS 与 Profile 启动

将 PowerShell 路径分隔符改为 `/`：

```bash
docker compose \
  --project-directory ./infra \
  --env-file ./infra/.env \
  -f ./infra/compose/docker-compose.candidate.yml \
  up --build --detach --wait
```

可选 Profile：

| Profile | 容器角色 |
| --- | --- |
| `model-extraction` | JD Extraction Provider、Extraction worker |
| `cv-extraction` | CV Extraction Provider、独立 CV worker |
| `semantic-demo` | Qdrant、语义 Matching API、vector worker；复用基础 Embedding 服务 |
| `evidence-rag` | Evidence 使用的 Qdrant；复用基础 Embedding 服务 |
| `crawler` | crawler MySQL、API、scheduler |
| `kg-init` | 一次性 KG 构建与严格就绪检查 |
| `full` | JD/CV、语义和 crawler 的组合 |

示例：

```bash
docker compose \
  --project-directory ./infra \
  --env-file ./infra/.env \
  -f ./infra/compose/docker-compose.candidate.yml \
  --profile semantic-demo \
  up --build --detach --wait
```

## 7. Full 部署

`-Full` 启动前必须满足：

1. `DEEPSEEK_API_KEY` 非空；
2. `CV_EXTRACTION_ENABLED=true`；
3. `ACQUISITION_ENABLED=true`；
4. BGE-M3 固定 revision 已缓存或允许联网下载；
5. Responsibility CE 启用时，固定 revision 模型目录和 manifest 完整；
6. crawler 站点凭据、授权和网络条件已准备；
7. 默认探针岗位已存在已发布 KG 图谱。

Full 启动不会自动修改或初始化真实图谱数据。先完成第 9 节的真实数据和 KG 初始化，再运行：

```powershell
.\scripts\compose-up.ps1 -Full
.\scripts\compose-status.ps1 -Full
```

## 8. 首次启动行为

基础启动会自动执行：

- 主后端 Alembic 迁移；
- bootstrap 账号创建；
- 技能和岗位目录同步；
- KG migrate/bootstrap；
- Matching migrate；
- Trend 数据库迁移；
- `LOAD_PACKAGED_JUDGE_DATA=true` 时幂等导入评审数据。

迁移和 bootstrap 容器显示 `Exited (0)` 是成功，不是故障。

评审数据初始化不会自动运行新兴岗位发现算法，也不会自动审核或发布候选岗位。

## 9. 真实数据与 KG 初始化

图谱版本位于 Docker 命名卷，不随 Git 分发。正确顺序是：

```text
导入真实 JD
  -> Extraction / Validation
  -> 人工审核并发布岗位画像事实
  -> 一次性构建、评审、发布 KG GraphVersion
  -> 严格业务就绪检查
```

离线 JD Bundle 入口详见[数据导入教程](../user-guide/data-import.md)。导入 Bundle 只生成
SourceJD/Version 和任务，不代表审核、发布或 KG 构建完成。

真实已发布事实进入 KG 数据库后执行一次：

```powershell
.\scripts\compose-init-kg.ps1

# 可调整并行度和单图超时
.\scripts\compose-init-kg.ps1 -Workers 4 -Timeout 300
```

该操作保留已有发布版本，只构建缺失图谱，不属于普通启动流程。

## 10. 状态和就绪检查

```powershell
# 容器状态 + 基础服务就绪；不要求已发布 KG
.\scripts\compose-status.ps1

# 导入真实数据后要求至少一个已发布岗位图谱
.\scripts\compose-status.ps1 -RequirePublishedKg

# Full 角色与已发布图谱均必须就绪
.\scripts\compose-status.ps1 -Full

# 只查看容器，不运行应用探针
.\scripts\compose-status.ps1 -SkipReadiness
```

基础状态与 KG 业务状态不同：

- KG `/health` 为 200：进程和数据库可用；
- KG `/readiness` 为 503 且 `published_profiles: 0`：服务健康，但尚无已发布图谱；
- 只有导入/发布真实事实并执行 KG 初始化后，才能要求 `-RequirePublishedKg` 通过。

宿主机入口：

| 服务 | 默认模板端口 | 地址 |
| --- | ---: | --- |
| 统一前端 | 53000 | `http://localhost:53000` |
| 主后端 | 58000 | `http://localhost:58000` |
| OpenAPI | 58000 | `http://localhost:58000/docs` |
| 主 PostgreSQL | 55433 | 仅运维访问 |
| Analytics PostgreSQL | 55434 | 仅运维访问 |
| 语义 Matching API | 8011 | 仅启用语义 Profile 时 |

KG、Emerging、Trend 和默认 Matching API 只在 Compose 网络内开放。

## 11. 停止、重启、局部重建和重置

```powershell
# 停止容器，保留数据卷
.\scripts\compose-down.ps1

# 再次启动并根据源码重建
.\scripts\compose-up.ps1

# 只重建一个目标，保留数据
.\scripts\compose-rebuild.ps1 frontend
.\scripts\compose-rebuild.ps1 main
.\scripts\compose-rebuild.ps1 knowledge-graph
.\scripts\compose-rebuild.ps1 matching
```

可用局部目标：`main-api`、`main`、`frontend`、`knowledge-graph`、`discovery`、`trend`、
`matching`、`jd-extraction`、`cv-extraction`。

彻底重置：

```powershell
.\scripts\compose-reset.ps1
# 或 reset-jobpulse.cmd
```

重置会删除当前 JobPulse Compose 项目所有 Profile 的数据库、上传、Redis、Qdrant、模型缓存、
crawler 和 checkpoint 卷，然后从空卷重建。该操作不可恢复。

## 12. 持久化数据和备份边界

命名卷包括：

| 卷 | 数据 |
| --- | --- |
| `main_postgres_data` | 用户、JD/CV、任务、审核和主业务数据 |
| `main_uploads` | 上传文件 |
| `knowledge_graph_postgres_data` | 图谱事实、构建和 GraphVersion |
| `matching_postgres_data` | Matching 任务与结果 |
| `matching_redis_data` | Matching 队列/缓存 |
| `analytics_postgres_data` | Emerging 与 Trend 的独立数据库 |
| `embedding_model_cache` | BGE-M3 缓存 |
| `matching_qdrant_data` | 语义向量索引 |
| `crawler_mysql_data` | crawler 数据库 |
| `cv_extraction_checkpoints` | CV 抽取 checkpoint |

仓库当前没有统一备份/恢复脚本。生产环境必须在升级或重置前分别备份 PostgreSQL/MySQL、
`main_uploads` 和必要的模型/向量数据，并进行恢复演练。不要把 Git、镜像或健康检查当成数据备份。

数据库恢复时必须保持各领域数据库隔离；不要把 Analytics、KG、Matching 数据合并到主库。

## 13. 升级流程

1. 记录当前 Git SHA 和镜像标签；
2. 备份数据库及上传数据；
3. 获取目标提交；
4. 运行 Compose 配置校验；
5. 使用 `compose-up.ps1` 或带 `--build --wait` 的 Compose 命令重建；
6. 确认一次性迁移容器 `Exited (0)`；
7. 运行基础/严格状态检查和业务 smoke；
8. 确认无误后再清理旧镜像。

普通升级不得运行 `compose-reset.ps1`。

## 14. 离线部署

### 14.1 完整同数据交付包（推荐）

需要让离线机器看到与导出机器一致的数据库、上传文件、Qdrant 索引、Redis 状态、
Crawler 数据和本地模型缓存时，在导出机器运行：

```powershell
.\scripts\export-offline-full.ps1
```

该命令会短暂停止 JobPulse 容器，生成跨数据卷一致的快照，然后恢复导出前正在运行的
服务。默认输出为 `dist/offline/full`，包括：

- `jobpulse-images-full.tar`：基础服务、全量微服务以及 PostgreSQL、Redis、Qdrant、MySQL；
- `jobpulse-data/`：10 个 Compose 数据卷、Crawler 的 3 个绑定目录及离线运行环境文件；
- `jobpulse-responsibility-ce.tar`：正式 Matching Responsibility CE 模型；
- `jobpulse-source.zip`：当前工作区源码（包括未提交但未被 Git 忽略的文件）；
- `SHA256SUMS.txt`：上述文件和每个数据归档的 SHA-256。

BGE 模型快照已经包含在 `jobpulse-candidate_embedding_model_cache` 数据卷中，完整包不会
再生成一份重复的 BGE 模型归档。

所有正式 FastAPI 服务的 `/docs` 与 `/redoc` 使用源码包和镜像内固定版本的 Swagger UI
与 ReDoc 资源，不依赖 jsDelivr、Google Fonts 或其他公网 CDN；`/openapi.json` 同样由
对应服务本地生成。

在一台没有现存 JobPulse 数据卷的新离线机器上，解压源码后执行：

```powershell
.\scripts\import-offline-images.ps1 <jobpulse-images-full.tar> -Full
.\scripts\import-offline-ce-model.ps1 <jobpulse-responsibility-ce.tar>
.\scripts\import-offline-data.ps1 <jobpulse-data目录>
.\start-jobpulse.cmd /offline /full
```

`import-offline-data.ps1` 会先验证全部数据归档的 SHA-256，并拒绝覆盖已有的同名数据卷
或已有的 `infra/.env`，因此必须使用干净目标环境。恢复标记存在时，`/offline /full`
不会重新导入演示数据或重建镜像，而是直接用快照启动全部 Profile。

完整包中的 `offline-runtime.env` 会原样保留 `infra/.env` 中显式配置的临时
`DEEPSEEK_API_KEY`，以便目标机联网后直接调用 JD/CV 抽取和 Evidence RAG。导出前应将
临时密钥写入 `infra/.env`；不要把密钥提交到 Git。数据包包含外部 API 密钥、数据库口令、
业务数据和可能存在的 Crawler Cookie，应按敏感交付物管理，不要上传到公开制品库。

### 14.2 仅镜像和 BGE 缓存

不需要复制本地数据库，只需要准备一个干净的基础离线环境时，在联网机器导出：

```powershell
.\scripts\export-offline-images.ps1
.\scripts\export-offline-models.ps1
```

需要 JD 模型镜像时：

```powershell
.\scripts\export-offline-images.ps1 -IncludeModelExtraction
```

将包、代码和经过安全处理的 `infra/.env` 复制到离线机器后：

```powershell
.\scripts\import-offline-images.ps1 <镜像包路径>
.\scripts\import-offline-models.ps1 <模型包路径>
.\start-jobpulse.cmd /offline
```

离线模式不会补下载缺失镜像或模型；缺少固定 revision 时会在启动前失败。

## 15. 部署验证

最低验证：

```powershell
docker compose `
  --project-directory .\infra `
  --env-file .\infra\.env `
  -f .\infra\compose\docker-compose.candidate.yml `
  config --quiet

.\scripts\compose-status.ps1
python -m pytest tests/test_compose_production_parity.py -q -o "addopts="
```

真实异步链验证需要对应 Profile 和一个已存在的 `personal_user` 账号：

```powershell
$env:ASYNC_E2E_CV_USERNAME = "<personal-user>"
$env:ASYNC_E2E_CV_PASSWORD = "<password>"
python .\apps\api\scripts\async_e2e_smoke.py
```

最终状态写入 `reports/async-e2e-latest.json`。容器 healthy 或 HTTP 200 不能替代任务最终
`succeeded`、审核记录、发布版本和下游资源检查。

## 16. 正式发布物

正式镜像使用完整 Git SHA：

```dotenv
JOBPULSE_IMAGE_TAG=<full-git-sha>
```

构建后生成 SBOM 和 release manifest：

```bash
python scripts/build_release_manifest.py \
  --root . \
  --sbom artifacts/release/sbom.spdx.json \
  --model-manifest models/responsibility-ce-v1/manifest.json \
  --output artifacts/release/release-manifest.json \
  --require-complete
```

`--require-complete` 会拒绝 `candidate` 标签、本地-only digest、缺失 SBOM 或缺失模型 manifest。

## 17. 常见故障

### `GetFileAttributesEx ...\infra\apps` 或找不到构建上下文

原因：从仓库根目录运行 Compose 时遗漏 `--project-directory .\infra`。

处理：使用本手册完整命令或仓库 PowerShell 脚本。

### Full 提示缺少 DeepSeek/CV/Acquisition

确认 `infra/.env` 中只有一个有效赋值，并满足：

```dotenv
DEEPSEEK_API_KEY=<有效密钥>
CV_EXTRACTION_ENABLED=true
ACQUISITION_ENABLED=true
```

### KG 容器 healthy，但严格检查返回 503

若响应包含 `published_profiles: 0`，说明真实图谱尚未发布。完成真实 JD 导入、审核、事实发布，
再运行 `compose-init-kg.ps1`。不要为了通过健康检查伪造或写入演示图谱。

### BGE-M3 下载慢或离线失败

首次联网启动允许脚本填充 Compose 模型缓存；离线机器先导入与
`config/semantic-demo-contract.env` 固定 revision 一致的模型包。

若日志显示配置的 `HF_ENDPOINT` 返回 308 并继续访问 `huggingface.co`，而目标机器无法与
官方域名完成 TLS 握手，可为单次启动临时指定一个能够提供固定 revision 的可信镜像：

```powershell
$env:HF_ENDPOINT = "<可信的 Hugging Face 兼容镜像>"
.\scripts\compose-up.ps1
Remove-Item Env:HF_ENDPOINT
```

临时环境变量优先于 `infra/.env`，不会改写本机配置。生产交付优先使用已审核的内部镜像或
第 14 节的离线模型包；使用第三方镜像时必须核对 resolved commit，并校验模型制品哈希，
不能仅以“下载成功”作为供应链验证。

### Debian APT 构建失败或 502

使用 `docker build --progress=plain` 查看真实失败层，检查 `DEBIAN_MIRROR_URL`、代理和网络；
仓库 Dockerfile 已使用 HTTPS 与 APT 重试，不要通过关闭签名校验绕过错误。

### 修改源码后页面没有变化

前端是静态 Nginx 镜像，需要重新构建并替换容器：

```powershell
.\scripts\compose-rebuild.ps1 frontend
```

### 一次性迁移容器显示 Exited

`Exited (0)` 正常；非零退出码才需要读取对应容器日志。

## 18. 安全红线

- 不提交 `infra/.env`、模型密钥、站点 Cookie 或账号密码；
- 不公开启用 `ALLOW_DEMO_ADMIN_REGISTRATION`；
- 不让 crawler 直接写主数据库，只交换校验后的离线 Bundle；
- 不在普通启动中自动写入或重建真实 KG；
- 不用 `down -v` 或 `compose-reset.ps1` 代替普通升级；
- 不以健康检查代替真实异步链和业务数据生命周期验证；
- 不使用 `candidate` 镜像作为正式发布证据。
