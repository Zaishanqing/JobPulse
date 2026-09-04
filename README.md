# JobPulse

JobPulse 是一套面向多源异构招聘数据治理、动态职业感知与可验证职业行动决策的系统：从多源 JD / CV 结构化抽取，到岗位知识图谱、新兴岗位发现、岗位动态演化与趋势分析，再到个人/企业的人岗匹配、Gap、What-if 反事实推演和 Learning Path，全链路都建立在 Evidence 可追溯与
人工审核闭环之上。

## 核心能力

- **多源 JD 数据治理**：跨来源采集契约、结构化抽取与归一化、发布门禁。
- **岗位能力图谱**：技能目录、岗位画像（position profile）、GraphVersion 版本化、知识图谱构建与发布。
- **新兴岗位发现**：多视角候选发现、候选生命周期、正式识别判定与治理门禁。
- **岗位动态演化**：连续 GraphVersion 上的演化事件检测、覆盖波动抑制与 Evidence 血缘。
- **趋势分析**：多窗口趋势状态与 ChangePoint 检测、absolute-support 门禁。
- **人岗匹配**：Skill / Hard Condition / Responsibility 三模块匹配、Gap、What-if、Learning Path。
- **Evidence 闭环**：Evidence 独立性与有效样本量、Evidence RAG、价值排序人工审核（HITL）。
- **企业招聘工作台**：企业岗位、候选人看板、正式 Evaluation/Gap 与 RBAC 权限体系。

## 核心功能

- JD / CV 结构化抽取（rule / LLM 双模式，抽取结果必须经过人工审核才能发布）
- 技能归一化（unresolved 自动接受 + 人工复核闭环）
- 岗位知识图谱（GraphVersion 不可变版本、发布前评审）
- 新兴岗位发现与候选生命周期治理
- 岗位演化事件、趋势状态与 ChangePoint
- 个人 / 企业人岗匹配、Gap、What-if、Learning Path
- Evidence Court / Evidence Assistant（证据查询、受约束问答）
- 人工审核工作台与价值排序
- RBAC（普通用户 / 求职者 / 评审 / 管理员 / 开发者）

## 能力指标

| 模块 | 指标 |
|---|---|
| JD 解析准确率 | Exact Span F1 `99.67%` |
| CV 提取准确率 | Field F1 `99.45%` |
| Matching 匹配准确率 | 最终结论级准确率 `98.00%` |
| 单元测试覆盖率 | `≥ 60%` |

## 系统架构

```text
apps/web          React 前端（统一门户 + 企业招聘工作台）
apps/api          主后端 / BFF（FastAPI）：账号、JD/CV、招聘、治理与跨服务编排
services/knowledge-graph      岗位知识图谱：构建、评审、发布、GraphVersion
services/emerging-discovery   新兴岗位发现：多视角聚类、候选生命周期、正式识别判定
services/trend-intelligence   趋势分析：多窗口状态、ChangePoint、报告
services/matching-service     人岗匹配：Skill / Hard / Responsibility、Gap、What-if、Learning Path
services/embedding-service    BGE-M3 向量服务（语义 profile / 可选）
services/jd-extraction        JD 结构化抽取服务（显式 profile）
services/cv-extraction        CV 结构化抽取服务（显式 profile）
services/crawler              多源采集（可选）
packages/contracts            跨运行时共享 DTO 与契约
infra/compose                 Docker Compose 生产级拓扑
```

详细架构见 [docs/architecture](docs/architecture/index.md)，接口目录见
[apps/api/docs/api-overview.md](apps/api/docs/api-overview.md)。

## 技术栈

- 后端：Python 3.11、FastAPI、SQLAlchemy、Alembic、Pydantic
- 前端：React、TypeScript、Vite、Ant Design
- 数据：PostgreSQL（多实例）、Redis、Qdrant（语义 profile）
- 向量与模型：BGE-M3（embedding-service，可选 profile）、mMARCO Cross-Encoder（Responsibility CE）
- 部署：Docker Compose（生产级多服务拓扑）
- 测试：pytest（Python）、Vitest / Testing Library（前端）、GitHub Actions

## 目录结构

```text
apps/            主后端与前端
services/        独立能力服务（KG / Emerging / Trend / Matching / Embedding / JD / CV / Crawler）
packages/        共享契约
infra/           Compose 拓扑、环境模板
scripts/         测试、Compose、覆盖率和部署工具
tests/           仓库级架构、契约与跨服务测试
docs/            架构、部署、用户、API、测试与技术说明
config/          运行配置（含 semantic-demo-contract.env）
models/          Responsibility CE 模型产物
```

## 环境要求

- Docker Engine ≥ 24 与 Docker Compose v2（Compose 版本 ≥ 2.20.3）
- 可选：Python 3.11（本地开发）、Node.js ≥ 20（前端开发）
- 可选：GPU（仅语义 profile 与 CE 完整推理需要；基础模式可在 CPU 运行）

## 配置方法

1. 复制环境模板：

   ```powershell
   Copy-Item infra\.env.example infra\.env
   ```

2. 按需修改 `infra/.env`。所有变量都有默认值；必须提供的密钥类变量
   （PostgreSQL 密码、JWT Secret、服务间 Token）在模板中已给出开发默认值，
   正式部署请替换为强随机值。完整变量说明见
   [docs/deployment/deployment.md](docs/deployment/deployment.md)。
   `ALLOW_DEMO_ADMIN_REGISTRATION=true` 仅用于演示环境的管理员自助注册；
   常规及正式部署必须设为 `false`。该开关不允许公开注册 `reviewer` 或 `developer`。

## 一键启动（Windows）

```powershell
.\start-jobpulse.cmd
```

脚本会构建镜像、启动默认拓扑并打开前端页面。

完整统一拓扑（JD/CV、语义和招聘采集一并启动）：

```powershell
.\start-jobpulse.cmd /full
```

清理并重建数据卷：

```powershell
.\reset-jobpulse.cmd
```

默认启动会执行一次幂等的示例数据初始化，便于空数据卷直接体验；常规生产部署可设置
`LOAD_PACKAGED_JUDGE_DATA=false` 禁用。

## Docker Compose 部署（Linux / macOS）

默认拓扑（基础模式，不包含 JD 模型抽取、CV 抽取与语义链）：

```bash
cd infra
cp .env.example .env
docker compose -f compose/docker-compose.candidate.yml \
  --env-file .env --project-directory . up -d --build
```

完整演示拓扑（显式启用可选 profile）：

```bash
docker compose -f compose/docker-compose.candidate.yml \
  --env-file .env --project-directory . \
  --profile model-extraction --profile cv-extraction --profile semantic-demo \
  up -d --build
```

完整统一拓扑（JD/CV、语义和招聘采集一并启动）：

```bash
docker compose -f compose/docker-compose.candidate.yml \
  --env-file .env --project-directory . --profile full \
  up -d --build
```

完整模式需要 `DEEPSEEK_API_KEY`、`CV_EXTRACTION_ENABLED=true`、`ACQUISITION_ENABLED=true`、BGE-M3 模型缓存，以及爬虫所需的站点凭据和授权。爬虫仍通过校验后的离线 JD Bundle 与主后端交换数据，不会直接写入主后端数据库。

只启用证据问答所需的模型、Embedding 与 Qdrant（Windows）：

```powershell
.\scripts\compose-up.ps1 -Evidence
```

该模式要求 `infra/.env` 已配置 `DEEPSEEK_API_KEY`。它会启用 Evidence RAG，启动
Embedding 与 Qdrant，并在后台幂等补建已有已发布图谱的检索索引；不会额外启动
JD/CV 模型抽取和 crawler。已有 BGE-M3 Compose 缓存会直接复用，离线演示可增加
`-Offline`，缓存缺失时脚本会在启动前停止并给出导入提示。

岗位图谱版本保存在 Docker 命名卷中，不随 Git 分发。首次导入真实已发布 JD 数据后，Windows
请执行一次 `scripts\compose-init-kg.ps1`；随后 `scripts\compose-up.ps1 -Full` 会在启动前检查
默认的 `BACKEND_ENGINEER` 图谱，缺失时停止并打印初始化提示。

服务就绪后访问：

- 主前端：`http://localhost:53000`（或 `.env` 中 `MAIN_FRONTEND_PORT`）
- 主后端：`http://localhost:58000`（或 `MAIN_BACKEND_PORT`）
- 后端 OpenAPI：`http://localhost:58000/docs`

## 可选模型 / CV / 语义 profile

- `model-extraction`：JD 抽取服务（`jd-extraction`）。
- `cv-extraction`：CV 抽取服务及其 worker（`cv-extraction`、`cv-extraction-worker`）。
- `semantic-demo`：BGE-M3 embedding-service、Qdrant、语义匹配 API 与向量 worker。
  该 profile 需要下载 BGE-M3 模型，基础模式默认不启用，避免强制拉取大模型。
- `full`：同时启用 JD/CV Extraction、语义链和 crawler profile，供完整容器拓扑启动。
- Responsibility CE：由 `MATCHING_RESPONSIBILITY_CE_MODE` 控制，默认 `disabled`
  （规则模式仍可完成匹配）；启用时需要模型产物与 embedding 服务。

## 演示账号与角色

首次启动会通过 `seed_bootstrap_accounts.py` 创建引导管理员。角色包括：

- `admin`：系统管理、治理与发布
- `reviewer`：抽取审核、图谱评审、企业看板
- `developer`：工程与数据操作
- 普通用户：简历、个人匹配、Gap、What-if 与 Learning Path

演示账号与初始密码见启动日志或部署文档。

## 推荐演示路径

1. **JD 数据治理**：导入 JD → 解析（rule/LLM）→ 审核 → 发布岗位画像。
2. **个人职业决策**：上传/确认 CV → 匹配 → Gap → What-if 最小行动 → Learning Path。
3. **企业招聘工作台**：企业岗位与候选人看板 → 正式 Evaluation/Gap → 招聘决策。
4. **Evidence 闭环**：Evidence 查询与 RAG 问答 → 审核任务价值排序 → Insight Card。
5. **岗位动态**：图谱构建与发布 → 演化事件 → 新兴岗位候选生命周期 → 趋势报告。

## 测试

```bash
# 仓库级架构、契约与跨服务测试
bash tests/run-ci.sh

# 单模块测试（例如主后端）
bash scripts/test-all.sh Main

# 前端
cd apps/web && npm test && npm run typecheck && npm run build
```

## Coverage

统一覆盖率入口：

```bash
python scripts/coverage-all.py
```

该入口依次运行 9 个模块（Main、Knowledge Graph、Matching、Emerging、Trend、
Crawler、JD Extraction、CV Extraction、Embedding）的正式测试，并输出
`artifacts/coverage/summary.md`（含 commit SHA、9/9 执行状态、Overall Line
Coverage 与 PASS/FAIL 判定）。项目要求总单元测试覆盖率 ≥ 60%。

## 常见问题

- **端口冲突**：修改 `infra/.env` 中 `MAIN_BACKEND_PORT` / `MAIN_FRONTEND_PORT`。
- **语义链不可用**：确认启用 `semantic-demo` profile，并保持
  `config/semantic-demo-contract.env` 与 `.env` 一致。
- **模型下载慢**：`embedding-service` 首次启动需下载 BGE-M3；基础模式不依赖它。
- **数据重置**：`.\reset-jobpulse.cmd`（Windows）或删除 Compose volumes 后重建。

更多文档入口：[docs/README.md](docs/README.md)。
