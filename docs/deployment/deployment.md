# JobPulse 部署拓扑与运行边界

面向安装、配置、数据初始化、升级、离线交付和故障排查的端到端操作说明见
[完整部署手册](complete-guide.md)。本文聚焦 Compose 拓扑和生产运行边界。

仓库提供一个 Compose 入口：`infra/compose/docker-compose.candidate.yml`。

## 拓扑

- 主后端与统一前端；
- Knowledge Graph PostgreSQL、迁移、bootstrap、API 和 build worker；
- Matching PostgreSQL、Redis、迁移、API、task worker 和 outbox dispatcher；
- Analytics PostgreSQL，分别承载 Emerging 与 Trend 的独立数据库；
- Emerging Discovery API；Trend Intelligence API 与 worker；
- 主后端 Extraction、Validation 与 KG Outbox worker；
- 基础常驻 Embedding；可选 Qdrant + Matching vector worker、JD Extraction、
  CV Extraction + 独立 CV worker profile；
- crawler MySQL、API 与 scheduler（`crawler` profile）。

`full` profile 是上述可选 profile 的组合入口；它只统一启动容器，不改变语义链的
shadow 语义，也不把 crawler 的离线 Bundle 交换改成跨数据库直写。

每个服务只能访问自己的业务数据库。`analytics-postgres` 是共享 PostgreSQL 实例，不是共享
schema：Emerging 和 Trend 使用不同数据库与凭据。

## 启动

从仓库根目录执行：

```powershell
Copy-Item .\infra\.env.example .\infra\.env
docker compose --project-directory .\infra --env-file .\infra\.env -f .\infra\compose\docker-compose.candidate.yml config --quiet
docker compose --project-directory .\infra --env-file .\infra\.env -f .\infra\compose\docker-compose.candidate.yml up --build --detach --wait
docker compose --project-directory .\infra --env-file .\infra\.env -f .\infra\compose\docker-compose.candidate.yml ps
```

主后端启动命令会执行主 Alembic、创建 bootstrap 账号并同步技能与岗位目录。KG 由独立
migrate/bootstrap job 初始化；Matching 由 migrate job 初始化。Trend 镜像入口负责其数据库
迁移。一次性 job 成功后显示 `Exited (0)` 是正常状态。

## 生产一致性分母

`infra/compose/production-parity.yaml` 固定默认服务与各 profile 的必需角色，
`tests/test_compose_production_parity.py` 校验服务存在性、worker 独立命令、启用开关和
下游依赖，并拒绝 Compose profile 与 parity 清单漂移。默认分母覆盖不依赖外部模型凭据的
应用在线异步链；需要模型或站点凭据的链仍由同一个 Compose 文件通过 profile 启用，数据库
仍保持按领域隔离，不因统一编排而合并 schema。

启用 CV 全链时，在 `infra/.env` 设置 `CV_EXTRACTION_ENABLED=true` 并启用
`cv-extraction` profile。启用语义链时，启用 `semantic-demo` profile，并将需要
语义结果的调用方指向 `matching-api-semantic-demo:8000`。

启用完整 JD 异步链时，必须同时启用 `model-extraction` profile；该 profile 会一起启动
`jd-extraction` 和 `extraction-worker`。启用招聘数据采集时使用 `crawler` profile：

```bash
docker compose --env-file ./infra/.env \
  --project-directory ./infra \
  -f ./infra/compose/docker-compose.candidate.yml \
  --profile model-extraction --profile crawler up --build --detach --wait
```

需要一次启动全部服务时，先在 `infra/.env` 设置 `DEEPSEEK_API_KEY`、
`CV_EXTRACTION_ENABLED=true` 和 `ACQUISITION_ENABLED=true`，再执行：

```powershell
docker compose --env-file .\infra\.env `
  --project-directory .\infra `
  -f .\infra\compose\docker-compose.candidate.yml `
  --profile full up --build --detach --wait
```

提交前的 parity 测试会枚举 Compose 文件中的所有 profile，确保每个 profile 都在角色分母
中声明，且默认服务不会意外带 profile；这使“一个 Compose 的生产角色覆盖”成为可自动验证
的结构契约，而不是文档约定。

Responsibility CE 默认关闭。只有在已提供并校验固定 revision 模型目录后，才可设置
`MATCHING_RESPONSIBILITY_CE_MODE=enabled`，并用
`MATCHING_INSTALL_RESPONSIBILITY_CE=true` 构建包含 `torch`/`transformers` 的
Matching 镜像；同时设置 `MATCHING_RESPONSIBILITY_CE_MODEL_PATH` 和
`MATCHING_RESPONSIBILITY_CE_EMBEDDING_URL`。生产模式禁止规则静默回退，模型未加载时
API 必须启动失败。模型目录必须包含由仓库脚本生成的 `manifest.json`：

```bash
PYTHONPATH=services/matching-service python scripts/build_responsibility_ce_manifest.py \
  models/responsibility-ce-v1 --model-id responsibility-ce-v1 --model-revision <revision>
```

首次生成模型制品时使用训练脚本；它要求显式输出目录和不可变 revision，默认只读取本地
base model 缓存，避免构建过程隐式下载模型：

```bash
python services/matching-service/evaluation/final_matching_validation_v1/train_deploy_ce.py \
  --output models/responsibility-ce-v1 \
  --model-revision <training-run-or-release-revision> \
  --base-model <local-base-model-path> \
  --base-model-revision <base-model-commit>
```

如果发布环境明确允许联网准备 base model，才额外传入 `--allow-network`。训练完成后脚本
会同时写出 `train_meta.json` 和 `manifest.json`；发布前必须保存 manifest 中的
`artifact_sha256`，并在容器内通过 `/health/ready` 的 `responsibility_ce.artifact_digest`
和 smoke verifier 复核同一个 digest。

通过 `MATCHING_RESPONSIBILITY_CE_MODEL_HOST_PATH` 将该目录只读挂载到容器；生产启动会
重新计算每个文件的 SHA-256，并把 `artifact_sha256` 暴露在 `/health/ready` 的
`responsibility_ce` 组件中。digest 不匹配、manifest 缺失或模型加载失败都会使 readiness
失败（生产模式下启动也会 fail-closed）。

`docker compose ... down` 默认保留卷；除非明确接受删除该环境全部数据，否则不要加 `-v`。
真实密钥只写入被忽略的 `infra/.env`，不得提交。

基础部署只保证服务与目录数据在线，不要求命名卷中已有已发布岗位图谱。Windows 的
`.\scripts\compose-status.ps1` 默认检查这一基础状态；导入真实已发布 JD facts 并运行
`.\scripts\compose-init-kg.ps1` 后，使用 `.\scripts\compose-status.ps1 -RequirePublishedKg`
检查岗位图谱业务就绪。`-Full` 状态检查会自动要求已发布图谱。

彻底重置本项目数据时使用 `.\scripts\compose-reset.ps1`。该脚本会删除当前 Compose
项目所有 Profile 的命名卷（包括未运行的模型缓存、Qdrant、crawler 和 CV checkpoint），
然后从空卷重建；这些数据不可恢复。

## 异步链路验证

`apps/api/scripts/async_e2e_smoke.py` 会创建并轮询 JD 提取、CV 提取与确认、JD
发布、Knowledge Graph 同步/构建、Matching 和 Trend 任务；任何任务失败、超时或
缺少最终 `succeeded` 状态都会返回非零。运行前必须启用对应 worker/profile，并提供
一个 `personal_user` 的 CV 账号（不会自动注册账号）：

```bash
set ASYNC_E2E_CV_USERNAME=<personal-user>
set ASYNC_E2E_CV_PASSWORD=<password>
python apps/api/scripts/async_e2e_smoke.py
```

脚本将每一步的任务 ID、最终状态和发布/图谱响应写入
`reports/async-e2e-latest.json`，该文件记录异步链最终状态，不得用健康检查结果替代。

## 发布物固定

发布镜像必须使用 Git SHA 标签（`JOBPULSE_IMAGE_TAG=<full-sha>`），并在构建完成后
生成 release manifest；manifest 会记录 Compose 配置 hash、各镜像本地 ID/远端 digest、
迁移目录 head、CE 模型 artifact digest 和 SBOM hash：

```bash
python scripts/build_release_manifest.py \
  --root . \
  --sbom artifacts/release/sbom.spdx.json \
  --model-manifest models/responsibility-ce-v1/manifest.json \
  --output artifacts/release/release-manifest.json \
  --require-complete
```

`--require-complete` 会拒绝仍使用 `candidate`/本地-only 镜像 digest、缺 SBOM 或缺
模型 manifest 的发布物。
