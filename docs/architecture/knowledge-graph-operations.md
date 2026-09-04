# 知识图谱集成运维说明

## 配置

复制 `infra/.env.example` 为 `infra/.env` 后至少替换：

```text
KNOWLEDGE_GRAPH_ENABLED=true
KNOWLEDGE_GRAPH_BASE_URL=http://knowledge-graph-backend:8000
KNOWLEDGE_GRAPH_TIMEOUT_SECONDS=20
KNOWLEDGE_GRAPH_SERVICE_USERNAME=integration_developer
KNOWLEDGE_GRAPH_SERVICE_PASSWORD=<unique secret>
KNOWLEDGE_GRAPH_JWT_SECRET_KEY=<at least 32 random characters>
CATALOG_WRITES_ENABLED=false
```

真实密码不得提交。Compose 不提供密码回退值；空值、`change-me`、开发值、占位符、历史公开密码、少于 16 字符的值以及旧密码字段都会使 production 拒绝启动。

## 启动与检查

```bash
docker compose --env-file infra/.env -f infra/compose/docker-compose.candidate.yml config
docker compose --env-file infra/.env -f infra/compose/docker-compose.candidate.yml build
docker compose --env-file infra/.env -f infra/compose/docker-compose.candidate.yml up -d --wait
docker compose --env-file infra/.env -f infra/compose/docker-compose.candidate.yml ps
```

Compose 文件未覆盖时的宿主端口回退值为主后端 `8000`、唯一前端 `3000`、
`main-postgres` `5433` 和 `analytics-postgres` `5434`；仓库提供的 `infra/.env.example`
分别覆盖为 `58000`、`53000`、`55433`、`55434`。KG 与发现服务仅 `expose: 8000`，只能从 Compose 网络访问。
容器内主后端使用 `http://knowledge-graph-backend:8000` 和
`http://emerging-discovery:8000`。主后端等待两个内部服务 readiness 后启动。

检查：

```bash
curl http://127.0.0.1:58000/readiness
curl -I http://127.0.0.1:53000/
```

## 数据持久化与数据库边界

`main_postgres_data` 和 `knowledge_graph_postgres_data` 分别保存主系统与 KG 的 PostgreSQL 数据，
`analytics_postgres_data` 保存新兴岗位与趋势服务的 PostgreSQL，`main_uploads` 独立保存上传文件。
`docker compose down` 保留数据；只有明确需要清空全部本地数据时使用
`docker compose down -v`。各后端分别运行自己的 Alembic，不共享 migration history 或业务表。

## 集成验证与停服恢复

```bash
python apps/api/scripts/verify_knowledge_graph_integration.py --exercise-outage
```

脚本通过公开 API 创建非 Seed JD、确认双 V2、显式映射、同步/幂等/变更、构建、审核发布、
读取图谱/Evidence/版本、验证 trace，然后停止 KG 验证主 503，再启动并验证持久数据恢复。
任一步失败均非零退出，不固定打印 PASS。

手工故障操作：

```bash
docker compose --env-file infra/.env -f infra/compose/docker-compose.candidate.yml stop knowledge-graph-backend
docker compose --env-file infra/.env -f infra/compose/docker-compose.candidate.yml start knowledge-graph-backend
```

失败同步可直接重试原主同步接口；根据 mapping 的 `sync_status` 从安全阶段继续，文档 ID
不会重复创建。

## 密钥轮换

更新 `.env` 中服务密码后重启 KG（seed 会幂等更新服务账号密码）与主后端。更新 JWT secret
会使旧 KG Token 失效，主客户端收到 401 后会自动重新登录一次。

## 已知边界

- 主系统与 KG 的空 PostgreSQL 16 数据库必须能从首个 Alembic revision 升级到 head；pytest 仍以隔离 SQLite fixture 运行快速单元测试。
- KG 服务自身使用 PostgreSQL 持久化，不接入 Neo4j、向量库、LLM 或 OCR；这些能力即使由其他服务提供，也不改变 KG 的数据库所有权。
- 根目录白色 Ant Design 前端是唯一网站入口，知识图谱独立账号与独立前端只属于服务单独开发历史，不参与统一部署。
- 映射歧义必须由 admin 通过主后端映射接口解决。
- KG 独立开发 Compose 可显式设置 `CATALOG_WRITES_ENABLED=true`；统一 production 配置为 false，防止标准技能双主写入。
- 当前 Compose 默认编排主后端 KG Outbox worker，并在主 API、KG 与 Matching readiness 后启动。
